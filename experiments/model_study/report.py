"""Cold, provider-free comparison over the prospective population and original case artifacts."""

import base64
from pathlib import Path
from subprocess import SubprocessError

from experiments.e1.accounting import READ_ERRORS
from experiments.e1.artifacts import MANIFEST, REGISTRATION, sha
from experiments.e1.measurement import MeasurementUnavailable, same
from experiments.e1.report import Objects, parse, require, wire_valid
from experiments.e1.recovery import FileArchive, input_identity
from experiments.e2.outcomes import conjunction

from experiments.model_costs import FIELDS as COST, ModelCaseCost, render_model_costs

from .collector import E1_FIELDS, E2_FIELDS, case_identity, case_path, coding_score, coding_validity, e3_args
from .registration import ARMS, load_registration, schedule

OUTCOMES = {"E1": E1_FIELDS, "E2": E2_FIELDS,
            "E3": ("correct_reconciliation", "duplicate_count", "effect_count", "resolved_by_budget",
                   "stuck_requires_manual", "manual_repair")}
E3_EXTRAS = ("original_truth", "original_report", "justified_in_order", "uncertainty_remaining")
E2_EXTRAS = ("publicly_delivered", "checkpoint_candidate", "stale_at_delivery", "new_bad_closure", "observed_retention",
             "observed_reopened", "reopened_subjects", "observed_invalid_closure", "rework_observed", "final_parent_closed")
LABELS = {"complete": "complete", "arm_incomplete": "incomplete", "infrastructure_invalid": "invalid"}
UNREADABLE = (*READ_ERRORS, OSError, SubprocessError, ImportError, MeasurementUnavailable)


class Archive(FileArchive):
    """Cold entry point also works when the evidence tree is mounted read-only."""
    def __init__(self, directory):
        self.directory = directory
        self.objects = directory / "objects"


def reported_models(records):
    result = set()
    for record in records.values():
        wire = record["transport"]
        if not wire.get("response_complete", wire["status"] == "response") or not wire.get("response_base64"):
            continue
        try:
            response = parse(base64.b64decode(wire["response_base64"], validate=True))
        except (ValueError, UnicodeError, RecursionError):
            continue
        if isinstance(response, dict) and isinstance(response.get("model"), str):
            result.add(response["model"])
    return sorted(result)


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
    previous_marker = None
    for unit in units:
        path = case_path(directory, unit)
        if not path.exists():
            continue
        identity = case_identity(unit, frozen, wire)
        archive = Archive(path)
        objects = Objects(archive)
        exact, lower, facts, raw = {}, {}, {}, {}
        records, provenance, errors = {}, {}, []
        trace_ref = trace = None
        try:
            marker = parse((path / "started.json").read_bytes())
            require(marker["identity"] == identity and type(marker["monotonic_ns"]) is int, "invalid original start marker")
            require(previous_marker is None or marker["started_utc"] >= previous_marker,
                    "case starts disagree with the registered arm rotation")
            previous_marker = marker["started_utc"]
            try:
                trace_ref = parse((path / "session.json").read_bytes())
                trace = objects.item(trace_ref)
                records, exact, lower, first = read_session(trace, archive, frozen, identity)
            except UNREADABLE as error:
                errors.append(f"collector session: {type(error).__name__}: {error}")
            else:
                exposure = {"ordinal": first["turn"]["ordinal"], "request_sha256": first["request_sha256"],
                            "send_interval_ns": [first["started_monotonic_ns"], first["returned_monotonic_ns"]],
                            "http_status": first["transport"]["http_status"]} if first else None
                provenance = {"collector_invocations": len(trace["turns"]), "model_receipts": len(records),
                              "first_model_exposure": exposure, "reported_models": reported_models(records)}
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
                participant_turns = objects.item(raw["artifacts"]["model"])["turns"]
                for entry in participant_turns:
                    ref = entry.get("result") or entry.get("recovered_result")
                    common = records.get(entry["ordinal"])
                    if common is not None and ref:
                        require(objects.item(ref) == common, "participant and collector retained different original replies")
                for k, value in facts["cost_exact"].items():
                    if value is not None:
                        require(exact.get(k, value) == value, "E3 cost differs from shared original collector evidence")
                        exact[k] = value
                for k, value in facts["cost_lower_bounds"].items():
                    if value is not None:
                        lower[k] = max(lower.get(k, 0), value)
                        require(exact.get(k, lower[k]) >= lower[k], "E3 lower bound exceeds an original measurement")
                first_observed = [r for r in (facts.get("first_model_exposure"), provenance.get("first_model_exposure")) if r]
                if len(first_observed) == 2 and first_observed[0]["ordinal"] == first_observed[1]["ordinal"]:
                    require(first_observed[0] == first_observed[1], "readers disagree about the first confirmed send")
                provenance["first_model_exposure"] = min(first_observed, key=lambda r: r["ordinal"], default=None)
                provenance["model_receipts"] = len({*records, *(e["ordinal"] for e in participant_turns
                                                   if e.get("result") or e.get("recovered_result"))})
            else:
                require(not errors and raw["artifacts"]["model"] == trace_ref, "model trace differs from original session")
                facts = readback.reconstruct_case(raw, archive, records, registration_sha256=identity["registration_sha256"],
                                                  evidence_issues=errors)
                for k, partial in (("elapsed_ns", "elapsed_lower_ns"), ("steps", "observed_steps")):
                    exact[k] = facts[k]
                    lower[k] = facts[partial]
                if raw["validity"] != coding_validity(raw["terminal"], track, facts):
                    errors.append("published classification differs from independently established exit and exposure")
                if not same(raw["score"], coding_score(raw, facts)):
                    errors.append("published score differs from independently established measurements")
                for k in COST:
                    expected = exact.get(k)
                    if expected is None and k in ("steps", "elapsed_ns", "model_calls"):
                        expected = lower.get(k)
                    if not same(expected, raw["cost"][k]):
                        errors.append("published cost differs from independently established " + k)
            platform = objects.item(raw["artifacts"]["platform"])
            problem = platform.get("failure") or facts.get("error")
            validity = "infrastructure_invalid" if errors else raw["validity"]
            if trace is not None:
                errors.extend(t.get("outcome", "interrupted model request") for t in trace["turns"] if t.get("outcome") != "decision")
            if (path / "dispute.json").exists():
                errors.append(parse((path / "dispute.json").read_bytes())["error"])
            if problem:
                errors.append(str(problem))
            if facts.get("qualification_dispute"):
                errors.append(facts["qualification_dispute"])
            errors.extend(facts.get("public_observation_gaps", []))
            facts.update(provenance)
            cases[unit] = {"facts": facts, "terminal": raw["terminal"], "errors": errors,
                           "cost": ModelCaseCost(validity, exact, lower)}
        except UNREADABLE as error:
            cases[unit] = {"facts": provenance, "terminal": "unavailable original evidence",
                           "errors": [*errors, f"{type(error).__name__}: {error}"],
                           "cost": ModelCaseCost("infrastructure_invalid", exact, lower)}
    return frozen, cases


