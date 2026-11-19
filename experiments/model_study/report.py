"""Cold, provider-free comparison over the prospective population and original case artifacts."""

from collections import Counter
from pathlib import Path

from experiments.e1.artifacts import MANIFEST, REGISTRATION, sha
from experiments.e1.measurement import same
from experiments.e1.report import Objects, parse, require, wire_valid
from experiments.e1.recovery import FileArchive, input_identity
from experiments.e2.outcomes import conjunction

from experiments.model_costs import FIELDS as COST, ModelCaseCost, render_model_costs

from .collector import E1_FIELDS, E2_FIELDS, case_identity, case_path, coding_score, e3_args
from .registration import ARMS, load_registration, schedule

OUTCOMES = {"E1": E1_FIELDS, "E2": E2_FIELDS,
            "E3": ("correct_reconciliation", "duplicate_count", "effect_count", "resolved_by_budget",
                   "stuck_requires_manual", "manual_repair")}
E3_EXTRAS = ("original_truth", "original_report", "justified_in_order", "uncertainty_remaining")
LABELS = {"complete": "complete", "arm_incomplete": "incomplete", "infrastructure_invalid": "invalid"}


class Archive(FileArchive):
    """Cold entry point also works when the evidence tree is mounted read-only."""
    def __init__(self, directory):
        self.directory = directory
        self.objects = directory / "objects"


def inspect(directory):
    from . import readback
    from .session import read_session
    directory = Path(directory)
    frozen, wire = load_registration(directory)
    cases = {}
    units = list(schedule())
    known = {case_path(directory, u) for u in units}
    for path in (directory / "cases").glob("*/*/*/*"):
        require(path in known, "unexpected case outside the frozen population")
    schema = parse((REGISTRATION / "raw.schema.json").read_bytes())
    for unit in units:
        path = case_path(directory, unit)
        if not path.exists():
            continue
        identity = case_identity(unit, frozen, wire)
        archive = Archive(path)
        objects = Objects(archive)
        exact, lower, facts, raw = {}, {}, {}, {}
        try:
            require(parse((path / "started.json").read_bytes()) == identity, "invalid original start marker")
            trace_ref = parse((path / "session.json").read_bytes())
            records, exact, lower = read_session(objects.item(trace_ref), archive, frozen, identity)
            raw = parse((path / "raw.json").read_bytes())
            exp, track, seed, arm = unit
            require(wire_valid(raw, schema, schema) and raw["mode"] == "model" and raw["suite"] == frozen["suite"] and
                    [raw[k] for k in ("experiment", "track", "seed", "arm")] == list(unit) and
                    raw["replicate"] == frozen["replicate"] and raw["inputs"] == input_identity(frozen, arm),
                    "original row differs from registered identity or M0 raw schema")
            if exp == "E3":
                from experiments.model_e3 import reconstruct_case
                facts = reconstruct_case(raw, archive, **e3_args(frozen, identity),
                                         service_sha256=frozen["conditions"]["authority"]["service_sha256"],
                                         authority_sha256=frozen["conditions"]["authority"]["authority_sha256"])
                for k, value in facts["cost_exact"].items():
                    if k in exact and exact[k] is not None:
                        require(exact[k] == value, "E3 cost differs from shared original collector evidence")
                    exact[k] = value
                lower.update(facts["cost_lower_bounds"])
            else:
                require(raw["artifacts"]["model"] == trace_ref, "model trace differs from original session")
                facts = readback.reconstruct_case(raw, archive, records, registration_sha256=identity["registration_sha256"])
                for k, partial in (("elapsed_ns", "elapsed_lower_ns"), ("steps", "observed_steps")):
                    exact[k] = facts[k]
                    lower[k] = facts[partial]
                require(same(raw["score"], coding_score(raw, facts)), "published score differs from original evidence")
            for k, value in exact.items():
                if value is not None:
                    require(same(value, raw["cost"][k]), "published cost differs from original " + k)
            platform = objects.item(raw["artifacts"]["platform"])
            problem = platform.get("failure") or facts.get("error")
            trace = objects.item(trace_ref)
            errors = [t.get("outcome", "interrupted model request") for t in trace["turns"] if t.get("outcome") != "decision"]
            if problem:
                errors.append(str(problem))
            cases[unit] = {"facts": facts, "terminal": raw["terminal"], "errors": errors,
                           "cost": ModelCaseCost(raw["validity"], exact, lower)}
        except (OSError, ValueError, KeyError, TypeError, IndexError, ImportError) as error:
            cases[unit] = {"facts": {}, "terminal": "unavailable original evidence",
                           "errors": [f"{type(error).__name__}: {error}"],
                           "cost": ModelCaseCost("infrastructure_invalid", exact, lower)}
    return frozen, cases


