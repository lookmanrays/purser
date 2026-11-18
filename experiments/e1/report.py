"""Validate the registered archive and report qualified comparisons plus directly observed outcomes."""

import json
import re
import subprocess
import zipfile
from dataclasses import dataclass

from .artifacts import FIXTURE, MANIFEST, REGISTRATION, ROOT, encoded, read_artifact, sha
from .measurement import same
from .scorer import LABELS, assess, verify_report
from .suite import ARMS, schedule

HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def require(condition, explanation):
    if not condition:
        raise ValueError(explanation)


def parse(raw):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            require(key not in out, "duplicate JSON field " + key)
            out[key] = value
        return out
    def nonfinite(value):
        raise ValueError("nonfinite JSON value")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=nonfinite)


def validate_freeze(freeze):
    require(freeze["suite"] == MANIFEST["id"] and HEX40.fullmatch(freeze["source_commit"]),
            "invalid series registration identity")
    require(freeze["fixture_tree"] == FIXTURE["tree_sha1"] and freeze["fixture_commit"] == FIXTURE["commit_sha1"],
            "incorrect starting fixture")
    for key, path in (("manifest_sha256", "manifest.json"), ("protocol_sha256", "protocol.md"),
                      ("raw_schema_sha256", "raw.schema.json")):
        require(freeze[key] == sha((REGISTRATION / path).read_bytes()), "incorrect registration digest " + key)
    rev = freeze["source_commit"]
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT)
    tracked = git("ls-tree", "-r", "--name-only", rev, "--", "purser", "experiments/e1").decode().splitlines()
    files = {name: sha(git("show", f"{rev}:{name}")) for name in tracked if name.endswith(".py")}
    require(files == freeze["source_files"], "pinned source files differ from recorded Git commit")
    group = lambda selected: sha(encoded({key: files[key] for key in sorted(selected)}))
    study = [p for p in files if p.startswith("experiments/e1/")]
    product = [p for p in files if p.startswith("purser/")]
    def modules(*names):
        return ["experiments/e1/" + n + ".py" for n in names]
    support = modules("artifacts", "measurement", "probe")
    pins = {"controller_sha256": group(study), "scorer_sha256": group(modules("scorer") + support),
            "executor_sha256": group(modules("executor") + support),
            "fault_driver_sha256": group(modules("executor", "run"))}
    for key, value in pins.items():
        require(freeze[key] == value, "incorrect component pin " + key)
    arms = {arm: sha(encoded({"selected_arm": arm, "implementation": group(study + (product if arm == "purser" else []))}))
            for arm in ARMS}
    require(freeze["treatments"] == arms, "incorrect treatment pins")
    rep = freeze.get("replicate", 1)
    require(type(rep) is int and rep > 0 and (rep == 1 or bool(freeze.get("replication_reason"))),
            "invalid replicate or missing replication reason")
    require(freeze.get("experiment", "E1") == "E1", "unexpected frozen experiment")
    return rep


def wire_valid(value, node, schema):
    """Interpret the vocabulary used by the frozen raw schema using only the standard library."""
    if "$ref" in node:
        return wire_valid(value, schema["$defs"][node["$ref"].split("/")[-1]], schema)
    native = {dict: "object", list: "array", str: "string", bool: "boolean", int: "integer", type(None): "null"}
    if "type" in node:
        allowed = node["type"] if type(node["type"]) is list else [node["type"]]
        if native.get(type(value)) not in allowed:
            return False
    if "const" in node and not same(value, node["const"]):
        return False
    if "enum" in node and not any(same(value, e) for e in node["enum"]):
        return False
    if type(value) is dict:
        if set(node.get("required", [])) - value.keys():
            return False
        fields = node.get("properties", {})
        if node.get("additionalProperties") is False and value.keys() - fields.keys():
            return False
        if any(not wire_valid(value[key], rule, schema) for key, rule in fields.items() if key in value):
            return False
    if type(value) is int and (value < node.get("minimum", value) or value > node.get("maximum", value)):
        return False
    if type(value) is str:
        if len(value) < node.get("minLength", 0) or ("pattern" in node and not re.fullmatch(node["pattern"], value)):
            return False
    if not all(wire_valid(value, rule, schema) for rule in node.get("allOf", [])):
        return False
    if "oneOf" in node and sum(wire_valid(value, rule, schema) for rule in node["oneOf"]) != 1:
        return False
    return not ("if" in node and wire_valid(value, node["if"], schema) and not wire_valid(value, node["then"], schema))