def show(value):
    if value is None:
        return "?"
    if type(value) is bool:
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(map(show, value)) or "∅"
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
    if unit[0] == "E3" and case["cost"].validity == "infrastructure_invalid":
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


def e2_publications(cases):
    lines = []
    for (exp, track, seed, arm), case in cases.items():
        if exp != "E2":
            continue
        for field, action in (("reopen_observations", "reopened"), ("closure_observations", "closed")):
            for observation in case["facts"].get(field, []):
                cells = (f"{track} / {seed} / {arm}", observation["subject"], action, observation["publication"],
                         observation["candidate"], observation["at_delivery"], observation.get("revised_pass"))
                lines.append("| " + " | ".join(show(c) for c in cells) + " |")
    if not lines:
        return []
    return ["", "### Observed E2 publications", "",
            "Each reopening cites its publication and the previously closed candidate. Each affected closure "
            "cites its declared candidate and its revised retry grade. The final pass checks all six obligations.", "",
            "| Case | Subject | State change | Publication | Candidate | At delivery | Revised retry passed |",
            "| --- | --- | --- | --- | --- | --- | --- |", *lines]


def render(frozen=None, cases=None):
    cases = {} if cases is None else cases
    units = list(schedule())
    population = frozen["population"] if frozen else "unregistered original coverage"
    out = [f"# Notification model study · {population}", ""]
    if frozen:
        service = frozen["conditions"]["service"]
        out += [f"Replicate {frozen['replicate']} on `{frozen['source_commit']}`. "
                f"Service `{service.get('model', 'disabled')}`. "
                f"{len(cases)} of {len(units)} scheduled case directories are present.", ""]
        if not service.get("disabled"):
            out += ["Registered provider, provider version and model revision — " + " / ".join(
                show(service.get(k)) for k in ("provider", "provider_version", "model_revision")) + ".", ""]
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
        detailed = (*keys, "eligible", *(E3_EXTRAS if exp == "E3" else E2_EXTRAS if exp == "E2"
                                          else ("final_pass", "final_parent_closed")), *COST)
        out += ["", "### Case measurements", "",
                "| Track | Seed | Arm | Validity / terminal | " + " | ".join(detailed) + " |",
                "| --- | ---: | --- | --- |" + " ---: |" * len(detailed)]
        for unit in (u for u in units if u[0] == exp):
            _, track, seed, arm = unit
            rec = cases.get(unit)
            status = LABELS[rec["cost"].validity] + " / " + rec["terminal"] if rec else "missing"
            cells = [track, str(seed), arm, status]
            for k in detailed:
                exact = field(cases, unit, k)
                bound = rec["cost"].lower_bounds.get(k) if rec and k in COST else None
                cells.append("≥" + show(bound) if exact is None and bound is not None else show(exact))
            out.append("| " + " | ".join(cells) + " |")
        if exp == "E2":
            out.extend(e2_publications(cases))
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
    out.append(render_model_costs(units, {u: c["cost"] for u,c in cases.items()}))
    if cases:
        out += ["## Retained model evidence", "",
                "A collector invocation can end before a request reaches the service. The first confirmed send "
                "links to its original request digest. A sealed reply can survive the loss of its participant.", "",
                "| Case | Collector invocations | Transport receipts | First confirmed send | Model IDs in responses |",
                "| --- | ---: | ---: | --- | --- |"]
        for unit, case in cases.items():
            facts = case["facts"]
            first = facts.get("first_model_exposure")
            first_label = f"{first['ordinal']} · `{first['request_sha256']}`" if first else "?"
            ids = facts.get("reported_models", [])
            out.append("| " + " | ".join(("/".join(map(str, unit)), show(facts.get("collector_invocations")),
                       show(facts.get("model_receipts")), first_label, show(", ".join(ids) or None))) + " |")
        out.append("")
    out += ["## Original errors", ""]
    for unit, case in cases.items():
        if case["errors"]:
            out.append("- " + " / ".join(map(str, unit)) + " — " + show(", ".join(case["errors"])))
    if not any(c["errors"] for c in cases.values()):
        out.append("No error was recorded in the available original artifacts.")
    return "\n".join(out) + "\n"
