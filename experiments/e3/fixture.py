"""Prepare the registered Git objects and provision one fresh, scoped study session."""

import json
import subprocess

from experiments.e1.artifacts import Candidates, FIXTURE, MANIFEST, START, encoded, git
from experiments.e1.executor import ORDER, implement
from experiments.e1.recovery import write_atomic
from purser.effects.git import GitRepository
from purser.kernel.wire import digest

E3 = MANIFEST["experiments"]["E3"]
TARGET = E3["prepared_candidate"]["commit_sha1"]
WORK = MANIFEST["units"]["scored_parent"]
SCOPE = MANIFEST["units"]["commitment"]
RESOURCE = "refs/heads/notify"
EXECUTOR = "executor:script"
PRINCIPAL = "principal:local"
ASSIGNMENT = "AS-notify"
TASK = "Publish the prepared notification policy once on the allowed local branch. Resolve an uncertain acknowledgment."
EXPIRES = "2099-01-01T00:00:00.000000Z"


def read(path):
    return json.loads(path.read_bytes())


def write(path, value):
    write_atomic(path, encoded(value))


def candidate(oid):
    return {"kind": "git.commit", "algorithm": "sha1", "digest": oid}


def intent_for(seed):
    return {"repository": "R-notify", "resource": RESOURCE, "expected_old": candidate(START),
            "target": candidate(TARGET), "purpose": TASK, "work": WORK, "commitment": SCOPE,
            "assignment": ASSIGNMENT, "generation": 1, "slot": "publish", "effect": f"notify-{seed}",
            "grant": "G-notify", "capability": "git.commit"}


def envelope(seed):
    intent = intent_for(seed)
    grant = {"id": intent["grant"], "work": WORK, "slot": intent["slot"], "repository": intent["repository"],
             "resource": RESOURCE, "paths": sorted(FIXTURE["public_paths"]), "assignment": ASSIGNMENT,
             "generation": 1, "grantee": EXECUTOR, "expires_at": EXPIRES}
    return {"task": TASK, "intent": intent, "grant": grant,
            "receipt_ref": "refs/purser/receipts/" + digest("effect", intent)}


def provision(directory, assets, arm, seed, checkpoint):
    for name in ("calls", "declarations", "writers", "native"):
        (directory / name).mkdir()
    candidates = Candidates(directory, assets)
    checkpoint(candidates.entries)
    for work in ORDER:
        implement(candidates.work, work)
    prepared = E3["prepared_candidate"]
    if (candidates.commit(prepared["commit_message"]) != TARGET
            or candidates.entries[TARGET]["tree"] != prepared["tree_sha1"]):
        raise ValueError("prepared E3 candidate differs from the registered commit or tree")
    checkpoint(candidates.entries)
    packet = envelope(seed)
    intent = packet["intent"]
    repository = GitRepository.initialize(directory / "canonical.git", intent["repository"], RESOURCE,
                                          candidates.archive, START, candidates=(TARGET,))
    write(directory / "task.json", packet)
    write(directory / "binding.json", repository.binding)
    if arm == "purser":
        from .treatment import enroll
        enroll(directory, packet, repository)
    return packet, repository


def recover_candidates(directory, assets, recorded):
    """Recover reachable, registered objects from the original run if its checkpoint was interrupted."""
    recovered = dict(recorded)
    for oid, tree, parent in ((START, FIXTURE["tree_sha1"], None), (TARGET, E3["prepared_candidate"]["tree_sha1"], START)):
        if oid in recovered:
            continue
        for repo in (directory / "private-objects", directory / "canonical.git", directory / "worker" / ".git"):
            if not repo.is_dir():
                continue
            try:
                parents = git(repo, "rev-list", "--parents", "-n", "1", oid).decode().split()
            except subprocess.CalledProcessError:
                continue
            if (parents != [oid] + ([parent] if parent else [])
                    or git(repo, "rev-parse", oid + "^{tree}").decode().strip() != tree):
                raise ValueError("registered object has inconsistent native identity")
            pack = git(repo, "pack-objects", "--stdout", "--revs", data=(oid + "\n").encode())
            recovered[oid] = {"tree": tree, "parent": parent, "start": START, "pack": assets.put(pack, "pack")}
            break
    return recovered
