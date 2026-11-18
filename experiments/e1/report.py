"""Validate the registered archive and report qualified comparisons plus directly observed outcomes."""

import json
import re
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .outcomes import CLOSED
from .accounting import READ_ERRORS, PublicHistory, check_exit, digest, journal_prefix
from .artifacts import FIXTURE, MANIFEST, REGISTRATION, ROOT, START, encoded, extract_tree, git, read_artifact, sha
from .executor import PARENT
from .intervention import validate_fault, verify_fault_candidate
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
    outcome = modules("outcomes") if "experiments/e1/outcomes.py" in files else []
    pins = {"controller_sha256": group(study), "scorer_sha256": group(modules("scorer") + outcome + support),
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
    issues: tuple = ()
    final_parent: tuple | None = None
    measured_endpoint: bool = False

    def comparable(self):
        if self.issues or self.basis != "exact" or self.raw["validity"] != "complete" or self.raw["terminal"] != "done":
            return False
        primary = self.raw["track"] == "primary"
        if self.evidence.score(primary) is None or not self.final_parent or self.final_parent[0] not in CLOSED:
            return False
        oid = self.final_parent[1]
        state, measured_parent = self.evidence.final_parent
        if state not in CLOSED or measured_parent != oid:
            return False
        if primary:
            return oid in self.evidence.correct_candidates or oid in self.evidence.false_candidates
        return oid in self.evidence.correct_candidates and self.evidence.facts["false_parent_closure"] is False


class Objects:
    def __init__(self, archive):
        self.archive = archive
        self.parsed, self.content = {}, {}

    def blob(self, ref):
        require(type(ref) is dict and ref.keys() == {"path", "sha256", "bytes"}
                and type(ref["bytes"]) is int and ref["bytes"] >= 0 and digest(ref["sha256"], 64)
                and type(ref["path"]) is str and ref["path"] in
                {f"objects/{ref['sha256']}.{suffix}" for suffix in ("json", "bin", "pack")}, "invalid artifact reference")
        key = (ref["path"], ref["sha256"], ref["bytes"])
        try:
            if key not in self.content:
                self.content[key] = read_artifact(self.archive, ref)
            return self.content[key]
        except (KeyError, FileNotFoundError, zipfile.BadZipFile) as exc:
            raise ValueError("unavailable object " + ref["path"]) from exc

    def references(self, value):
        if type(value) is dict:
            if "path" in value and ("sha256" in value or "bytes" in value):
                yield value
            else:
                for member in value.values():
                    yield from self.references(member)
        elif type(value) is list:
            for member in value:
                yield from self.references(member)

    def item(self, ref):
        key = encoded(ref)
        if key not in self.parsed:
            self.parsed[key] = parse(self.blob(ref))
        return self.parsed[key]


class GitPacks:
    def __init__(self, directory):
        self.directory, self.repos, self.entries = directory, {}, {}

    def unpack(self, reference, objects):
        pack = objects.blob(reference)
        path = self.directory / reference["sha256"]
        if path not in self.repos:
            path.mkdir(exist_ok=True)
            git(path, "init", "-q", "--bare", "--object-format=sha1")
            git(path, "index-pack", "--stdin", data=pack)
            git(path, "fsck", "--full", "--no-dangling")
            self.repos[path] = path
        return path

    def restore(self, oid, entry, objects):
        key = oid, encoded(entry)
        if key in self.entries:
            return self.entries[key]
        require(digest(oid, 40) and digest(entry["tree"], 40) and entry["start"] == START
                and (entry["parent"] is None if oid == START else digest(entry["parent"], 40)),
                "invalid Git candidate identity")
        path = self.unpack(entry["pack"], objects)
        git(path, "merge-base", "--is-ancestor", START, oid)
        ancestry = git(path, "rev-list", "--parents", "-n", "1", oid).decode().split()
        require(ancestry == [oid] + ([entry["parent"]] if entry["parent"] else [])
                and git(path, "rev-parse", oid + "^{tree}").decode().strip() == entry["tree"],
                "archived Git parent or tree identity mismatch")
        self.entries[key] = path
        return path


def inspect_case(raw, schema, objects, packs, freeze, track, seed, rank, arm):
    issues = []

    def issue(message):
        if message not in issues:
            issues.append(message)

    def read(label, operation, default=None):
        try:
            return operation()
        except (*READ_ERRORS, OSError, subprocess.SubprocessError) as exc:
            issue(f"{label}: {exc}")
            return default

    if not wire_valid(raw, schema, schema):
        issue("raw record violates frozen schema")
    if "terminal" not in raw or "validity" not in raw:
        raw = dict(raw, terminal=raw.get("terminal", "unavailable"), validity=raw.get("validity", "unavailable"))
    artifacts = raw["artifacts"]
    journal, ordered = journal_prefix(read("journal", lambda: objects.item(artifacts["journal"])), arm, issue)
    platform = read("platform", lambda: objects.item(artifacts["platform"]), {})
    if type(platform) is not dict:
        issue("invalid platform artifact")
        platform = {}
    declared = read("candidates", lambda: objects.item(artifacts["candidates"]), {})
    if type(declared) is not dict:
        issue("invalid candidate map")
        declared = {}
    candidates, repos = {}, {}
    for oid, entry in declared.items():
        repo = read(f"candidate {oid}", lambda: packs.restore(oid, entry, objects))
        if repo is not None:
            candidates[oid], repos[oid] = entry, repo
            if oid != START and entry["parent"] not in declared:
                issue(f"candidate {oid} lacks its archived parent entry")

    modern = "experiments/e1/accounting.py" in freeze["source_files"] or "clock_stopped" in platform
    basis, endpoint_ok = check_exit(raw, journal, platform, modern, issue)
    measured_endpoint = (basis == "exact" and endpoint_ok and ordered
                         and (raw["terminal"] != "harness_failure" or modern and platform.get("participant_exit") is not None))
    if modern and type(platform.get("public_history_complete")) is not bool:
        issue("public history certainty missing")
    complete_exit = raw.get("validity") == "complete"
    infrastructure = raw.get("validity") == "infrastructure_invalid"
    history = PublicHistory(arm, modern, objects.item, issue)
    observed = history.reconcile(journal, platform, incomplete=not complete_exit)
    for ref in objects.references(journal):
        read("journal reference", lambda: objects.blob(ref))
    refs = platform.get("referenced_files", [])
    if type(refs) is list:
        for ref in refs:
            read("referenced file", lambda: objects.blob(ref))
    else:
        issue("invalid platform file manifest")
    definitions = platform.get("definition_artifacts", [])
    labels = set()
    if type(definitions) is list:
        for ref in definitions:
            definition = read("operational definition", lambda: objects.item(ref))
            if type(definition) is not dict:
                issue("invalid operational definition")
                continue
            label = definition.get("criterion")
            if (label not in LABELS or label in labels or not same(definition.get("version"), 1)
                    or definition.get("fixture") != FIXTURE["tree_sha1"]):
                issue("operational criterion identity mismatch")
                continue
            expected = {name: freeze["source_files"].get("experiments/e1/" + name)
                        for name in ("observer.py", "probe.py", "measurement.py")}
            if not same(definition.get("public_verifier"), expected):
                issue("operational verifier differs from the source registration")
            labels.add(label)
    else:
        issue("invalid operational definition manifest")
    environment = platform.get("environment_artifact")
    if environment is not None:
        recorded = read("environment", lambda: objects.item(environment))
        expected = {"git": platform.get("git"), "platform": platform.get("os"), "python": platform.get("python")}
        if not same(recorded, expected):
            issue("environment differs from platform")
    if not infrastructure and (labels != set(LABELS) or not environment or not platform.get("treatment_trace")
                               or not all(type(platform.get(k)) is str and platform[k] for k in ("git", "os", "python", "sqlite"))):
        issue("participant exit lacks enrolled definitions, environment or trace")
    if not infrastructure and modern and platform.get("public_history_complete") is not True:
        issue("participant exit has incomplete public history")

    checks = {}
    duplicates = set()
    for event in observed:
        if event["kind"] != "oracle":
            continue
        p = event["payload"]
        def check_oracle():
            oid, label = p["candidate"], p["criterion"]
            require(oid in candidates and label in LABELS and same(p["version"], 1), "unknown protected subject")
            report = objects.item(p["evidence"])
            judgment = verify_report(report, oid, label, seed, rank)
            require(same(report.get("version"), 1) and judgment == report["verdict"] == p["verdict"] == p["observed"]
                    and p["expected"] == "all specified cases pass", "protected observation disagrees with journal")
            return oid, label
        identity = read(f"oracle at event {event.get('seq')}", check_oracle)
        if identity is not None:
            if identity in checks:
                duplicates.add(identity)
                issue(f"duplicate protected criterion {identity}")
            checks[identity] = event
    valid_oracles = {id(event) for key, event in checks.items() if key not in duplicates}
    reliable = [e for e in observed if e["kind"] != "oracle" or id(e) in valid_oracles]
    for event in reliable:
        p = event["payload"]
        if event["kind"] in ("action", "public_state", "fault") and p.get("candidate") is not None and p["candidate"] not in candidates:
            issue(f"missing retained Git candidate for event {event.get('seq')}")

    # Public work may still have correctly or incorrectly closed when an intervention is unavailable.
    try:
        fault = validate_fault(reliable, track, seed, rank)
        if fault is not None:
            require(fault.get("candidate") in candidates, "faulted candidate unavailable")
            verify_fault_candidate(repos[fault["candidate"]], candidates, reliable, seed, rank)
    except (*READ_ERRORS, OSError, subprocess.SubprocessError) as exc:
        issue(f"registered intervention: {exc}")
        reliable = [e for e in reliable if e["kind"] != "fault"]

    known_history = ordered and endpoint_ok and history.complete and history.final is not None
    if modern:
        known_history = known_history and platform.get("public_history_complete") is True
    elif infrastructure:
        known_history = False
    evidence = assess(reliable, history_complete=known_history, candidates=candidates)
    expected = evidence.score(True) if track == "primary" and not infrastructure else None
    if not same(expected, raw.get("score")):
        issue("raw grade disagrees with the observed evidence")
    final = history.final[PARENT] if history.final is not None else None
    if complete_exit:
        submissions = {e["payload"]["candidate"] for e in reliable if e["kind"] == "action" and e["payload"]["action"] == "submit"}
        if not submissions or any((oid, label) not in checks or (oid, label) in duplicates for oid in submissions for label in LABELS):
            issue("complete record lacks candidate-specific protected measurements")
        if not final or final[0] not in CLOSED or not (final[1] in evidence.false_candidates or final[1] in evidence.correct_candidates):
            issue("complete record lacks its final measured public parent")
        if not known_history or evidence.missing_candidates or track == "primary" and expected is None:
            issue("complete record lacks the observed history required to score")
    return Case(raw, evidence, basis, tuple(issues), final, measured_endpoint)


def inspect_series(directory, *, strict=True):
    results = {}
    schema = parse((REGISTRATION / "raw.schema.json").read_bytes())
    with zipfile.ZipFile(directory / "raw.zip") as archive, tempfile.TemporaryDirectory(prefix="purser-e1-inspect-") as tmp:
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
                if strict:
                    require(sha(archive.read(name)) == tail.split(".")[0], "object content address mismatch")
        objects, packs = Objects(archive), GitPacks(Path(tmp))
        for track, seed, rank, arm in scheduled:
            suffix = f"{track}/{seed}/{arm}.json"
            marker, name = "started/" + suffix, "runs/" + suffix
            if name not in members and marker not in members:
                continue
            try:
                require(marker in members, "raw record without scheduled start marker")
                start = parse(archive.read(marker))
                require(all(same(start.get(k), v) for k, v in {"arm": arm, "seed": seed, "rank": rank, "track": track}.items()),
                        "incorrect start marker")
                require(name in members, "started case lacks a run record")
                raw = parse(archive.read(name))
                require(same([raw[k] for k in ("suite", "experiment", "mode", "track", "seed", "arm", "replicate")],
                             [MANIFEST["id"], "E1", "scripted", track, seed, arm, replicate]), "raw series identity mismatch")
                pins = {k: freeze[k] for k in ("fixture_tree", "fixture_commit", "manifest_sha256", "protocol_sha256", "raw_schema_sha256",
                                                  "scorer_sha256", "executor_sha256", "fault_driver_sha256")}
                pins.update(treatment_sha256=freeze["treatments"][arm], worktree_head=freeze["source_commit"])
                require(same(pins, raw["inputs"]), "raw source inputs differ from frozen pins")
                case = inspect_case(raw, schema, objects, packs, freeze, track, seed, rank, arm)
            except (*READ_ERRORS, zipfile.BadZipFile) as exc:
                if strict:
                    raise ValueError(f"{suffix}: {exc}") from exc
                raw = {"track": track, "seed": seed, "arm": arm, "validity": "unavailable", "terminal": "unavailable",
                       "cost": {"steps": None, "elapsed_ns": None}, "score": None}
                case = Case(raw, assess([], history_complete=False), "unavailable", (str(exc),))
            if strict and case.issues:
                raise ValueError(f"{suffix}: " + " | ".join(case.issues))
            results[(track, seed, arm)] = case
    return results


def render(directory):
    from .summary import render_summary
    return render_summary(inspect_series(directory, strict=False))


def verify_candidates(directory):
    """Restore each self-contained pack and repeat the frozen protected behavior on its own bytes."""
    from . import scorer

    seen = inspect_series(directory, strict=True)
    with tempfile.TemporaryDirectory(prefix="purser-e1-report-") as tmp, zipfile.ZipFile(directory / "raw.zip") as archive:
        packs, objects, measurements = GitPacks(Path(tmp)), Objects(archive), {}
        for track, seed, rank, arm in schedule():
            item = seen.get((track, seed, arm))
            if not item:
                continue
            raw = item.raw
            entries = objects.item(raw["artifacts"]["candidates"])
            repos = {oid: packs.restore(oid, entry, objects) for oid, entry in entries.items()}
            journal = objects.item(raw["artifacts"]["journal"])
            fault = next((event["payload"] for event in journal if event["kind"] == "fault"), None)
            if fault is not None:
                verify_fault_candidate(repos[fault["candidate"]], entries, journal, seed, rank, replay_check=True)
            reports = {}
            for event in journal:
                if event["kind"] == "oracle":
                    payload = event["payload"]
                    reports.setdefault(payload["candidate"], {})[payload["criterion"]] = payload["evidence"]
            for oid, archived in reports.items():
                key = (seed, rank, oid)
                previous = measurements.setdefault(key, {})
                missing = archived.keys() - previous.keys()
                if missing:
                    with extract_tree(repos[oid], oid) as checkout:
                        current = scorer.measure(repos[oid], oid, Path(checkout), seed, rank, labels=missing)
                    previous.update(current)
                for label, ref in archived.items():
                    if encoded(previous[label]) != objects.blob(ref):
                        raise ValueError("archived input produced a different protected report")
        return {"runs": len(seen), "independent_git_packs": len(packs.repos), "reexecuted_seed_candidates": len(measurements)}
