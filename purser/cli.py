"""The trusted local owner's JSON command line."""

import argparse
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

from .kernel.store import Store
from .kernel.wire import KernelError, canonical, decode, format_time, parse_time
from .queries import status, why

ERROR_EXITS = {
    "invalid_input": 2, "not_found": 4, "bad_revision": 4, "id_conflict": 4,
    "inactive_assignment": 4, "unauthenticated": 5, "unavailable": 6,
    "integrity_error": 7, "unsupported_version": 7,
}


def emit(reply):
    sys.stdout.buffer.write(canonical(reply) + b"\n")


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise KernelError("invalid_input")

    def print_help(self, file=None):
        emit({"v": 1, "help": self.format_help()})


def arguments(argv):
    parser = Parser(prog="python -m purser", allow_abbrev=False)
    parser.add_argument("--db", required=True, metavar="PATH")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    proposal = commands.add_parser("propose", allow_abbrev=False)
    proposal.add_argument("file", metavar="FILE", help="UTF-8 JSON proposal, or - for stdin")
    driver = commands.add_parser("run", help="run one eligible local driver turn", allow_abbrev=False)
    driver.add_argument("--git-binding", metavar="FILE", help="attach the owner's registered local Git repository")
    context = commands.add_parser("context", allow_abbrev=False)
    context.add_argument("assignment_id", metavar="ASSIGNMENT")
    context.add_argument("--revision", type=int, metavar="N")
    context.add_argument("--as-of", type=parse_time, metavar="TIME")
    for name in ("status", "why", "frontier", "replay"):
        query = commands.add_parser(name, allow_abbrev=False)
        if name == "why":
            query.add_argument("record_id", metavar="ID")
        if name != "replay":
            query.add_argument("--revision", type=int, metavar="N")
        query.add_argument("--as-of", type=parse_time, metavar="TIME")
    return parser.parse_args(argv)


def local_store(path, initializing):
    try:
        db = Path(os.path.realpath(path))
        try:
            info = db.stat()
        except FileNotFoundError:
            if not initializing:
                raise KernelError("unavailable") from None
            info = db.parent.stat()
        else:
            if not stat.S_ISREG(info.st_mode):
                raise KernelError("unavailable")
        if info.st_uid != os.geteuid():
            raise KernelError("unauthenticated")
        return Store(db)
    except OSError as exc:
        raise KernelError("unavailable") from exc


def local_repository(path):
    from .effects.git import GitRepository

    try:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK), "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
                raise KernelError("unauthenticated")
            return GitRepository(decode(source.read()))
    except OSError as exc:
        raise KernelError("unavailable") from exc


def run(args):
    store = local_store(args.db, args.command == "init")
    if args.command == "init":
        snapshot = store.initialize()
        return 0, {"v": 1, "revision": snapshot["revision"], "head_hash": snapshot["head_hash"]}
    if args.command == "propose":
        from .kernel.gate import admit

        try:
            raw = sys.stdin.buffer.read() if args.file == "-" else Path(args.file).read_bytes()
        except OSError as exc:
            raise KernelError("invalid_input") from exc
        principal = "principal:local"
        binding = {
            "actor_id": principal, "role": "principal", "principal_id": principal,
            "commitment": None, "assignment": None,
        }
        receipt = admit(store, binding, decode(raw), clock=lambda: datetime.now(timezone.utc))
        return {"admitted": 0, "pending": 3, "rejected": 4}[receipt["disposition"]], {
            "v": 1, "receipt": receipt,
        }
    if args.command == "run":
        from .driver import tick

        if args.git_binding is None:
            result = tick(store)
        else:
            from .effects.git import GitError
            try:
                result = tick(store, repository=local_repository(args.git_binding))
            except GitError as exc:
                raise KernelError("unavailable", {"reason": exc.code}) from exc
        return (4 if result.get("receipt", {}).get("disposition") == "rejected" else 0), result
    as_of = format_time(args.as_of or datetime.now(timezone.utc))
    if args.command == "context":
        from .driver import context_pack
        return 0, context_pack(store.snapshot(revision=args.revision), args.assignment_id, as_of=as_of)
    if args.command == "replay":
        return 0, status(store.replay(), as_of)
    snapshot = store.snapshot(revision=args.revision)
    if args.command == "why":
        return 0, why(snapshot, as_of, args.record_id)
    if args.command == "frontier":
        response = status(snapshot, as_of)
        return 0, {"v": 1, "revision": snapshot["revision"], "head_hash": snapshot["head_hash"],
                   "as_of": as_of, "actions": response["answers"]["q10"]}
    return 0, status(snapshot, as_of)


def main(argv=None):
    try:
        exit_code, reply = run(arguments(argv))
    except KernelError as exc:
        exit_code = ERROR_EXITS[exc.code]
        reply = {"v": 1, "error": {"code": exc.code, "details": exc.details}}
    emit(reply)
    return exit_code
