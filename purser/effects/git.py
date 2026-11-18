"""One foreground broker and an independent observer for local Git effects."""
from contextlib import contextmanager
from copy import deepcopy

from ..kernel.rules import HASH, ID, candidate, matches
from ..kernel.wire import canonical, decode, digest
from ._git_store import GitError, initialize, lease, oid, require, validate_binding


def _intent(value):
    frozen = decode(canonical(value))
    ids = ("repository", "commitment", "work", "assignment", "slot", "effect", "grant")
    required = {*ids, "capability", "generation", "resource", "expected_old", "target", "purpose"}
    require(type(frozen) is dict and frozen.keys() == required and all(matches(ID, frozen[k]) for k in ids)
            and frozen["capability"] == "git.commit" and type(frozen["generation"]) is int
            and frozen["generation"] > 0 and type(frozen["purpose"]) is str and frozen["purpose"]
            and type(frozen["resource"]) is str and frozen["resource"].startswith("refs/heads/")
            and all(candidate(frozen[k]) and frozen[k]["kind"] == "git.commit" for k in ("expected_old", "target")),
            "invalid_git_intent")
    return frozen


def _report(binding, intent, attempt):
    checksum = digest("effect", intent)
    return {"intent_digest": checksum, "repository_instance": binding["repository_instance"], "attempt": attempt,
            "state": "pending", "reason": "observation_incomplete", "repository": intent["repository"],
            "resource": intent["resource"], "expected_old": intent["expected_old"]["digest"],
            "target": intent["target"]["digest"], "branch_tip": None,
            "receipt_ref": "refs/purser/receipts/" + checksum, "receipt_target": None,
            "writer_quiescent": False, "history_intact": False, "matching_transitions": None}


def _scope(binding, intent):
    require(intent["repository"] == binding["repository"] and intent["resource"] == binding["resource"],
            "repository_resource_mismatch")
    require(all(intent[k]["algorithm"] == binding["algorithm"] for k in ("expected_old", "target")),
            "object_format_mismatch")


def _history(native, binding, report):
    """Check retained native branch/receipt history from the unique bootstrap."""
    branch = binding["resource"]
    tip = report["branch_tip"] = native.ref(branch)
    report["receipt_target"] = native.ref(report["receipt_ref"])
    require(tip is not None, "canonical_branch_missing")
    entries = native.log(branch)
    anchor = (binding["initial_oid"], "purser.init " + binding["repository_instance"])
    require(entries and entries[0] == anchor and entries[-1][0] == tip, "branch_history_incomplete")
    releases = {}
    previous = anchor[0]
    for value, message in entries[1:]:
        require(oid(value, binding["algorithm"]) and previous != value and native.ancestor(previous, value),
                "unexpected_branch_transition")
        # A separate legitimate fast-forward can advance this private branch.
        # Only the broker's actual native transaction carries its intent/attempt.
        if message.startswith("purser."):
            fields = message.split(" ")
            require(len(fields) == 3 and fields[0] == "purser.effect"
                    and matches(HASH, fields[1]) and matches(ID, fields[2]), "unknown_branch_writer")
            checksum, attempt = fields[1:]
            require(checksum not in releases, "duplicate_intent_transition")
            releases[checksum] = (attempt, previous, value, message)
        previous = value

    checksum = report["intent_digest"]
    release = releases.get(checksum)
    report["matching_transitions"] = int(release is not None and release[0] == report["attempt"])
    prefix = "refs/purser/receipts/"
    raw = native.output("for-each-ref", "--format=%(refname)", prefix)
    present = {line.decode("ascii") for line in raw.splitlines()}
    require(present == {prefix + key for key in releases}, "receipt_history_inconsistent")
    for key, (_, _, value, message) in releases.items():
        ref = prefix + key
        receipt = report["receipt_target"] if key == checksum else native.ref(ref)
        require(receipt == value, "receipt_target_mismatch")
        require(native.log(ref) == [(value, message)], "receipt_history_inconsistent")
    report["history_intact"] = True
    return release


