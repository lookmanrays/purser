"""Join assignment-bound requests, the durable cutoff and independent Git reads."""
from datetime import datetime, timezone

from ..kernel import effectops, effectpolicy
from ..kernel.gate import PRINCIPAL, admit, authorize_attempt, envelope, redelivery
from ..kernel.rules import ID, matches
from ..kernel.wire import KernelError
from ..kernel.workview import WorkView
from .git import GitError


def _now():
    return datetime.now(timezone.utc)


def _single(proposal, name):
    proposal = envelope(proposal)
    ops = proposal["ops"]
    if len(ops) != 1 or not isinstance(ops[0], dict) or ops[0].get("op") != name:
        raise KernelError("invalid_input")
    op = ops[0]
    if (op.keys() != {"op", *effectops.OPS[name].split()}
            or any(not matches(ID, op[k]) for k in ("id", "effect", "attempt") if k in op)):
        raise KernelError("invalid_input")
    return proposal, op


def _binding(actor, role, scope, subject):
    return {"actor_id": actor, "role": role, "principal_id": PRINCIPAL, "commitment": scope, "assignment": subject}


def _persisted_intent(store, scope, effect):
    row = WorkView(store.snapshot()["projection"]).records.get(effect)
    if not row or row["kind"] != "Effect" or row["commitment"] != scope:
        raise KernelError("not_found")
    if "intent" not in row["fields"]:
        raise KernelError("effect_unavailable")
    return row["fields"]["intent"]


def request_effect(store, repository, executor_binding, proposal, *, clock=_now):
    """Receive one executor request. The Git measurement belongs to this launcher."""
    proposal, op = _single(proposal, "effect.request")
    if (not isinstance(executor_binding, dict) or executor_binding.get("role") != "executor"
            or executor_binding.keys() != {"actor_id", "role", "principal_id", "commitment", "assignment"}):
        raise KernelError("unauthenticated")
    binding = dict(executor_binding)
    prior = redelivery(store, binding, proposal)
    if prior is not None:
        return prior

    def needs_measurement():
        view = WorkView(store.snapshot()["projection"])
        row = view.records.get(op["id"])
        return (effectpolicy.intent_valid(op["intent"]) and op["intent"]["effect"] == op["id"]
                and op["intent"]["commitment"] == proposal["commitment"] == binding["commitment"]
                and effectops.bound(op, binding) and effectops.executor_access(view, binding)
                and (row is None or row["kind"] == "Effect" and "intent" not in row["fields"]))

    if needs_measurement():
        try:
            with repository.broker() as broker:
                binding["git_preparation"] = broker.prepare(op["intent"])
        except GitError:
            # A concurrent original may have bound or published while we opened Git.
            prior = redelivery(store, binding, proposal)
            if prior is not None:
                return prior
            if needs_measurement():
                raise
    return admit(store, binding, proposal, clock=clock)


def dispatch_effect(store, repository, proposal, *, clock=_now, after_authorization=None, after_write=None):
    """Return the cutoff receipt and whether this invocation actually issued a write."""
    proposal, op = _single(proposal, "effect.authorize")
    scope, effect = proposal["commitment"], op["effect"]
    binding = _binding(effectpolicy.BROKER, "broker", scope, effect)
    prior = redelivery(store, binding, proposal)
    if prior is not None:
        return prior, False
    intent = _persisted_intent(store, scope, effect)
    won_cutoff = False

    def authorize(facts):
        nonlocal won_cutoff
        receipt, won_cutoff = authorize_attempt(store, {**binding, "git_preparation": facts}, proposal, clock=clock)
        return receipt, won_cutoff

    try:
        with repository.broker() as broker:
            return broker.dispatch(intent, op["id"], authorize, after_authorization=after_authorization,
                                   after_write=after_write)
    except GitError:
        if not won_cutoff:
            prior = redelivery(store, binding, proposal)
            if prior is not None:
                return prior, False
        raise


def reconcile_effect(store, repository, proposal, *, clock=_now):
    """Take an independent read of the recorded Git intent and admit its observation."""
    proposal, op = _single(proposal, "effect.reconcile")
    scope, attempt = proposal["commitment"], op["attempt"]
    binding = _binding(effectpolicy.OBSERVER, "observer", scope, attempt)
    prior = redelivery(store, binding, proposal)
    if prior is not None:
        return prior
    intent = _persisted_intent(store, scope, op["effect"])
    facts = repository.observe(intent, attempt)
    return admit(store, {**binding, "git_observation": facts}, proposal, clock=clock)
