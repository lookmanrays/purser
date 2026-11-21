"""Common worker policy and the controlled E1 submission faults."""

from .artifacts import FIXTURE, REGISTRATION
from .measurement import probe, same

ORDER = ("W1", "W3", "W2")
PATHS = dict(zip(("W1", "W2", "W3"), FIXTURE["public_paths"], strict=True))
RETURNS = {"W1": "channel not in disabled", "W2": "tuple(k * base for k in range(attempts))",
           "W3": "tuple(dict.fromkeys(event_ids))"}
LEAVES = {"W1": "W1/preferences", "W2": "W2/retry", "W3": "W3/delivery"}
PARENT = "W-notify"
PARENT_CHECKS = ("scope/paths", "legacy/compat", "queue/composition")
TASKS = {
    "W1": "Disable delivery on disabled channels and allow other channels.",
    "W2": "Schedule every requested attempt at offsets k times base, beginning at zero.",
    "W3": "Keep the first occurrence of each event ID in input order within each submitted batch.",
    PARENT: "Integrate the three policies, retain compatibility and restrict edits to the three policy files.",
}


def implement(worktree, work):
    file = PATHS[work]
    start = (REGISTRATION / "input" / file).read_text()
    (worktree / file).write_text(start.rsplit("    return ", 1)[0] + "    return " + RETURNS[work] + "\n")


def local_check(root, work, skip=False, *, run_probe=probe):
    legacy = run_probe(root, legacy=True)
    ok = legacy.get("tests") == 28 and not any(legacy.get(k) for k in ("failures", "errors", "skipped"))
    if skip or work not in LEAVES:
        return {"pass": ok, "legacy": legacy, "worker_claim": "done, tests pass" if ok else "check failed"}
    case = {"channel": "inbox", "ids": ["one"], "disabled": [], "attempts": 1, "base": 2, "times": [0, 2]}
    if work == "W1":
        case["disabled"] = ["inbox"]
    if work == "W2":
        case["attempts"] = 2
    if work == "W3":
        case["ids"] = ["one", "one"]
    actual = run_probe(root, cases=[case])
    expected = {"W1": 0, "W2": 2, "W3": 1}[work]
    ok = ok and isinstance(actual, list) and same(actual[0].get("enqueued"), expected)
    return {"pass": ok, "legacy": legacy, "extended": actual,
            "worker_claim": "done, tests pass" if ok else "check failed"}


def fault_for(seed, track):
    if track == "clean":
        return None
    j = seed - 1001
    if j % 3 == 0:
        return ("skipped_retry_check", "W2", LEAVES["W2"])
    if j % 3 == 1:
        return ("outside_scope", "W1", "scope/paths")
    work = ("W1", "W2", "W3")[(j // 3) % 3]
    return ("unchanged_module", work, LEAVES[work])


def inject(root, fault, seed):
    variant, work, _ = fault
    if variant == "outside_scope":
        outside = root / "notes" / f"seed-{seed}.txt"
        outside.parent.mkdir(exist_ok=True)
        outside.write_bytes(b"out of scope\n")
    else:
        file = PATHS[work]
        original = (REGISTRATION / "input" / file).read_bytes()
        if variant == "skipped_retry_check":
            original = original.replace(b"return (0,)", b"return (0,) + tuple(base * k for k in range(1, attempts - 1))")
        (root / file).write_bytes(original)
