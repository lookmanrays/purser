"""Verify the retained run archive and recompute E1 judgments without running an arm."""

import json
import zipfile

from .artifacts import MANIFEST, read_artifact
from .scorer import score
from .suite import ARMS, schedule


def inspect_series(directory):
    results = {}
    with zipfile.ZipFile(directory / "raw.zip") as archive:
        freeze = json.loads(archive.read("freeze.json"))
        if freeze != json.loads((directory / "freeze.json").read_bytes()):
            raise ValueError("series registration mismatch")
        for track, seed, rank, arm in schedule():
            name = f"runs/{track}/{seed}/{arm}.json"
            if name not in archive.namelist():
                continue
            raw = json.loads(archive.read(name))
            if (raw["track"], raw["seed"], raw["arm"], raw["mode"], raw["replicate"]) != (track, seed, arm, "scripted", 1):
                raise ValueError("run identity mismatch")
            def load(ref):
                return json.loads(read_artifact(archive, ref))
            journal = load(raw["artifacts"]["journal"])
            candidates = load(raw["artifacts"]["candidates"])
            platform = load(raw["artifacts"]["platform"])
            for ref in platform["referenced_files"] + platform["definition_artifacts"] + [platform["environment_artifact"]]:
                read_artifact(archive, ref)
            for oid, entry in candidates.items():
                read_artifact(archive, entry["pack"])
            if [e["seq"] for e in journal] != list(range(1, len(journal) + 1)):
                raise ValueError("non-contiguous journal")
            actions = [r["payload"] for r in journal if r["kind"] == "action"]
            if len(actions) != raw["cost"]["steps"] or [a["attempt"] for a in actions] != list(range(1, len(actions) + 1)):
                raise ValueError("step total mismatch")
            if platform["end_monotonic_ns"] - platform["start_monotonic_ns"] != raw["cost"]["elapsed_ns"]:
                raise ValueError("elapsed time mismatch")
            for row in journal:
                p = row["payload"]
                if row["kind"] != "oracle":
                    continue
                report = load(p["evidence"])
                measured = "pass" if all(q["expected"] == q["observed"] for q in report["cases"]) else "fail"
                if (p["candidate"] not in candidates or report["candidate"] != p["candidate"]
                        or report["criterion"] != p["criterion"] or measured != p["verdict"]):
                    raise ValueError("protected observation disagrees with journal")
            observed = score(journal) if raw["validity"] != "infrastructure_invalid" else None
            if track == "primary" and observed != raw["score"]:
                raise ValueError("raw result disagrees with independently recomputed score")
            results[(track, seed, arm)] = (raw, observed)
    return results


