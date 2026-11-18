"""Original native Git and process evidence, independent of the arm's observer."""

import os
from pathlib import Path
import time

from experiments.e1.artifacts import git
from .fixture import read, write


def native(directory, binding, receipt_ref, label):
    location = Path(binding["git_dir"])
    started = time.monotonic_ns()
    text = git(location, "for-each-ref", "--format=%(refname)%09%(objectname)",
               "refs/heads/", "refs/purser/receipts/").decode()
    refs = dict(line.split("\t") for line in text.splitlines())

    def log(name):
        if name not in refs:
            return ""
        return git(location, "reflog", "show", "--format=%H%x09%gs", "--no-color", name, "--").decode()

    facts = {"repository_instance": binding["repository_instance"], "branch": binding["resource"],
             "receipt_ref": receipt_ref, "started_ns": started, "refs": text,
             "branch_log": log(binding["resource"]), "receipt_log": log(receipt_ref),
             "finished_ns": time.monotonic_ns()}
    write(directory / "native" / (label + ".json"), facts)
    return facts


def transitions(snapshot):
    rows = [line.split("\t", 1) for line in reversed(snapshot["branch_log"].splitlines())]
    return [{"old": previous[0], "new": current[0], "message": current[1]}
            for previous, current in zip(rows, rows[1:])]


def note(folder, name, **fields):
    data = {"event": name, "at_ns": time.monotonic_ns(), "recorder_pid": os.getpid(), **fields}
    write(folder / (name + ".json"), data)
    return data


def writer_history(folder):
    return sorted((read(path) for path in folder.glob("*.json") if path.name != "command.json"),
                  key=lambda value: value["at_ns"])
