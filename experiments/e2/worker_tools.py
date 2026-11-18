"""Real, scoped worker tools. Their implementation reads are inside the admitted request."""
from contextlib import nullcontext
from pathlib import Path

from experiments.e1.executor import ORDER, PARENT

from . import git_reads, policy
from .actions import Delivery, local_receiver


class WorkerTools:
    """A bound outward handle carries the run's issuer across copies and worker roles."""
    def __init__(self, actions, participant, candidates):
        self._actions, self._participant, self._candidates = actions, participant, candidates

    def _run(self, kind, work, actor, inputs, operation, oid=None):
        def perform():
            self._actions.start_work()
            return operation()
        return self._actions.call(kind, work, actor, inputs, perform, oid)

    def context(self, work, *, actor="executor", receiver=local_receiver):
        return self._participant.context(work, actor=actor, receiver=receiver)

    def schedule(self):
        return self._run("plan", PARENT, "planner", {"schedule": list(ORDER)}, lambda: {"work_order": list(ORDER)})

    def edit(self, work, phase):
        def perform():
            policy.edit(self._candidates.work, work, phase)
            return {"edited": work}
        return self._run("edit", work, "executor", {"work": work, "schedule": phase}, perform)

    def check(self, work, phase, *, candidate=None, pass_only=False):
        def perform():
            tree = self._candidates.export(candidate) if candidate is not None else nullcontext(self._candidates.work)
            with tree as root:
                report = policy.worker_check(Path(root), work, phase)
            return report["pass"] if pass_only else report
        return self._run("check", work, "executor", {"check": "public task checks", "schedule": phase}, perform, candidate)

    def review(self, work, candidate, phase, root):
        return self._run("check", work, "plan_reviewer", {"check": "review submitted candidate", "schedule": phase},
                         lambda: policy.review(self._candidates.archive, candidate, root, work, phase), candidate)

    def inspect(self, tool, work=PARENT, *, actor="planner", candidate=None, receiver=None):
        """Inspect the real public plan, local Git, or kernel query for the stated subject."""
        from purser.queries import status, why
        inputs = {"tool": tool, "work": work}
        def inspect_source():
            if tool in ("status", "replay", "frontier") and work != PARENT:
                raise ValueError("a workspace-wide query has the parent subject")
            if tool == "plan":
                rows, head = self._participant.public_view()
                value = rows[work]
                packet = {k: v for k, v in value.items() if k not in ("_publication", policy.plan_versions.KEY)}
                if isinstance(self._participant, policy.PlanArm):
                    from experiments.e1.capture import checkpoint
                    action = self._actions.current
                    assets = self._actions.driver.assets
                    action["context_source"] = checkpoint(assets, rows, head)
                    action["prepared_context"] = assets.put(packet, "json")
                    self._actions.driver.j.persist()
                return packet
            if tool in git_reads.TOOLS:
                oid = candidate or self._candidates.head
                if oid not in self._candidates.entries:
                    raise ValueError("unknown study candidate")
                value = git_reads.result(self._candidates.archive, tool, work, oid)
                git_reads.record(self._actions, oid, value, default_receiver=receiver is None)
                return value
            if tool not in ("status", "why", "frontier", "replay") or not isinstance(self._participant, policy.KernelArm):
                raise ValueError("tool unavailable to this participant")
            store = self._participant.store
            snap = store.replay() if tool == "replay" else store.snapshot()
            as_of = "2026-01-01T00:00:00.000000Z"
            if tool == "why":
                return why(snap, as_of, work)
            result = status(snap, as_of)
            return result["answers"]["q10"] if tool == "frontier" else result

        def perform():
            value = inspect_source()
            witness = Delivery(self._actions, self._actions.current)
            if receiver is not None:
                return receiver(value, witness)
            return self._receive(work, value, witness)
        return self._run("context", work, actor, inputs, perform, candidate)

    @staticmethod
    def _receive(subject, result, witness):
        witness.received(subject, result)
        return result