def show(value):
    if value is None:
        return "?"
    if type(value) is bool:
        return "yes" if value else "no"
    return str(value).replace("|", "&#124;").replace("\n", " ")


def _at_least(values, target):
    count, unknown = sum(v is True for v in values), values.count(None)
    return True if count >= target else False if count + unknown < target else None


def _all(values):
    return conjunction(values)


def field(cases, unit, name):
    case = cases.get(unit)
    if not case:
        return None
    if name in COST:
        return case["cost"].exact.get(name)
    if case["cost"].validity == "infrastructure_invalid":
        return None
    return case["facts"].get(name)


def column(cases, exp, arm, name):
    result = []
    for seed in MANIFEST["experiments"][exp]["seeds"]:
        unit = exp, "primary", seed, arm
        case = cases.get(unit)
        eligible = case and case["facts"].get("eligible") is True
        result.append(field(cases, unit, name) if eligible or name == "intervention_reached" else None)
    return result


def predicates(cases, comparator="loop"):
    def values(exp, name, arm="purser"):
        return column(cases, exp, arm, name)
    false = values("E1", "false_parent_closure")
    e1 = (_all([_at_least([None if v is None else not v for v in false], 20),
                _at_least(values("E1", "missing_named"), 19)]),
          _at_least(values("E1", "caught", comparator), 19))
    p, b = values("E2", "rework_steps"), values("E2", "rework_steps", comparator)
    fp, fb = values("E2", "final_pass"), values("E2", "final_pass", comparator)
    half = lower = final = None
    if None not in p + b:
        half, lower = 2 * sum(p) <= sum(b), sum(p) < sum(b)
    # Boolean pass-count intervals keep provable failures even with missing arms.
    min_p, min_b = fp.count(True), fb.count(True)
    if min_p >= min_b + fb.count(None):
        final = True
    elif min_p + fp.count(None) < min_b:
        final = False
    comparative = _all([lower, final])
    e2 = (_all([_at_least(values("E2", "unaffected_both_retained"), 18), half, final]),
          None if comparative is None else not comparative)
    d = values("E3", "duplicate_count")
    no_duplicate = _all([None if v is None else v == 0 for v in d])
    no_stuck = _all([None if v is None else not v for v in values("E3", "stuck_requires_manual")])
    clean_recovery = _all([no_duplicate, no_stuck])
    e3 = (_all([no_duplicate, _at_least(values("E3", "correct_reconciliation"), 20),
                _at_least(values("E3", "resolved_by_budget"), 20)]),
          None if clean_recovery is None else not clean_recovery)
    return {"E1": e1, "E2": e2, "E3": e3}


def healthy(exp, facts):
    if exp == "E3":
        return facts.get("resolved_by_budget") is True and facts.get("duplicate_count") == 0
    return facts.get("final_parent_closed") is True and facts.get("final_pass") is True


