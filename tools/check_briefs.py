#!/usr/bin/env python3
"""Validate package briefs and contract drafts, and regenerate docs/packages/README.md.

Stop-gap until P0-02 (briefcheck, in Go) replaces it. Needs Python 3.9+ and PyYAML.

    python3 tools/check_briefs.py            # validate; exit 1 on errors
    python3 tools/check_briefs.py --index    # validate, then rewrite docs/packages/README.md
"""
import argparse
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "docs" / "packages"
PHASES = ["P0", "P1", "P2", "P3", "P4", "P5", "P6", "P7"]
KEYS = ["id", "title", "phase", "status", "size", "builder", "test_author", "reviewer", "merge",
        "contract_change", "depends_on", "contract", "scope", "tests_frozen", "reuse",
        "deps_allowed", "budget"]
SECTIONS = ["Goal", "Context", "Design", "Out of scope", "Test plan", "Acceptance criteria",
            "Definition of done", "Escalate if"]
BUILDERS = {"codex", "antigravity", "purser", "you"}
AUTHORS = {"codex", "antigravity", "purser", "none"}
BUDGET = {"S": 200, "M": 600, "L": 600}
CONTRACT_KEYS = ["contract", "kind", "version", "status", "owner", "summary"]
KINDS = {"module", "function", "data", "endpoint", "event", "ui", "cli"}
ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]*$")


class Report:
    def __init__(self):
        self.errors, self.warnings = [], []

    def err(self, where, msg):
        self.errors.append(f"{where}: {msg}")

    def warn(self, where, msg):
        self.warnings.append(f"{where}: {msg}")


def split_front_matter(text):
    if not text.startswith("---\n"):
        return None, text
    end = text.index("\n---\n", 4)
    return yaml.safe_load(text[4:end]), text[end + 5:]


def contract_paths(value):
    if value == "self":
        return []
    items = value if isinstance(value, list) else [value]
    return [str(v).split("@")[0] for v in items]


def load_briefs(rep):
    briefs = {}
    for path in sorted(PKG.glob("P*.md")):
        if path.name.endswith(".contradiction.md"):
            continue
        fm, body = split_front_matter(path.read_text(encoding="utf-8"))
        if fm is None:
            rep.err(path.name, "missing front matter")
            continue
        briefs[path.stem] = (fm, body, path)
    return briefs


def check_brief(pid, fm, body, ids, rep):
    w = f"{pid}.md"
    for k in KEYS:
        if k not in fm:
            rep.err(w, f"missing key {k}")
    if rep.errors and any(e.startswith(w + ": missing key") for e in rep.errors):
        return
    if fm["id"] != pid:
        rep.err(w, f"id {fm['id']} does not match file name")
    if fm["phase"] not in PHASES or not pid.startswith(fm["phase"] + "-"):
        rep.err(w, f"phase {fm['phase']} does not match id")
    if fm["status"] not in ("draft", "ready", "done"):
        rep.err(w, f"bad status {fm['status']}")
    if fm["size"] not in BUDGET:
        rep.err(w, f"bad size {fm['size']}")
    elif fm["size"] == "L" and fm["status"] != "draft":
        rep.err(w, "size L is allowed only in draft; split before ready")
    budget = (fm.get("budget") or {}).get("changed_lines", 0)
    if fm["size"] in BUDGET and budget > BUDGET[fm["size"]]:
        rep.err(w, f"budget {budget} exceeds {BUDGET[fm['size']]} for size {fm['size']}")
    b, t, r = fm["builder"], fm["test_author"], fm["reviewer"]
    if b not in BUILDERS:
        rep.err(w, f"bad builder {b}")
    if t not in AUTHORS:
        rep.err(w, f"bad test_author {t}")
    if b == t and b != "purser":
        rep.err(w, "builder and test_author are the same agent")
    if r not in (t, "you"):
        rep.err(w, f"reviewer {r} must be the test author or you")
    if fm["merge"] not in ("auto", "human"):
        rep.err(w, f"bad merge {fm['merge']}")
    if fm["merge"] == "auto" and (fm["size"] != "S" or fm["contract_change"] or t == "none"):
        rep.err(w, "merge auto requires size S, no contract change and an independent test author")
    writes = (fm.get("scope") or {}).get("write") or []
    if any(str(p).startswith("contracts/") for p in writes) and not fm["contract_change"]:
        rep.err(w, "writes under contracts/ but contract_change is false")
    if t == "none" and fm["tests_frozen"]:
        rep.err(w, "tests_frozen listed but no test author")
    if t != "none" and not fm["tests_frozen"] and fm["status"] == "ready":
        rep.err(w, "ready package with a test author has no tests_frozen")
    for dep in fm["depends_on"]:
        if dep not in ids:
            rep.err(w, f"unknown dependency {dep}")
        elif PHASES.index(dep.split("-")[0]) > PHASES.index(fm["phase"]):
            rep.err(w, f"depends on later phase {dep}")
    for cpath in contract_paths(fm["contract"]):
        if not (ROOT / cpath).exists():
            (rep.err if fm["status"] == "ready" else rep.warn)(w, f"contract {cpath} not found")
    heads = re.findall(r"^## (.+)$", body, re.M)
    for s in SECTIONS:
        if s not in heads:
            rep.err(w, f"missing section '## {s}'")
    m = re.search(r"^## Acceptance criteria\n(.*?)(?=^## )", body, re.M | re.S)
    if m:
        nums = [int(n) for n in re.findall(r"^- AC-(\d+) ", m.group(1), re.M)]
        if not nums:
            rep.err(w, "no acceptance criteria")
        elif nums != list(range(1, len(nums) + 1)):
            rep.err(w, f"AC numbering {nums} is not 1..n")


