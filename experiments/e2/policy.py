"""Public task wording, ordinary plan and real gate adapters for the E2 participant."""

from experiments.e1 import observer
from experiments.e1.arms import Plan, self_review
from experiments.e1.artifacts import REGISTRATION, encoded
from experiments.e1.executor import PATHS, TASKS, implement, local_check
from experiments.e1.measurement import probe, same
from experiments.e1.purser_arm import OBSERVER, Treatment

LINEAR = "linear"
EXPONENTIAL = "exponential"
CHOICES = [LINEAR, EXPONENTIAL]
OLD = "Use zero, then k times base at attempt index k."
NEW = "Use zero, then base times two to the power k minus one at attempt index k."
ASSUMPTION = "A1"
ORIGINAL = "D-retry-linear"
REPLACEMENT = "D-retry-exponential"
OBSERVATION = "Obs-A1-changed"
TASK_TITLES = {**TASKS, "W2": "Schedule every requested attempt under the current retry decision, beginning at zero."}


def notice(seed):
    return {"id": OBSERVATION, "seed": seed, "assumption": ASSUMPTION, "finding": "contradicts",
            "criterion": "W2/retry", "retry_schedule": EXPONENTIAL, "statement": NEW}


def decision(identity, choice, support, supersedes):
    return {"op": "decision.record", "id": identity, "choice": choice, "alternatives": CHOICES,
            "rationale": OLD if choice == LINEAR else NEW, "support": support, "triggers": support,
            "supersedes": supersedes}


class PlanArm(Plan):
    def __init__(self, directory):
        super().__init__(directory, tasks=TASK_TITLES)
        self.update("W2", retry_choice=LINEAR, criterion="W2/retry", premise=None)

    def receive(self, message):
        return self.update("W2", premise=message)

    def revise(self, message):
        row = self.context("W2")
        if row["premise"] != message or row["criterion"] != message["criterion"]:
            raise ValueError("plan has no matching new requirement")
        return self.update("W2", retry_choice=message["retry_schedule"])

    @staticmethod
    def choice(context):
        return context["retry_choice"]


class KernelArm(Treatment):
    def __init__(self, directory, definitions, environment):
        super().__init__(directory, definitions, environment, before_assignments=self.setup_decision, tasks=TASK_TITLES)

    @staticmethod
    def setup_decision(arm):
        arm.send("planner", [{"op": "assumption.create", "id": ASSUMPTION,
                               "statement": OLD, "observer": OBSERVER},
                              decision(ORIGINAL, LINEAR, [ASSUMPTION], None),
                              {"op": "dependency.add", "id": "W2", "support": ORIGINAL}])

    def receive(self, message):
        return self.send("observer", [{"op": "assumption.observe", "id": message["id"],
                                       "assumption": message["assumption"], "finding": message["finding"]}],
                         assignment=ASSUMPTION, premise={"artifact": encoded(message)})

    def revise(self, message):
        return self.send("planner", [decision(REPLACEMENT, message["retry_schedule"], [], ORIGINAL)])

    @staticmethod
    def choice(context):
        current, = context["decisions"]
        return current["choice"]


def edit(root, work, phase):
    if work != "W2" or phase == LINEAR:
        implement(root, work)
        return
    if phase != EXPONENTIAL:
        raise ValueError("task has no usable retry decision")
    path = PATHS[work]
    source = (REGISTRATION / "input" / path).read_text()
    (root / path).write_text(source.rsplit("    return ", 1)[0]
                             + "    return (0,) + tuple(base * 2 ** (k - 1) for k in range(1, attempts))\n")


def public_retry(root, phase, *, composed=False):
    # Fixed public examples are deliberately separate from the seeded protected scorer.
    times = [0, 3, 6, 9, 12, 13]
    last = 9 if phase == LINEAR else 12
    ids = ["first", "next", "first"] if composed else ["first"]
    emitted = ["first", "next"] if composed else ["first"]
    case = {"channel": "local", "ids": ids, "disabled": [], "attempts": 4, "base": 3, "times": times}
    expected = [{"enqueued": 4 * len(emitted), "pending": 0,
                 "samples": [[[item, "local", t * 1_000_000] for item in emitted] if t in (0, 3, 6, last) else []
                             for t in times]}]
    return case, probe(root, cases=[case]), expected


def worker_check(root, work, phase):
    result = local_check(root, work)
    if work == "W2":
        _, actual, expected = public_retry(root, phase)
        result.update(schedule_check=actual, **{"pass": result["pass"] and same(actual, expected)})
        result["worker_claim"] = "done, tests pass" if result["pass"] else "retry check failed"
    return result


def review(repo, oid, root, work, phase):
    result = self_review(repo, oid, root, work)
    if work in ("W2", "W-notify"):
        label = "W2/retry" if work == "W2" else "queue/composition"
        _, actual, expected = public_retry(root, phase, composed=work == "W-notify")
        result["retry_check"] = actual
        if not same(actual, expected) and label not in result["missing"]:
            result["missing"].append(label)
    return result


def inspect(repo, oid, root, label, phase):
    if label not in ("W2/retry", "queue/composition"):
        return observer.inspect(repo, oid, root, label)
    case, actual, expected = public_retry(root, phase, composed=label == "queue/composition")
    return {"criterion": label, "version": 1, "candidate": oid, "schedule": phase,
            "input": case, "observed": actual, "expected": expected}
