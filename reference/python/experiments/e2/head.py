"""Fence a qualified premise against the public head under the participant's own writer."""

from contextlib import contextmanager
from contextvars import ContextVar

_gate_head = ContextVar("e2_qualified_kernel_head", default=None)


_expected_checkpoint = ContextVar("e2_notice_checkpoint", default=None)


def required():
    return _expected_checkpoint.get()


@contextmanager
def expect(arm, checkpoint):
    token = _expected_checkpoint.set(checkpoint)
    try:
        if "plan_sha256" in checkpoint:
            from .plan_versions import expect_head
            with expect_head(checkpoint["plan_sha256"]):
                yield
        else:
            gate = _gate_head.set((arm.store.path, checkpoint["revision"], checkpoint["head_hash"]))
            try:
                yield
            finally:
                _gate_head.reset(gate)
    finally:
        _expected_checkpoint.reset(token)


class QualifiedStore:
    def __init__(self, store, expected):
        self.store = store
        self.path = store.path
        self.expected = expected

    def snapshot(self, **args):
        return self.store.snapshot(**args)

    def verified_changes_since(self, revision):
        return self.store.verified_changes_since(revision)

    def transact(self, actor_id, proposal_id, clock, decide):
        from .plan_versions import HeadMoved
        def reserved(snapshot, at):
            if (self.path, snapshot["revision"], snapshot["head_hash"]) != self.expected:
                raise HeadMoved("the qualified kernel head changed")
            return decide(snapshot, at)
        return self.store.transact(actor_id, proposal_id, clock, reserved)


def for_notice(store):
    required = _gate_head.get()
    return QualifiedStore(store, required) if required is not None else store