def render(directory):
    seen = inspect_series(directory)
    seeds = MANIFEST["experiments"]["E1"]["seeds"]
    out = ["# Scripted E1 results", "", "The units are the preregistered paired notification tasks. "
           "Each arm used the same fixture, seed, local tool budget and worker policy.", "",
           "| Arm | Completed / 20 | False parent closures | Missing obligation named | Caught | Correct parent closure |",
           "| --- | ---: | ---: | ---: | ---: | ---: |"]
    counts = {}
    for arm in ARMS:
        rows = [seen.get(("primary", s, arm)) for s in seeds]
        samples = [r[1] for r in rows if r and r[0]["validity"] == "complete" and r[1] is not None]
        counts[arm] = (len(samples), *(sum(r[k] for r in samples) for k in
                         ["false_parent_closure","missing_named","caught","correct_parent_closure"]))
        out.append("| " + arm + " | " + " | ".join(map(str, counts[arm])) + " |")
    pc, bc = counts["purser"], counts["loop"]
    passes = "inconclusive" if pc[0] != 20 else ("met" if pc[1] == 0 and pc[2] >= 19 else "missed")
    no_adv = "inconclusive" if bc[0] != 20 else ("met" if bc[3] >= 19 else "missed")
    out += ["", f"The absolute E1 threshold for Purser was **{passes}**. "
            f"The original no-advantage threshold for the plain loop was **{no_adv}**.", "",
            "Each cell gives the outcome and number of steps. The last two columns show paired differences "
            "in steps and measured seconds.", "",
            "| Seed | Plain loop | Purser | Equal verifier | Purser − plain | Purser − equal verifier |",
            "| ---: | --- | --- | --- | ---: | ---: |"]
    def label(item):
        if item is None:
            return "missing"
        raw, score = item
        if raw["validity"] != "complete" or score is None:
            return raw["validity"] + " / " + str(raw["cost"]["steps"])
        if score["false_parent_closure"]:
            outcome = "false close"
        elif score["caught"]:
            outcome = "caught"
        else:
            outcome = "unnamed"
        return f"{outcome} / {raw['cost']['steps']}"
    def difference(a, b):
        if not a or not b:
            return "unavailable"
        x, y = a[0]["cost"], b[0]["cost"]
        return f"{x['steps'] - y['steps']:+d} / {(x['elapsed_ns'] - y['elapsed_ns']) / 1e9:+.3f} s"
    def cost(track, seed, arm):
        item = seen.get((track, seed, arm))
        return item[0]["cost"]["steps"] if item else None
    for seed in seeds:
        a, b, c = (seen.get(("primary", seed, arm)) for arm in ARMS)
        out.append(f"| {seed} | {label(a)} | {label(b)} | {label(c)} | {difference(b,a)} | {difference(b,c)} |")
    clean = MANIFEST["experiments"]["E1"]["clean_seed"]
    out += ["", "## Clean control", "", "| Arm | Terminal | Steps | Elapsed seconds | Correct close |",
            "| --- | --- | ---: | ---: | --- |"]
    for arm in ARMS:
        item = seen.get(("clean", clean, arm))
        if item:
            raw, result = item
            seconds = raw["cost"]["elapsed_ns"] / 1e9
            out.append(f"| {arm} | {raw['terminal']} | {raw['cost']['steps']} | {seconds:.3f} | "
                       f"{result['correct_parent_closure'] if result else 'unavailable'} |")
    a, b = cost("clean", clean, "loop"), cost("clean", clean, "purser")
    if a is None or b is None:
        overhead = "The clean step-overhead comparison is inconclusive."
    else:
        relation = "exceeded" if b > 2 * a else "was within"
        overhead = f"Purser used {b} clean steps and the plain loop used {a}. Purser {relation} the twice-baseline overhead limit."
    out += ["", overhead,
            "All model-call, token and spend counts are zero.", ""]
    return "\n".join(out)


def verify_candidates(directory):
    """Restore each self-contained pack and repeat the frozen protected behavior on its own bytes."""
    import tempfile
    from pathlib import Path
    from . import scorer
    from .artifacts import START, encoded, extract_tree, git

    seen = inspect_series(directory)
    with tempfile.TemporaryDirectory(prefix="purser-e1-report-") as tmp, zipfile.ZipFile(directory / "raw.zip") as archive:
        repos, measurements = {}, {}
        for track, seed, rank, arm in schedule():
            item = seen.get((track, seed, arm))
            if not item:
                continue
            raw, _ = item
            entries = json.loads(read_artifact(archive, raw["artifacts"]["candidates"]))
            for oid, entry in entries.items():
                digest = entry["pack"]["sha256"]
                if digest not in repos:
                    repo = Path(tmp) / digest
                    repo.mkdir()
                    git(repo, "init", "-q", "--bare", "--object-format=sha1")
                    git(repo, "index-pack", "--stdin", data=read_artifact(archive, entry["pack"]))
                    git(repo, "fsck", "--full", "--no-dangling", oid)
                    git(repo, "merge-base", "--is-ancestor", START, oid)
                    repos[digest] = repo
                repo = repos[digest]
                ancestry = git(repo, "rev-list", "--parents", "-n", "1", oid).decode().split()
                if ancestry != [oid] + ([entry["parent"]] if entry["parent"] else []):
                    raise ValueError("archived parent identity mismatch")
                if git(repo, "rev-parse", oid + "^{tree}").decode().strip() != entry["tree"]:
                    raise ValueError("archived tree identity mismatch")
            journal = json.loads(read_artifact(archive, raw["artifacts"]["journal"]))
            for event in journal:
                if event["kind"] != "oracle":
                    continue
                p = event["payload"]
                oid = p["candidate"]
                key = (seed, rank, oid)
                if key not in measurements:
                    repo = repos[entries[oid]["pack"]["sha256"]]
                    with extract_tree(repo, oid) as checkout:
                        measurements[key] = scorer.measure(repo, oid, Path(checkout), seed, rank)
                if encoded(measurements[key][p["criterion"]]) != read_artifact(archive, p["evidence"]):
                    raise ValueError("archived input produced a different protected report")
        return {"runs": len(seen), "independent_git_packs": len(repos), "reexecuted_seed_candidates": len(measurements)}
