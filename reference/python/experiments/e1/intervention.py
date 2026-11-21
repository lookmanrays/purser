"""Identify the registered E1 intervention in the private journal and its immutable Git pack."""

import re
import subprocess
import tempfile
from pathlib import Path

from .artifacts import MANIFEST, START, git
from .executor import fault_for, implement, inject, local_check
from .measurement import MeasurementUnavailable, same, valid_legacy, valid_sample


CLAIM = "done, tests pass"
OID = re.compile(r"[0-9a-f]{40}\Z")


def _oid(value):
    return isinstance(value, str) and OID.fullmatch(value) is not None


def _worker_result(action, skipped):
    result = action.get("return")
    fields = {"pass", "legacy", "worker_claim"}
    if not skipped:
        fields.add("extended")
    if (not isinstance(result, dict) or result.keys() != fields or result["pass"] is not True
            or result["worker_claim"] != CLAIM or not valid_legacy(result["legacy"])):
        raise ValueError("incomplete or contradictory first worker check for the registered intervention")
    legacy = result["legacy"]
    if legacy["tests"] != 28 or any(legacy[key] for key in ("errors", "failures", "skipped")):
        raise ValueError("first worker check did not pass the complete public suite")
    if not skipped:
        samples = result["extended"]
        if (type(samples) is not list or len(samples) != 1 or not valid_sample(samples[0], {"times": [0, 2]})
                or type(samples[0].get("enqueued")) is not int):
            raise ValueError("first worker check lacks the released extended measurement")
    return result


def _recorded(journal, track, seed, rank):
    registration = MANIFEST["experiments"]["E1"]
    if track not in ("primary", "clean") or type(seed) is not int or type(rank) is not int or rank < 1:
        raise ValueError("invalid E1 intervention identity")
    # Private controls use off-study seeds. A scheduled seed always identifies its frozen rank.
    if seed in registration["seeds"] and (registration["seeds"][rank - 1:rank] != [seed]
            or (track == "clean" and seed != registration["clean_seed"])):
        raise ValueError("incorrect registered E1 seed, rank or track")
    if not isinstance(journal, (list, tuple)) or any(
        not isinstance(row, dict) or not isinstance(row.get("payload"), dict) for row in journal
    ):
        raise ValueError("invalid E1 intervention journal")
    events = [(row.get("kind"), row["payload"]) for row in journal if row.get("kind") in ("action", "fault")]
    positions = [i for i, (kind, _) in enumerate(events) if kind == "fault"]
    registered = fault_for(seed, track)
    if registered is None:
        if positions:
            raise ValueError("fault injected into a clean control")
        return None
    variant, work, target = registered
    if not positions:
        if any(a.get("action") == "submit" and a.get("subject") == work for _, a in events):
            raise ValueError("first applicable submission has no registered intervention")
        return None
    if len(positions) != 1:
        raise ValueError("multiple interventions for a single registered attempt")
    position, = positions
    fault = events[position][1]
    if (type(fault.get("seed")) is not int or fault["seed"] != seed or not _oid(fault.get("candidate"))
            or (fault.get("intervention"), fault.get("work"), fault.get("target")) != registered
            or fault.get("trigger") != f"first submission of {work}" or fault.get("released") != CLAIM):
        raise ValueError("private fault subject, trigger or claim differs from the registered intervention")

    before = [payload for _, payload in events[:position]]
    submits = [a for a in before if a.get("action") == "submit"]
    if any(a.get("subject") == work for a in submits):
        raise ValueError("intervention followed an earlier submission of the designated work")
    first_checks = [a for a in before if (a.get("action"), a.get("actor"), a.get("subject")) == ("check", "executor", work)]
    if (len(first_checks) != 1 or len(before) < 2 or before[-1] is not first_checks[0]
            or before[-1].get("input") != {"check": "public task checks"} or before[-1].get("candidate") is not None):
        raise ValueError("registered intervention is not at the first applicable worker check")
    edit = before[-2]
    if ((edit.get("action"), edit.get("actor"), edit.get("subject")) != ("edit", "executor", work)
            or edit.get("input") != {"work": work, "repair": []} or edit.get("return") != {"edited": work}):
        raise ValueError("first intervention check lacks its completed initial work edit")
    result = _worker_result(before[-1], variant == "skipped_retry_check")
    if position + 1 < len(events):
        # This is submission intent even when the adapter fails or its acknowledgment is lost.
        submit = events[position + 1][1]
        if ((submit.get("action"), submit.get("actor"), submit.get("subject")) != ("submit", "executor", work)
                or submit.get("candidate") != fault["candidate"]
                or submit.get("input") != {"candidate": fault["candidate"], "claim": CLAIM}):
            raise ValueError("first post-intervention submission differs in subject, candidate or public claim")
    previous = submits[-1].get("candidate") if submits else START
    if not _oid(previous):
        raise ValueError("previous sealed candidate is unavailable for the registered intervention")
    return fault, previous, registered, result


