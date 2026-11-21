"""Capture the real M4 milestone and reproduce its public view from original events."""
import argparse
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile

from docs.evidence.M3.series_continuity import cli, require, restore
from purser.kernel.artifacts import Artifacts
from purser.kernel.store import Store
from purser.kernel.wire import canonical
from purser.kernel.workview import WorkView


EVIDENCE = Path(__file__).resolve().parents[1]
PREVIOUS = (EVIDENCE / "M2/development-log.zip", EVIDENCE / "M3/project-ledger.zip")
WORK = "W-m4-git-authority"
OBLIGATION = "O-m4-integrated"
ASSIGNMENT = "AS-m4-integration-1"


def prefix_checks(rows):
    for archive in PREVIOUS:
        with ZipFile(archive) as older:
            prior = older.read("events.jsonl").splitlines()
            require(rows[:len(prior)] == prior, f"original prefix changed in {archive.name}")


def observations(db, revision, moment):
    options = ("--revision", str(revision), "--as-of", moment)
    data = {"status": cli(db, "status", *options), "frontier": cli(db, "frontier", *options)}
    for subject in (WORK, OBLIGATION, "W-m3-decision-impact", "C-purser-delivery"):
        data[subject] = cli(db, "why", subject, *options)
    return data


def qualified(db, revision, candidate):
    snapshot = Store(db).snapshot(revision=revision)
    view = WorkView(snapshot["projection"])
    fields = view.fields(WORK)
    require(fields["state"] == "closed" and view.closed(WORK), "M4 is not closed on this revision")
    require(fields["candidate"] == {"kind": "git.commit", "algorithm": "sha1", "digest": candidate},
            "the M4 candidate differs")
    state, witness = view.qualification(OBLIGATION)
    require(state == "passed" and witness["evidence"] and not witness["waiver"], "no independent M4 pass")
    run = view.fields(witness["run"])
    require(run["work"] == WORK and run["candidate"] == fields["candidate"] and run["result"] == fields["result"]
            and run["generation"] == fields["generation"] and run["assignment"] == ASSIGNMENT,
            "independent run is not attached to the submitted work")
    require(view.fields("C-purser-delivery")["state"] == "active" and not view.closed("W-purser-delivery"),
            "the parent project is not active")
    return {"revision": revision, "head_hash": snapshot["head_hash"], "candidate": candidate,
            "outcome": fields["closed_outcome"], "result": fields["result"], "witness": witness}


def capture(db, archive, revision, candidate):
    facts = qualified(db, revision, candidate)
    uri = db.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as source:
        rows = [body for body, in source.execute("SELECT body FROM events WHERE revision<=? ORDER BY revision", (revision,))]
    events = list(map(json.loads, rows))
    require(len(events) == revision and events[-1]["hash"] == facts["head_hash"], "live head moved during capture")
    prefix_checks(rows)
    moment = events[-1]["at"]
    files = {"events.jsonl": b"\n".join(rows) + b"\n", "public.json": canonical(observations(db, revision, moment))}
    objects = Artifacts(db)
    for event in events:
        for key, value in event["data"].items():
            if key in ("definition_sha256", "environment_sha256", "artifact_sha256"):
                files["objects/" + value] = objects.read(value)
    # The original M2 archive carries the additional fixture named inside its criterion.
    with ZipFile(PREVIOUS[0]) as original:
        for name in original.namelist():
            if name.startswith("objects/"):
                data = original.read(name)
                require(name == "objects/" + sha256(data).hexdigest(), "historical fixture digest differs")
                require(files.get(name, data) == data, "historical object contents differ")
                files[name] = data
    manifest = {**facts, "format": "purser.m4.project-ledger.v1", "as_of": moment,
                "members": {name: sha256(data).hexdigest() for name, data in files.items()}}
    with ZipFile(archive, "x", compression=ZIP_DEFLATED) as out:
        for name, data in sorted(files.items()):
            out.writestr(name, data)
        out.writestr("manifest.json", canonical(manifest))


def verify(archive):
    with ZipFile(archive) as source:
        m = json.loads(source.read("manifest.json"))
        require(sorted(source.namelist()) == sorted(["manifest.json", *m["members"]]), "archive membership differs")
        for name, expected in m["members"].items():
            require(sha256(source.read(name)).hexdigest() == expected, f"changed {name}")
        rows = source.read("events.jsonl").splitlines()
        require(len(rows) == m["revision"] and rows and json.loads(rows[-1])["hash"] == m["head_hash"]
                and json.loads(rows[-1])["at"] == m["as_of"], "archive revision or time differs from the event log")
        prefix_checks(rows)
        objects = {name.split("/")[1]: source.read(name) for name in m["members"] if name.startswith("objects/")}
        require(all(sha256(data).hexdigest() == key for key, data in objects.items()), "object address differs")
        with tempfile.TemporaryDirectory(prefix="purser-m4-project-readback-") as scratch:
            db = restore(Path(scratch) / "cold", rows, objects)
            facts = qualified(db, m["revision"], m["candidate"])
            require(all(m[k] == v for k, v in facts.items()), "cold milestone identity differs")
            require(canonical(observations(db, m["revision"], m["as_of"])) == source.read("public.json"),
                    "cold CLI results differ")
        return {**facts, "objects": len(objects), "archive_sha256": sha256(archive.read_bytes()).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--capture", metavar="DB", type=Path)
    parser.add_argument("--revision", type=int)
    parser.add_argument("--candidate")
    args = parser.parse_args()
    if args.capture:
        if not args.revision or not args.candidate:
            parser.error("capture requires the closed revision and the submitted Git candidate")
        capture(args.capture, args.archive, args.revision, args.candidate)
    print(json.dumps(verify(args.archive), sort_keys=True))
