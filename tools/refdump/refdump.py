#!/usr/bin/env python3
"""Record golden event logs and snapshots from reference test scenarios."""
import argparse
import hashlib
import os
import shutil
import sqlite3
import sys
import unittest
from pathlib import Path

# Locate repository root from this script's path
ROOT = Path(__file__).resolve().parents[2]
REF_PYTHON = ROOT / "reference" / "python"
if str(REF_PYTHON) not in sys.path:
    sys.path.insert(0, str(REF_PYTHON))

from purser.kernel.store import Store, _batches
from purser.kernel.wire import canonical, digest

TEST_MODULES = {
    "test_gate": "tests.test_gate",
    "test_work": "tests.test_work",
    "test_decisions": "tests.test_decisions",
    "test_effects": "tests.test_effects",
}


def compute_reference_digest():
    lines = []
    for base in [REF_PYTHON / "purser", REF_PYTHON / "tests"]:
        for root, dirs, files in os.walk(base):
            dirs.sort()
            for f in sorted(files):
                if f.endswith(".pyc") or f == ".DS_Store" or "__pycache__" in root:
                    continue
                path = Path(root) / f
                rel = path.relative_to(ROOT).as_posix()
                h = hashlib.sha256(path.read_bytes()).hexdigest()
                lines.append(f"{rel} {h}\n")
    lines.sort()
    combined = "".join(lines).encode("utf-8")
    return hashlib.sha256(combined).hexdigest()


def record_scenario(mod_name, method_name, ref_digest, out_dir):
    mod = __import__(TEST_MODULES[mod_name], fromlist=["*"])
    target_cls = None
    for attr in dir(mod):
        c = getattr(mod, attr)
        if isinstance(c, type) and issubclass(c, unittest.TestCase) and hasattr(c, method_name):
            target_cls = c
            break
    if target_cls is None:
        raise RuntimeError(f"Could not find test class for {mod_name}.{method_name}")

    opened = []
    orig_init = Store.__init__

    def tracking_init(self, path):
        orig_init(self, path)
        p = str(self.path)
        if p not in opened:
            opened.append(p)

    Store.__init__ = tracking_init

    recorded_data = None
    orig_doCleanups = unittest.TestCase.doCleanups

    def hook_doCleanups(self):
        nonlocal recorded_data
        for p in opened:
            if os.path.exists(p):
                try:
                    con = sqlite3.connect(p)
                    cur = con.cursor()
                    cur.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='events'")
                    if cur.fetchone()[0] > 0:
                        cur.execute("SELECT count(*) FROM events")
                        if cur.fetchone()[0] > 0:
                            store = Store(p)
                            event_rows = con.execute("SELECT revision, body FROM events ORDER BY revision").fetchall()

                            snapshots = []
                            for batch in _batches(con):
                                end_rev = batch[-1]["revision"]
                                snap = store.snapshot(revision=end_rev)
                                chk = digest("projection", snap["projection"])
                                snap_entry = {
                                    "revision": snap["revision"],
                                    "head_hash": snap["head_hash"],
                                    "projection_checksum": chk,
                                    "projection": snap["projection"],
                                }
                                snapshots.append(canonical(snap_entry))

                            recorded_data = ([row[1] for row in event_rows], snapshots)
                    con.close()
                except Exception:
                    pass
        orig_doCleanups(self)

    unittest.TestCase.doCleanups = hook_doCleanups

    try:
        suite = unittest.TestSuite()
        suite.addTest(target_cls(method_name))
        runner = unittest.TextTestRunner(stream=open(os.devnull, "w"))
        res = runner.run(suite)
    finally:
        Store.__init__ = orig_init
        unittest.TestCase.doCleanups = orig_doCleanups

    scenario_name = f"{mod_name}.{method_name}"
    if not res.wasSuccessful() or not recorded_data:
        raise RuntimeError(f"Scenario {scenario_name} failed or produced no recorded events")

    events, snaps = recorded_data
    sc_dir = out_dir / scenario_name
    sc_dir.mkdir(parents=True, exist_ok=True)

    with open(sc_dir / "events.jsonl", "wb") as f:
        for ev in events:
            f.write(ev + b"\n")

    with open(sc_dir / "snapshots.jsonl", "wb") as f:
        for s in snaps:
            f.write(s + b"\n")

    meta = {
        "scenario": scenario_name,
        "reference_digest": ref_digest,
    }
    with open(sc_dir / "meta.json", "wb") as f:
        f.write(canonical(meta) + b"\n")


def update_manifest(vectors_dir):
    entries = []
    for root, dirs, files in os.walk(vectors_dir):
        dirs.sort()
        for f in sorted(files):
            if f == "MANIFEST.sha256":
                continue
            path = Path(root) / f
            rel = path.relative_to(vectors_dir).as_posix()
            h = hashlib.sha256(path.read_bytes()).hexdigest()
            entries.append((rel, h))
    entries.sort(key=lambda x: x[0])
    manifest_path = vectors_dir / "MANIFEST.sha256"
    with open(manifest_path, "w", encoding="utf-8") as f:
        for rel, h in entries:
            f.write(f"{h}  {rel}\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", default=str(ROOT / "testdata" / "vectors" / "golden"),
                    help="Directory to write golden scenario logs")
    ap.add_argument("--scenarios", default=str(Path(__file__).resolve().parent / "scenarios.txt"),
                    help="File listing scenario test methods")
    ap.add_argument("--manifest", action="store_true",
                    help="Regenerate testdata/vectors/MANIFEST.sha256")
    args = ap.parse_args()

    out_dir = Path(args.output).resolve()
    scenarios_path = Path(args.scenarios).resolve()
    lines = [line.strip() for line in scenarios_path.read_text(encoding="utf-8").splitlines()]
    scenarios = [l for l in lines if l and not l.startswith("#")]

    ref_digest = compute_reference_digest()

    for item in scenarios:
        mod_name, method_name = item.split(".")
        record_scenario(mod_name, method_name, ref_digest, out_dir)

    if args.manifest:
        vectors_dir = ROOT / "testdata" / "vectors"
        update_manifest(vectors_dir)


if __name__ == "__main__":
    main()