def _inspect(native, binding, intent, report):
    _scope(binding, intent)
    report["writer_quiescent"] = True
    old, target = report["expected_old"], report["target"]
    native.object_is_commit(old)
    native.object_is_commit(target)
    require(old != target and native.ancestor(old, target), "invalid_candidate_ancestry")
    release = _history(native, binding, report)
    if release is None:
        require(report["receipt_target"] is None, "receipt_history_inconsistent")
        require(report["branch_tip"] == old, "branch_precondition_changed")
        report.update(state="confirmed_absent", reason="quiescent_no_application")
        return
    attempt, before, after, _ = release
    require(report["attempt"] is not None, "receipt_already_exists")
    require(attempt == report["attempt"], "receipt_attempt_mismatch")
    require((before, after) == (old, target), "receipt_transition_mismatch")
    report.update(state="confirmed_happened", reason="protected_receipt_verified")


class GitRepository:
    """An independently reopenable launcher binding to one private bare repository."""

    @classmethod
    def initialize(cls, git_dir, repository_id, branch, source_git_dir, initial_oid, *, candidates=()):
        binding = initialize(git_dir, repository_id, branch, source_git_dir, initial_oid, candidates)
        return cls(binding)

    def __init__(self, binding):
        self._binding = deepcopy(binding)
        validate_binding(self._binding)

    @property
    def binding(self):
        return deepcopy(self._binding)

    @contextmanager
    def broker(self):
        with lease(self._binding, write=True) as native:
            broker = _Broker(self._binding, native)
            try:
                yield broker
            finally:
                broker._native = None

    def observe(self, intent, attempt):
        """Read native evidence under a new, nonblocking, shared fence. No writes."""
        require(matches(ID, attempt), "invalid_attempt")
        intent = _intent(intent)
        report = _report(self._binding, intent, attempt)
        try:
            _scope(self._binding, intent)
            with lease(self._binding, write=False) as native:
                _inspect(native, self._binding, intent, report)
        except GitError as exc:
            report["reason"] = exc.code
        except UnicodeError:
            report["reason"] = "history_unreadable"
        return report


class _Broker:
    def __init__(self, binding, native):
        self._binding, self._native = binding, native

    def prepare(self, intent):
        """Derive actual changed paths only for a fresh, physically admissible update."""
        require(self._native is not None, "broker_lease_ended")
        intent = _intent(intent)
        report = _report(self._binding, intent, None)
        try:
            _inspect(self._native, self._binding, intent, report)
        except UnicodeError as exc:
            raise GitError("history_unreadable") from exc
        paths = self._native.changed_paths(report["expected_old"], report["target"])
        return {"intent_digest": report["intent_digest"], "repository_instance": report["repository_instance"],
                "changed_paths": paths}

    def dispatch(self, intent, attempt, authorize, *, after_authorization=None, after_write=None):
        """Release Git only after this invocation's matching cutoff has committed."""
        require(matches(ID, attempt), "invalid_attempt")
        intent = _intent(intent)
        facts = self.prepare(intent)
        checksum = facts["intent_digest"]
        receipt, fresh = authorize(facts)
        if fresh is False:
            return receipt, False
        expected = {"effect": intent["effect"], "attempt": attempt, "intent_digest": checksum, "state": "dispatching"}
        require(fresh is True and type(receipt) is dict and receipt.get("disposition") == "admitted"
                and receipt.get("details") == expected, "authorization_mismatch")
        if after_authorization is not None:
            after_authorization()
        require(self._native is not None, "broker_lease_ended")
        branch, old, target = intent["resource"], intent["expected_old"]["digest"], intent["target"]["digest"]
        ref = "refs/purser/receipts/" + checksum
        commands = f"start\nupdate {branch} {target} {old}\ncreate {ref} {target}\nprepare\ncommit\n"
        self._native.run("update-ref", "--no-deref", "--stdin", "--create-reflog", "-m",
                         f"purser.effect {checksum} {attempt}", data=commands.encode("utf-8"))
        if after_write is not None:
            after_write()
        return receipt, True