def validate_fault(journal, track, seed, rank):
    """Return the validated private fault, if reached. Public admission has separate evidence."""
    recorded = _recorded(journal, track, seed, rank)
    return recorded[0] if recorded is not None else None


def _entry(repo, candidates, oid):
    entry = candidates.get(oid)
    if not isinstance(entry, dict) or entry.get("start") != START or not _oid(entry.get("tree")):
        raise ValueError("intervention references an unavailable immutable candidate")
    expected = [oid, entry["tree"]]
    if oid != START:
        if not _oid(entry.get("parent")) or entry["parent"] not in candidates:
            raise ValueError("intervention candidate lacks its retained parent")
        expected.append(entry["parent"])
    elif entry.get("parent") is not None:
        raise ValueError("intervention fixture is not the registered root")
    actual = git(repo, "log", "-1", "--format=%H %T %P", oid, "--").decode().split()
    if actual != expected:
        raise ValueError("intervention commit, tree or parent differs from the retained Git object")
    return entry


def verify_fault_candidate(repo, candidates, journal, seed, rank, *, replay_check=False):
    """Replay the registered overlay on its actual predecessor from the fault's self-contained pack."""
    recorded = _recorded(journal, "primary", seed, rank)
    if recorded is None:
        return
    fault, previous, registered, result = recorded
    oid, work = fault["candidate"], fault["work"]
    try:
        entry = _entry(repo, candidates, oid)
        base = entry if oid == previous else _entry(repo, candidates, previous)
        git(repo, "merge-base", "--is-ancestor", START, previous)
        with tempfile.TemporaryDirectory(prefix="purser-e1-intervention-") as directory:
            # A separate index keeps the private pack read-only and preserves every earlier Git entry.
            git(directory, "clone", "-q", "--shared", "--no-checkout", "--", str(Path(repo).resolve()), "worker")
            root = Path(directory) / "worker"
            git(root, "checkout", "-q", "--detach", previous)
            implement(root, work)
            if replay_check and not same(local_check(root, work, skip=registered[0] == "skipped_retry_check"), result):
                raise ValueError("retained first worker check differs from its pre-intervention replay")
            inject(root, registered, seed)
            git(root, "add", "-f", "--all", "--", ".")
            expected_tree = git(root, "write-tree").decode().strip()
    except (OSError, subprocess.SubprocessError, MeasurementUnavailable) as error:
        raise ValueError("registered intervention cannot be verified from its retained Git pack") from error
    if entry["tree"] != expected_tree:
        raise ValueError("fault candidate tree differs from the registered intervention bytes")
    if expected_tree == base["tree"]:
        if oid != previous:
            raise ValueError("unchanged intervention must reuse the preceding commit")
    elif entry["parent"] != previous:
        raise ValueError("fault candidate is not the immediate successor of the preceding commit")