def render(frozen=None, cases=None):
    cases = {} if cases is None else cases
    units = list(schedule())
    population = frozen["population"] if frozen else "unregistered original coverage"
    out = [f"# Notification model study · {population}", ""]
    if frozen:
        out += [f"Replicate {frozen['replicate']} on `{frozen['source_commit']}`. "
                f"Service `{frozen['conditions']['service'].get('model', 'disabled')}`. "
                f"{len(cases)} of {len(units)} scheduled case directories are present.", ""]
        if frozen.get("replication_reason"):
            out += ["Replication reason — " + frozen["replication_reason"], ""]
    if not frozen or population != "model":
        out += ["This report contains **0 of 195 original real-model observations**. "
                "The original model criterion is open. Canned and disabled cases exercise the harness.", ""]
    main, diagnostic = predicates(cases), predicates(cases, "loop_equal_verifier")
    out += ["| Experiment | Absolute threshold | Original no-advantage | Diagnostic absolute / no-advantage |",
            "| --- | --- | --- | --- |"]
    for exp in OUTCOMES:
        out.append(f"| {exp} | {show(main[exp][0])} | {show(main[exp][1])} | "
                   f"{show(diagnostic[exp][0])} / {show(diagnostic[exp][1])} |")
    out += ["", "Thresholds use the 20 primary seeds per arm. E1 requires zero false parent closures and at least "
            "19 named faults. A baseline catching at least 19 establishes the E1 no-advantage predicate. "
            "E2 requires 18 retained sibling pairs, at most half the baseline’s total rework and at least its final pass count. "
            "Equal or higher rework or a lower pass count establishes E2 no-advantage. "
            "E3 requires 20 correct, completed recoveries and zero duplicates. Any duplicate or ordinary stuck case "
            "requiring manual repair establishes its separate adverse predicate.", ""]
    for exp, keys in OUTCOMES.items():
        out += [f"## {exp}", "", "Primary totals show the sum, measurable count and scheduled count.", "",
                "| Measure | Plain loop | Purser | Equal verifier |", "| --- | ---: | ---: | ---: |"]
        for key in keys:
            cells = []
            for arm in ARMS:
                vals = [v for v in column(cases, exp, arm, key) if v is not None]
                cells.append(f"{sum(vals) if vals else '?'} / {len(vals)} / 20")
            out.append("| " + " | ".join((key, *cells)) + " |")
        if exp == "E2":
            for comparator in ("loop", "loop_equal_verifier"):
                p, b = [column(cases, exp, arm, "rework_steps") for arm in ("purser", comparator)]
                ratio = "?" if None in p + b else "undefined" if sum(b) == 0 else str(sum(p) / sum(b))
                out += ["", f"Total rework ratio, Purser / {comparator} — {ratio}."]
        detailed = (*keys, "eligible", *(E3_EXTRAS if exp == "E3" else ("final_pass", "final_parent_closed")
                              if exp == "E1" else ("checkpoint_candidate", "stale_at_delivery")), *COST)
        out += ["", "### Original case measurements", "",
                "| Track | Seed | Arm | Validity / terminal | " + " | ".join(detailed) + " |",
                "| --- | ---: | --- | --- |" + " ---: |" * len(detailed)]
        for unit in (u for u in units if u[0] == exp):
            _, track, seed, arm = unit
            rec = cases.get(unit)
            status = LABELS[rec["cost"].validity] + " / " + rec["terminal"] if rec else "missing"
            cells = [track, str(seed), arm, status, *(show(field(cases, unit, k)) for k in detailed)]
            out.append("| " + " | ".join(cells) + " |")
        out += ["", "### Paired differences", "",
                "Each cell subtracts that comparator from Purser in this order — " + ", ".join((*keys, *COST)) + ".", "",
                "| Track / seed | Purser minus plain loop | Purser minus equal verifier |", "| --- | --- | --- |"]
        for _, track, seed, arm in (u for u in units if u[0] == exp and u[3] == "purser"):
            cells = []
            for other in ("loop", "loop_equal_verifier"):
                delta = []
                for name in (*keys, *COST):
                    pair = [(exp, track, seed, x) for x in (arm, other)]
                    a, b = [field(cases, u, name) if name in COST or track != "primary"
                            or cases.get(u, {}).get("facts", {}).get("eligible") is True else None for u in pair]
                    delta.append(show(a - b if a is not None and b is not None else None))
                cells.append(" / ".join(delta))
            out.append(f"| {track} / {seed} | " + " | ".join(cells) + " |")
        seed = MANIFEST["experiments"][exp]["clean_seed"]
        pair = [cases.get((exp, "clean", seed, a)) for a in ("purser", "loop")]
        flag = None
        if all(r and r["cost"].validity == "complete" and healthy(exp, r["facts"]) and r["cost"].exact.get("steps") is not None for r in pair):
            flag = pair[0]["cost"].exact["steps"] > 2 * pair[1]["cost"].exact["steps"]
        out += ["", f"Healthy matched clean pair above twice plain-loop steps — {show(flag)}.", ""]
    out += [render_model_costs(units, {u: c["cost"] for u,c in cases.items()}), "## Original errors", ""]
    for unit, case in cases.items():
        if case["errors"]:
            out.append("- " + " / ".join(map(str, unit)) + " — " + show(", ".join(case["errors"])))
    if not any(c["errors"] for c in cases.values()):
        out.append("No error was recorded in the available original artifacts.")
    return "\n".join(out) + "\n"