def check_cycles(briefs, rep):
    graph = {pid: fm["depends_on"] for pid, (fm, _, _) in briefs.items()}
    state = {}

    def visit(n, stack):
        state[n] = 1
        for d in graph.get(n, []):
            if d not in graph:
                continue
            if state.get(d) == 1:
                rep.err(f"{n}.md", "dependency cycle: " + " -> ".join(stack + [d]))
            elif state.get(d) is None:
                visit(d, stack + [d])
        state[n] = 2

    for n in graph:
        if state.get(n) is None:
            visit(n, [n])


def check_reuse(briefs, rep):
    """Every internal package a brief reuses must be written by one of its ancestors."""
    deps = {pid: fm["depends_on"] for pid, (fm, _, _) in briefs.items()}
    writes = {pid: [str(p) for p in ((fm.get("scope") or {}).get("write") or [])]
              for pid, (fm, _, _) in briefs.items()}

    def ancestors(pid, seen=None):
        seen = set() if seen is None else seen
        for d in deps.get(pid, []):
            if d in deps and d not in seen:
                seen.add(d)
                ancestors(d, seen)
        return seen

    for pid, (fm, _, _) in briefs.items():
        anc = ancestors(pid)
        for pkg in fm["reuse"] or []:
            pkg = str(pkg)
            if not pkg.startswith("internal/"):
                continue
            ok = any(w == pkg or w.startswith(pkg + "/") or w.startswith(pkg + "*")
                     for a in anc for w in writes[a])
            if not ok:
                rep.err(f"{pid}.md", f"reuses {pkg}, which no ancestor package writes")


def check_contracts(ids, rep):
    for path in sorted((ROOT / "contracts").rglob("*.yaml")):
        rel = path.relative_to(ROOT)
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            rep.err(str(rel), f"YAML error: {e}")
            continue
        if not isinstance(doc, dict) or "contract" not in doc:
            continue  # schema drafts such as records.draft.yaml
        for k in CONTRACT_KEYS:
            if k not in doc:
                rep.err(str(rel), f"missing key {k}")
        if doc.get("kind") not in KINDS:
            rep.err(str(rel), f"bad kind {doc.get('kind')}")
        if doc.get("owner") not in ids:
            rep.err(str(rel), f"owner {doc.get('owner')} is not a brief")
        if doc.get("kind") in ("module", "function"):
            for k in ("package", "interface"):
                if k not in doc:
                    rep.err(str(rel), f"{doc['kind']} contract missing {k}")
        if doc.get("kind") == "module":
            for k in ("layer", "allowed_imports"):
                if k not in doc:
                    rep.err(str(rel), f"module contract missing {k}")
        for fn in doc.get("functions") or []:
            if not fn.get("examples"):
                rep.err(str(rel), f"function {fn.get('name')} has no examples or vectors")
        for code in (doc.get("errors") or {}):
            if not ERROR_CODE.match(str(code)):
                rep.err(str(rel), f"error code {code} is not snake case")


def write_index(briefs):
    order = sorted(briefs, key=lambda p: (PHASES.index(p.split("-")[0]), p))
    lines = [
        "# Package index",
        "",
        "Generated by `python3 tools/check_briefs.py --index`. Do not edit by hand.",
        "",
        f"{len(order)} packages. P0 to P2 are `ready` with full contracts; P3 to P7 are `draft` and get their contracts in each phase's planning batch.",
        "",
        "| ID | Title | Size | Status | Builder | Tests by | Merge | After |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for pid in order:
        fm = briefs[pid][0]
        after = ", ".join(fm["depends_on"]) or "—"
        lines.append(f"| [{pid}]({pid}.md) | {fm['title']} | {fm['size']} | {fm['status']} | "
                     f"{fm['builder']} | {fm['test_author']} | {fm['merge']} | {after} |")
    lines += ["", "## Dependency graph", "", "```mermaid", "flowchart LR"]
    for ph in PHASES:
        members = [p for p in order if p.startswith(ph + "-")]
        if not members:
            continue
        lines.append(f"  subgraph {ph}")
        for p in members:
            lines.append(f"    {p.replace('-', '_')}[\"{p}\"]")
        lines.append("  end")
    for pid in order:
        for d in briefs[pid][0]["depends_on"]:
            lines.append(f"  {d.replace('-', '_')} --> {pid.replace('-', '_')}")
    lines += ["```", ""]
    (PKG / "README.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", action="store_true")
    args = ap.parse_args()
    rep = Report()
    briefs = load_briefs(rep)
    ids = set(briefs)
    for pid, (fm, body, _) in briefs.items():
        check_brief(pid, fm, body, ids, rep)
    check_cycles(briefs, rep)
    check_reuse(briefs, rep)
    check_contracts(ids, rep)
    for w in rep.warnings:
        print("warning:", w)
    for e in rep.errors:
        print("error:", e)
    print(f"{len(briefs)} briefs, {len(rep.errors)} errors, {len(rep.warnings)} warnings")
    if rep.errors:
        sys.exit(1)
    if args.index:
        write_index(briefs)
        print("wrote docs/packages/README.md")


if __name__ == "__main__":
    main()