@dataclass
class Case:
    raw: dict
    evidence: object
    basis: str

    def comparable(self):
        clean = self.raw["track"] == "clean"
        score = self.evidence.score(not clean)
        return bool(self.raw["validity"] == "complete" and self.raw["terminal"] == "done"
                    and self.basis == "exact" and score is not None
                    and (not clean or (score["correct_parent_closure"] and not score["false_parent_closure"])))


def inspect_series(directory):
    results = {}
    schema = parse((REGISTRATION / "raw.schema.json").read_bytes())
    with zipfile.ZipFile(directory / "raw.zip") as archive:
        members = archive.namelist()
        require(len(members) == len(set(members)), "duplicate ZIP archive member")
        freeze_bytes = archive.read("freeze.json")
        detached = directory / "freeze.json"
        if detached.exists():
            require(detached.read_bytes() == freeze_bytes, "detached registration mismatch")
        freeze = parse(freeze_bytes)
        replicate = validate_freeze(freeze)
        scheduled = list(schedule())
        keys = {f"{t}/{s}/{a}.json" for t, s, _, a in scheduled}
        for name in members:
            if name == "freeze.json":
                continue
            prefix, _, tail = name.partition("/")
            if prefix in ("runs", "started"):
                require(tail in keys, "unregistered archive run or marker")
            else:
                require(re.fullmatch(r"objects/([0-9a-f]{64})\.(json|bin|pack)", name), "invalid object member")
                require(sha(archive.read(name)) == tail.split(".")[0], "object content address mismatch")
        def blob(ref):
            require(type(ref) is dict and ref.keys() == {"path", "sha256", "bytes"}
                    and type(ref["bytes"]) is int and ref["bytes"] >= 0
                    and type(ref["path"]) is str and type(ref["sha256"]) is str and HEX64.fullmatch(ref["sha256"])
                    and ref["path"].rsplit(".", 1)[0] == "objects/" + ref["sha256"], "invalid artifact reference")
            return read_artifact(archive, ref)
        def item(ref):
            return parse(blob(ref))
        def nested_refs(value):
            if type(value) is dict:
                if value.keys() == {"path", "sha256", "bytes"}:
                    blob(value)
                else:
                    for member in value.values():
                        nested_refs(member)
            elif type(value) is list:
                for member in value:
                    nested_refs(member)
        for track, seed, rank, arm in scheduled:
            suffix = f"{track}/{seed}/{arm}.json"
            marker, name = "started/" + suffix, "runs/" + suffix
            if marker in members:
                start = parse(archive.read(marker))
                require(all(same(start.get(k), v) for k, v in
                            {"arm": arm, "seed": seed, "rank": rank, "track": track}.items()), "incorrect start marker")
            if name not in members:
                continue
            require(marker in members, "raw record without scheduled start marker")
            raw = parse(archive.read(name))
            require(wire_valid(raw, schema, schema), "raw record violates frozen schema")
            require((raw["suite"], raw["experiment"], raw["mode"], raw["track"], raw["seed"], raw["arm"], raw["replicate"])
                    == (MANIFEST["id"], "E1", "scripted", track, seed, arm, replicate), "raw series identity mismatch")
            pins = {k: freeze[k] for k in
                    ("fixture_tree", "fixture_commit", "manifest_sha256", "protocol_sha256", "raw_schema_sha256",
                     "scorer_sha256", "executor_sha256", "fault_driver_sha256")}
            pins.update(treatment_sha256=freeze["treatments"][arm], worktree_head=freeze["source_commit"])
            require(pins == raw["inputs"], "raw source inputs differ from frozen pins")
            journal = item(raw["artifacts"]["journal"])
            nested_refs(journal)
            candidates = item(raw["artifacts"]["candidates"])
            platform = item(raw["artifacts"]["platform"])
            for ref in platform["referenced_files"] + platform["definition_artifacts"]:
                blob(ref)
            for key in ("environment_artifact", "treatment_trace"):
                if platform.get(key) is not None:
                    blob(platform[key])
            for oid, entry in candidates.items():
                require(HEX40.fullmatch(oid) and HEX40.fullmatch(entry["tree"]) and entry["start"] == FIXTURE["commit_sha1"]
                        and (entry["parent"] is None if oid == FIXTURE["commit_sha1"] else entry["parent"] in candidates),
                        "incomplete Git candidate ancestry")
                blob(entry["pack"])
            require([e["seq"] for e in journal] == list(range(1, len(journal) + 1)), "non-contiguous journal")
            actions = [r["payload"] for r in journal if r["kind"] == "action"]
            require(len(actions) == raw["cost"]["steps"]
                    and [a["attempt"] for a in actions] == list(range(1, len(actions) + 1)), "step total mismatch")
            basis = platform.get("cost_basis", "exact")
            require(basis in ("exact", "before_release", "observed_lower_bound"), "unknown cost basis")
            start_ns, end_ns = (platform.get(k) for k in ("start_monotonic_ns", "end_monotonic_ns"))
            if start_ns is None or end_ns is None:
                require(start_ns is end_ns is None and raw["cost"]["elapsed_ns"] == 0 and basis != "exact"
                        and raw["validity"] == "infrastructure_invalid", "missing measured clock")
            else:
                require(type(start_ns) is int and type(end_ns) is int
                        and end_ns - start_ns == raw["cost"]["elapsed_ns"], "elapsed time mismatch")
            checks = {}
            for row in journal:
                p = row["payload"]
                if row["kind"] != "oracle":
                    continue
                oid, label = p["candidate"], p["criterion"]
                require(oid in candidates and label in LABELS and p["version"] == 1, "unknown protected subject")
                report = item(p["evidence"])
                judgment = verify_report(report, oid, label, seed, rank)
                require(judgment == report["verdict"] == p["verdict"] == p["observed"], "protected observation disagrees with journal")
                checks.setdefault(oid, set()).add(label)
            faults = [r["payload"] for r in journal if r["kind"] == "fault"]
            require(track == "primary" or not faults, "fault injected into a clean control")
            if faults:
                registration = MANIFEST["experiments"]["E1"]
                j = rank - 1
                variant = registration["fault_cycle"][j % 3]
                target = {"skipped_retry_check": "W2/retry", "outside_scope": "scope/paths",
                          "unchanged_module": LABELS[(j // 3) % 3]}[variant]
                require(len(faults) == 1 and (faults[0]["seed"], faults[0]["intervention"], faults[0]["target"])
                        == (seed, variant, target), "fault differs from the preregistered seed")
            evidence = assess(journal)
            expected = evidence.score(True) if track == "primary" and raw["validity"] != "infrastructure_invalid" else None
            require(same(expected, raw["score"]), "raw grade disagrees with the observed evidence")
            if raw["validity"] == "complete":
                submissions = {a["candidate"] for a in actions if a["action"] == "submit"}
                require(raw["terminal"] == "done" and basis == "exact" and platform.get("treatment_trace")
                        and platform["definition_artifacts"] and platform.get("environment_artifact")
                        and raw["cost"]["elapsed_ns"] <= MANIFEST["budgets"]["scripted"]["max_elapsed_s"] * 10**9
                        and evidence.false_candidates + evidence.correct_candidates and not evidence.missing_candidates
                        and submissions and all(checks.get(oid) == set(LABELS) for oid in submissions),
                        "complete record lacks qualifying candidate, closure or trace evidence")
            results[(track, seed, arm)] = Case(raw, evidence, basis)
    return results


def render(directory):
    seen = inspect_series(directory)
    seeds = MANIFEST["experiments"]["E1"]["seeds"]
    out = ["# Scripted E1 results", "",
           "The table includes verified false closures from interrupted attempts. Completion counts refer to measured, "
           "fully qualified primary runs. Each arm had 20 scheduled seeds.", "",
           "| Arm | Complete | Incomplete | Infrastructure or missing | Seeds with false close | Missing named | Caught | Correct close |",
           "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    totals = {}
    for arm in ARMS:
        rows = [seen.get(("primary", seed, arm)) for seed in seeds]
        complete = [r for r in rows if r and r.comparable()]
        incomplete = sum(bool(r and r.raw["validity"] == "arm_incomplete") for r in rows)
        false = sum(bool(r and r.evidence.false_candidates) for r in rows)
        true = sum(bool(r and r.evidence.correct_candidates) for r in rows)
        scores = [r.evidence.score(True) for r in complete]
        named, caught = (sum(x[k] for x in scores) for k in ("missing_named", "caught"))
        totals[arm] = (len(complete), false, named, caught)
        vals = (arm, len(complete), incomplete, 20-len(complete)-incomplete, false, named, caught, true)
        out.append("| " + " | ".join(map(str, vals)) + " |")
    n, false, named, _ = totals["purser"]
    passed = "missed" if false else ("inconclusive" if n != 20 else ("met" if named >= 19 else "missed"))
    baseline, _, _, caught = totals["loop"]
    no_adv = "inconclusive" if baseline != 20 else ("met" if caught >= 19 else "missed")
    out += ["", f"The absolute E1 threshold for Purser was **{passed}**. "
            f"The original no-advantage threshold for the plain loop was **{no_adv}**.", "",
            "The last two columns give paired differences in steps and elapsed seconds for eligible runs.", "",
            "| Seed | Plain loop | Purser | Equal verifier | Purser − plain | Purser − equal verifier |",
            "| ---: | --- | --- | --- | ---: | ---: |"]
    def label(case):
        if case is None:
            return "missing"
        raw, evidence = case.raw, case.evidence
        outcome = "false close" if evidence.false_candidates else (
            "caught" if case.comparable() and evidence.caught else raw["validity"])
        if raw["validity"] != "complete" and evidence.false_candidates:
            outcome += ", " + raw["validity"]
        return f"{outcome} / {raw['cost']['steps']}" if case.basis == "exact" else outcome + " / cost unavailable"
    def difference(a, b):
        if not a or not b or not (a.comparable() and b.comparable()):
            return "unavailable"
        x, y = a.raw["cost"], b.raw["cost"]
        return f"{x['steps'] - y['steps']:+d} / {(x['elapsed_ns'] - y['elapsed_ns']) / 1e9:+.3f} s"
    for seed in seeds:
        a,b,c = (seen.get(("primary", seed, arm)) for arm in ARMS)
        out.append(f"| {seed} | {label(a)} | {label(b)} | {label(c)} | {difference(b,a)} | {difference(b,c)} |")
    clean = MANIFEST["experiments"]["E1"]["clean_seed"]
    out += ["", "## Clean control", "", "| Arm | Terminal | Steps | Elapsed seconds | Correct close |",
            "| --- | --- | ---: | ---: | --- |"]
    for arm in ARMS:
        case = seen.get(("clean", clean, arm))
        if case and case.comparable():
            raw = case.raw
            out.append(f"| {arm} | {raw['terminal']} | {raw['cost']['steps']} | "
                       f"{raw['cost']['elapsed_ns']/1e9:.3f} | yes |")
        else:
            terminal = case.raw["terminal"] if case else "missing"
            out.append(f"| {arm} | {terminal} | unavailable | unavailable | unqualified |")
    a, b = (seen.get(("clean", clean, arm)) for arm in ("loop", "purser"))
    if a and b and a.comparable() and b.comparable():
        ac, bc = a.raw["cost"]["steps"], b.raw["cost"]["steps"]
        relation = "exceeded" if bc > ac * 2 else "was within"
        overhead = f"Purser used {bc} clean steps and the plain loop used {ac}. Purser {relation} the twice-baseline limit."
    else:
        overhead = "The clean step-overhead comparison is inconclusive."
    out += ["", overhead, "Scripted runs make zero model calls and incur zero model spend.", ""]
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
            raw = item.raw
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
