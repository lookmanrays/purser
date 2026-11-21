"""The immutable Git intent and the authority predicates shared by gate and queries."""
import re

from .rules import HASH, ID, candidate, matches
from .wire import KernelError, parse_time

CAPABILITY = "git.commit"
BROKER = "broker:git"
OBSERVER = "observer:git"
UNRESOLVED = ("dispatching", "pending")
INTENT = {"repository", "commitment", "work", "generation", "assignment", "slot", "effect",
          "capability", "resource", "expected_old", "target", "purpose", "grant"}
OBSERVATION = {"intent_digest", "repository_instance", "attempt", "state", "reason", "repository",
               "resource", "expected_old", "target", "branch_tip", "receipt_ref", "receipt_target",
               "writer_quiescent", "history_intact", "matching_transitions"}


def git_paths(value):
    return (type(value) is list and all(type(p) is str and "\0" not in p
            and all(part and part not in (".", "..") for part in p.split("/")) for p in value)
            and value == sorted(set(value)))


def allowed_paths(value):
    return git_paths(value) and bool(value)


def branch(value):
    return (type(value) is str and value.startswith("refs/heads/") and not value.endswith(".")
            and ".." not in value and "@{" not in value
            and all(part and not part.startswith(".") and not part.endswith(".lock") for part in value.split("/"))
            and not any(ord(c) <= 32 or ord(c) == 127 or c in "~^:?*[\\" for c in value))


def deadline(value):
    try:
        parse_time(value)
        return True
    except KernelError:
        return False


def intent_valid(intent):
    return (type(intent) is dict and intent.keys() == INTENT
            and all(matches(ID, intent[k]) for k in ("repository", "commitment", "work", "assignment",
                                                     "slot", "effect", "grant"))
            and type(intent["generation"]) is int and intent["generation"] >= 1
            and intent["capability"] == CAPABILITY and branch(intent["resource"])
            and type(intent["purpose"]) is str and bool(intent["purpose"].strip())
            and all(candidate(intent[k]) and intent[k]["kind"] == CAPABILITY
                    and int(intent[k]["digest"], 16) for k in ("expected_old", "target"))
            and intent["expected_old"]["algorithm"] == intent["target"]["algorithm"]
            and intent["expected_old"] != intent["target"])


def proof_valid(key, proof):
    if type(proof) is not dict:
        return False
    if key == "git_preparation":
        return (proof.keys() == {"intent_digest", "repository_instance", "changed_paths"}
                and git_paths(proof["changed_paths"])
                and all(matches(HASH, proof[k]) for k in ("intent_digest", "repository_instance")))
    return (proof.keys() == OBSERVATION and all(matches(HASH, proof[k]) for k in ("intent_digest", "repository_instance"))
            and matches(ID, proof["attempt"]) and matches(ID, proof["repository"]) and branch(proof["resource"])
            and proof["state"] in ("confirmed_happened", "confirmed_absent", "pending")
            and type(proof["reason"]) is str and bool(proof["reason"].strip()) and type(proof["receipt_ref"]) is str
            and all(type(proof[k]) is bool for k in ("writer_quiescent", "history_intact"))
            and (proof["matching_transitions"] is None or type(proof["matching_transitions"]) is int
                 and proof["matching_transitions"] >= 0)
            and all(type(proof[k]) is str and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", proof[k])
                    for k in ("expected_old", "target"))
            and all(proof[k] is None or type(proof[k]) is str and re.fullmatch(
                r"[0-9a-f]+", proof[k]) and len(proof[k]) == len(proof["target"])
                    for k in ("branch_tip", "receipt_target")))


def observes(report, fields, attempt):
    intent, checksum = fields["intent"], fields["intent_digest"]
    if (report["intent_digest"] != checksum or report["repository_instance"] != fields["repository_instance"]
            or report["attempt"] != attempt or report["receipt_ref"] != "refs/purser/receipts/" + checksum
            or any(report[k] != intent[k] for k in ("repository", "resource"))
            or any(report[k] != intent[k]["digest"] for k in ("expected_old", "target"))):
        return False
    if report["state"] == "pending":
        return True
    if not report["writer_quiescent"] or not report["history_intact"]:
        return False
    if report["state"] == "confirmed_absent":
        return (report["reason"] == "quiescent_no_application" and report["matching_transitions"] == 0
                and report["receipt_target"] is None and report["branch_tip"] == report["expected_old"])
    return (report["reason"] == "protected_receipt_verified" and report["matching_transitions"] == 1
            and report["receipt_target"] == report["target"] and report["branch_tip"] is not None)


def grant_state(grant, at):
    return "revoked" if grant["state"] == "revoked" else "expired" if at >= grant["expires_at"] else "active"


def live_authority(view, intent, measured, at):
    work = view.fields(intent["work"])
    slot = work.get("effect_slots", {}).get(intent["slot"])
    grant_row = view.records.get(intent["grant"])
    assignment = view.records.get(intent["assignment"])
    if (not slot or not grant_row or grant_row["kind"] != "AuthorityGrant" or not assignment
            or assignment["kind"] != "Assignment"):
        return "scope_mismatch"
    grant = grant_row["fields"]
    if (any(grant[k] != intent[k] for k in ("work", "slot", "assignment", "generation", "repository",
                                             "resource", "capability"))
            or grant_row["commitment"] != intent["commitment"] or grant["grantee"] != assignment["fields"]["executor"]
            or measured["repository_instance"] != slot["repository_instance"]
            or not set(measured["changed_paths"]) <= set(grant["paths"])):
        return "scope_mismatch"
    if (view.fields(intent["commitment"])["state"] != "active" or assignment["fields"]["state"] != "current"
            or work.get("assignment") != intent["assignment"] or work["generation"] != intent["generation"]
            or work["candidate"] != intent["target"]):
        return "inactive_assignment"
    if view.graph.issues(intent["work"]):
        return "invalidated_support"
    state = grant_state(grant, at)
    return None if state == "active" else "grant_" + state


def ref_attempts(view, fields):
    """Attempt history also versions a ref's exclusion set across a completed intervening attempt."""
    intent = fields["intent"]
    return [r for r in view.records.values() if r["kind"] == "EffectAttempt"
            and r["fields"]["resource"] == intent["resource"] and (
                r["fields"]["repository_instance"] == fields["repository_instance"]
                or r["fields"]["repository"] == intent["repository"])]


def dispatchable(view, fields, at):
    if fields["state"] not in ("ready", "confirmed_absent"):
        return "effect_unavailable"
    intent = fields["intent"]
    if view.fields(intent["work"])["effect_slots"][intent["slot"]]["effect"] != intent["effect"]:
        return "effect_unavailable"
    error = live_authority(view, intent, fields, at)
    if error:
        return error
    if any(r["fields"]["state"] in UNRESOLVED for r in ref_attempts(view, fields)):
        return "ref_unresolved"
    return None
