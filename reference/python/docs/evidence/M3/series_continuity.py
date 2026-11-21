"""Rebuild registered E2 Purser histories and compare historical queries with cold prefixes.

Run in the joined checkout with python3.11 -B docs/evidence/M3/series_continuity.py RAW_ZIP.
Only the local CLI reads product state. No participant is dispatched.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[3]
ENVIRONMENT = {"PATH": os.defpath, "TZ": "UTC", "LC_ALL": "C"}
SUBJECTS = ("W1", "W2", "W3")


def require(condition, explanation):
    if not condition:
        raise ValueError(explanation)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def referenced(archive, reference):
    raw = archive.read(reference["path"])
    require(sha(raw) == reference["sha256"] and len(raw) == reference["bytes"],
            "changed artifact " + reference["path"])
    return json.loads(raw)


def cli(database, *args):
    output = subprocess.check_output([sys.executable, "-B", "-m", "purser", "--db", str(database), *args],
                                     cwd=ROOT, env=ENVIRONMENT, timeout=30)
    return json.loads(output)


def restore(directory, originals, objects):
    database = directory / "ledger.sqlite"
    directory.mkdir()
    cli(database, "init")
    artifacts = Path(str(database) + ".artifacts")
    artifacts.mkdir(exist_ok=True)
    for digest, data in objects.items():
        (artifacts / digest).write_bytes(data)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.executemany("INSERT INTO events(revision,body) VALUES (?,?)", enumerate(originals, 1))
    replay = cli(database, "replay", "--as-of", json.loads(originals[-1])["at"])
    require(replay["revision"] == len(originals) and replay["head_hash"] == json.loads(originals[-1])["hash"],
            "cold replay did not land on the retained original head")
    with closing(sqlite3.connect(database)) as connection:
        copies = [body for body, in connection.execute("SELECT body FROM events ORDER BY revision")]
    require(copies == originals, "cold replay altered original event bytes")
    return database


def public_state(database, rev, moment):
    options = ("--revision", str(rev), "--as-of", moment)
    status = cli(database, "status", *options)
    frontier = cli(database, "frontier", *options)
    require(frontier["actions"] == status["answers"]["q10"], "two public frontier answers disagree")
    reasons = {work: cli(database, "why", work, *options) for work in SUBJECTS}
    return {"status": status, "frontier": frontier, "why": reasons}


def witness_objects(archive, events):
    keys = {value for event in events for field, value in event["data"].items()
            if field.endswith("_sha256") and isinstance(value, str)}
    data = {}
    for name in archive.namelist():
        if name.startswith("objects/"):
            address = Path(name).name.split(".", 1)[0]
            if address in keys and address not in data:
                content = archive.read(name)
                require(sha(content) == address, "wrong kernel witness bytes for " + address)
                data[address] = content
    require(data.keys() == keys, "missing kernel witness objects " + str(sorted(keys - data.keys())))
    return data


def checks(before, after, final, track, cause):
    old, changed, completed = (state["status"]["answers"] for state in (before, after, final))
    states = lambda answers: {row["work"]: row for row in answers["q02"]}
    before_work, after_work, final_work = map(states, (old, changed, completed))
    expected_state = "submitted" if track == "primary" else "closed"
    require(before_work["W2"]["state"] == expected_state and after_work["W2"]["state"] == "stale",
            "the changed premise did not interrupt the registered W2 state")
    require(final_work["W2"]["state"] == "closed" and final_work["W-notify"]["state"] == "closed",
            "the chosen E2 continuity example did not reach a covered repaired parent")
    require(final_work["W2"]["generation"] > before_work["W2"]["generation"] and
            final_work["W2"]["candidate"] != before_work["W2"]["candidate"], "repair reused the old subject")
    old_decisions = {row["decision"] for row in old["q03"] if row["state"] == "effective" and "A1" in row["support"]}
    reconsidered = {row["decision"] for row in changed["q03"] if row["state"] == "reconsidering"}
    require(len(old_decisions) == 1 and old_decisions == reconsidered,
            "q03 did not explain the original supported decision's transition")
    footprint = next(row["affected"] for row in changed["q04"] if row["assumption"] == "A1")
    require(set(footprint) == {"W2", *old_decisions}, "q04 lost a dependent or traversed a hierarchy edge")
    actions = {(row["action"], row["subject"]) for row in changed["q10"]}
    require(("rework", "W2") in actions and ("reconsider", next(iter(old_decisions))) in actions
            and ("execute", "W2") not in actions and ("evaluate_close", "W2") not in actions,
            "the frontier did not respect the invalidation")
    reasons = after["why"]["W2"]
    require(any(row["code"] == "invalidated_support" for row in reasons["reasons"]), "why lost the invalidation")
    require(cause in json.dumps(reasons), "why lost the observed cause of W2 invalidation")
    require(any(row["state"] == "effective" and row["decision"] not in old_decisions for row in completed["q03"]),
            "the repaired result lacks an effective replacement decision")
    for work in ("W1", "W3"):
        require(before_work[work]["state"] == "closed" and before_work[work] == after_work[work] == final_work[work],
                "an independent sibling changed state or identity")
        old_evidence = [row for row in old["q07"] if row["work"] == work]
        require(old_evidence and all(row["state"] == "passed" and row["evidence"] for row in old_evidence),
                "missing independent sibling evidence before the change")
        for view in (changed, completed):
            require([row for row in view["q07"] if row["work"] == work] == old_evidence,
                    "qualified sibling evidence was replaced")
        require(before["why"][work]["record"] == after["why"][work]["record"] == final["why"][work]["record"],
                "sibling outcome or last-change revision drifted")


def read_case(archive, track, seed, scratch):
    raw = json.loads(archive.read(f"runs/{track}/{seed}/purser.json"))
    platform = referenced(archive, raw["artifacts"]["platform"])
    trace = referenced(archive, platform["treatment_trace"])
    originals = [event.encode("utf-8") for event in trace["original_events"]]
    events = list(map(json.loads, originals))
    triggers = [event for event in events if event["data"].get("assumption") == "A1"
                and event["data"].get("finding") == "contradicts"]
    require(len(triggers) == 1, "expected one retained contradiction of A1")
    trigger = triggers[0]
    start = trigger["batch_start"]
    points = {"before": start - 1, "invalidated": start + trigger["batch_size"] - 1, "terminal": len(events)}
    objects = witness_objects(archive, events)
    full = restore(scratch / "full", originals, objects)
    states = {}
    for stage, revision in points.items():
        moment = events[revision - 1]["at"]
        states[stage] = public_state(full, revision, moment)
        if revision < len(events):
            cold = restore(scratch / stage, originals[:revision], objects)
            require(public_state(cold, revision, moment) == states[stage],
                    f"cold {track} prefix and later historical reads disagree at {stage}")
    checks(states["before"], states["invalidated"], states["terminal"], track, trigger["data"]["id"])
    return {"seed": seed, "track": track, "arm": "purser", "revisions": points,
            "cause": trigger["data"]["id"], "witnesses_sha256": sorted(objects),
            "original_events": len(events), "views": states}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    path = parser.parse_args().archive
    with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory() as working:
        seed = 2001
        cases = []
        for track in ("primary", "closed-affected"):
            scratch = Path(working) / track
            scratch.mkdir()
            cases.append(read_case(archive, track, seed, scratch))
        result = {"raw_sha256": sha(path.read_bytes()), "W2_status_and_siblings": cases}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
