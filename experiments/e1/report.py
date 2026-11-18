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
    passes = "inconclusive" if pc[0] != 20 else str(pc[1] == 0 and pc[2] >= 19).lower()
    no_adv = "inconclusive" if bc[0] != 20 else str(bc[3] >= 19).lower()
    out += ["", f"Purser meets the absolute E1 threshold: **{passes}**. "
            f"The original baseline meets the no-advantage threshold: **{no_adv}**.", "",
            "| Seed | Loop steps | Purser steps | Equal-verifier steps | Purser − loop | Purser − equal-verifier |",
            "| ---: | ---: | ---: | ---: | ---: | ---: |"]
    def cost(track, seed, arm):
        item = seen.get((track, seed, arm))
        return item[0]["cost"]["steps"] if item else None
    def diff(x, y):
        return x-y if x is not None and y is not None else "unavailable"
    for seed in seeds:
        a,b,c = (cost("primary", seed, arm) for arm in ARMS)
        out.append(f"| {seed} | {a} | {b} | {c} | {diff(b,a)} | {diff(b,c)} |")
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
    overhead = b > 2 * a if a is not None and b is not None else "inconclusive"
    out += ["", f"Purser exceeded twice the clean plain-loop step count: **{str(overhead).lower()}**.",
            "All model-call, token and spend counts are zero.", ""]
    return "\n".join(out)
