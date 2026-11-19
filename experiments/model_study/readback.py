"""Reconstruct coding outcomes from original public heads, Git objects and isolated observations."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from experiments.e1 import accounting, capture, outcomes
from experiments.e1.artifacts import REGISTRATION, START, changes, encoded, git, sha
from experiments.e1.executor import LEAVES, PARENT, PATHS, fault_for
from experiments.e1.measurement import same
from experiments.e1.report import GitPacks, Objects, require
from experiments.e2 import checkpoint, evidence, measure, plan_versions, policy
from experiments.e2.outcomes import all_pass, criteria
from experiments.e3.trial import files_in
from experiments.model_budget import LIMITS
from purser.driver import context_pack
from purser.kernel.wire import KernelError
from purser.kernel.workview import WorkView

from .trial import active_ns


def referenced_files(artifacts, platform, objects):
    """Follow the original content-addressed inputs without trusting the declared file list."""
    roots = [artifacts[k] for k in ("journal", "candidates", "model")]
    roots.append({k: v for k, v in platform.items() if k != "referenced_files"})
    pending = list(files_in(roots))
    found = {}
    while pending:
        ref = pending.pop()
        path = ref["path"]
        if path in found:
            require(found[path] == ref, "conflicting identity for a retained case object")
            continue
        objects.blob(ref)
        found[path] = ref
        if path.endswith(".json"):
            pending.extend(files_in(objects.item(ref)))
    return [found[path] for path in sorted(found)]


def public_points(arm, objects, trace, points):
    if arm == "purser":
        history = accounting.kernel_history(trace, {p["revision"] for p in points})
        def read(point):
            digest, rows, snapshot = history[point["revision"]]
            require(digest == point["head_hash"], "public kernel head differs from replay")
            return rows, snapshot["projection"]
    else:
        known, previous = {}, None
        for n, ref in enumerate(trace["plan_versions"], 1):
            rows = objects.item(ref)
            stamp = rows[PARENT][plan_versions.KEY]
            require(stamp["number"] == n and stamp["previous"] == previous, "broken plan publication history")
            previous = ref["sha256"]
            known[previous] = rows
        require(previous and known[previous] == trace["plan"], "terminal plan differs from durable publications")
        def read(point):
            rows = known[point["plan_sha256"]]
            return rows, rows["W2"]
    result, sources = {}, {}
    for point in points:
        rows, source = read(point)
        require(same(rows, objects.item(point["view"])), "public view differs from its durable head")
        key = encoded(point)
        result[key], sources[key] = accounting.public_rows(rows, arm), source
    return result, sources


def validate_overlay(repo, before, after, fault, seed):
    variant, work, _ = fault
    path = f"notes/seed-{seed}.txt" if variant == "outside_scope" else PATHS[work]
    expected = (b"out of scope\n" if variant == "outside_scope" else
                (REGISTRATION / "input" / path).read_bytes())
    if variant == "skipped_retry_check":
        expected = expected.replace(b"return (0,)", b"return (0,) + tuple(base * k for k in range(1, attempts - 1))")
    delta = git(repo, "diff", "--name-only", "-z", before, after).split(b"\0")[:-1]
    require(delta in ([], [path.encode()]) and git(repo, "show", f"{after}:{path}") == expected,
            "submitted tree differs from the registered overlay on the model's original source")
    require(git(repo, "ls-tree", after, "--", path).startswith(b"100644 blob "), "invalid overlay file mode")


def model_application(actions, records, objects, repos, source, experiment, complete):
    chosen = {}
    for a in actions:
        if not a.get("choice"):
            continue
        n, choice = a["model_turn"], a["choice"]
        r = records.get(n)
        if r is None and not complete:
            continue
        require(r is not None and a["issued_monotonic_ns"] <= r["started_monotonic_ns"]
                <= r["returned_monotonic_ns"] <= a.get("work_started_ns", r["returned_monotonic_ns"]),
                "model receipt lies outside the original public decision interval")
        require(n not in chosen and choice["request_sha256"] == r["request_sha256"]
                and choice["result_sha256"] == sha(encoded(r))
                and r["proposal"] == {k: choice[k] for k in ("action", "arguments")}
                and objects.item(a["prompt"]) == json.loads(r["request"]["messages"][1]["content"]),
                "applied choice differs from original model response or prompt")
        group = [p for p in actions if p.get("model_turn") == n and p["attempt"] >= a["attempt"]
                 and p["input"].get("tool") != "public plan and working Git tree"]
        first = next(p for p in source if p["kind"] == "action" and p["payload"] is a)
        preceding = [p["payload"] for p in source[:first["seq"] - 1] if p["kind"] == "action"]
        view = preceding[-1]
        prompt = objects.item(a["prompt"])
        from .participant import BY_PATH, TASK, menu, prompt_bytes, workspace_view
        from purser.adapters.model_protocol import ModelPrompt
        work_done = {p["subject"] for p in preceding if p["action"] == "edit" and p.get("return", {}).get("edited")}
        paths = [path for work, path in PATHS.items() if work != "W2" or {"W1", "W3"} <= work_done]
        previous_rows = source[:first["seq"] - 1]
        previous_oid = START
        notice = None
        for row in previous_rows:
            payload = row["payload"]
            if row["kind"] == "action" and payload["action"] == "edit":
                previous_oid = payload.get("return", {}).get("candidate", previous_oid)
            if row["kind"] == "fault":
                if experiment == "E2":
                    notice = payload["released"]
                else:
                    previous_oid = payload["candidate"]
        actual = workspace_view(experiment, objects.item(view["before"]["view"]), repos[previous_oid],
                                previous_oid, notice, previous_rows, n - 1)
        require(same(view["return"], actual), "model public context differs from participant, Git or public replies")
        outside = [row["path"] for row in actual["git_changes"] if row["status"] == "A" and row["path"] not in BY_PATH]
        revised = actual["notice"] and not any(p["action"] == "plan" and "adopt" in p["input"]
                                                and "failure" not in p.get("return", {}) for p in preceding)
        wanted = ModelPrompt(TASK, encoded(actual).decode(), menu(paths, outside, revised))
        require(view["input"] == {"tool": "public plan and working Git tree"} and prompt == prompt_bytes(wanted),
                "model input differs from released public workspace and registered tools")
        action, args = choice["action"], choice["arguments"]
        wanted.proposal(encoded(r["proposal"]))
        kind = {"edit": "edit", "revise_retry": "plan", "remove": "edit", "reopen": "plan", "stop": "decide",
                "read": "context", "inspect": "context", "close": "decide", "diagnose": "check"}[action]
        work = args.get("work", BY_PATH.get(args.get("path"), PARENT))
        require(all(p["subject"] == work for p in group), "compound action reached a different subject")
        matches = [p for p in group if p["action"] == kind]
        refused = any(p.get("return", {}).get("disposition") == "rejected" for p in group)
        require(matches or refused or any("failure" in p.get("return", {}) for p in group),
                "model choice lacks its corresponding public work action")
        for p in group:
            if p["action"] == "edit":
                require(action in ("edit", "remove", "revise_retry")
                        and all(p["input"].get(k) == v for k, v in args.items()), "edit differs from original operands")
                if (edited := p.get("return", {}).get("candidate")):
                    repo = repos[edited]
                    paths = git(repo, "diff", "--name-only", "-z", p["candidate"], edited).split(b"\0")[:-1]
                    require(paths in ([], [args["path"].encode()]), "model edit changed an additional path")
                    if action == "remove":
                        require(not git(repo, "ls-tree", edited, "--", args["path"]), "model removal was not applied")
                    else:
                        require(git(repo, "show", f"{edited}:{args['path']}") == args["source"].encode(),
                                "candidate bytes differ from returned source")
            if p["actor"] == "closure":
                require(action == "close" or args.get("disposition") == "request_close", "unrequested closure")
        if matches:
            applied = matches[-1] if action == "close" else matches[0]
            if action in ("stop", "read", "diagnose"):
                require(applied["input"] == args, "public operands differ from the requested decision")
            if action in ("inspect", "reopen", "close"):
                require(applied["subject"] == args["work"], "wrong public work operand")
            if action == "read" and "failure" not in applied.get("return", {}):
                page = int(args["page"])
                data = git(repos[previous_oid], "show", f"{previous_oid}:{args['path']}")
                require(applied["return"] == {"path":args["path"], "page":page,
                                "source":data[page*8192:(page+1)*8192].decode(errors="replace")}, "Git read differs")
        chosen[n] = choice
    if complete:
        require(set(chosen) == {n for n, r in records.items() if r["outcome"] == "decision"},
                "original model decision is missing its public application")
    return chosen


def reconstruct_case(raw, archive, records, *, registration_sha256):
    objects = Objects(archive)
    meta, rows, candidates = (objects.item(raw["artifacts"][k]) for k in ("platform", "journal", "candidates"))
    require(meta["referenced_files"] == referenced_files(raw["artifacts"], meta, objects),
            "coding case file manifest differs from the original artifact graph")
    arm, experiment, track, seed = (raw[k] for k in ("arm", "experiment", "track", "seed"))
    from experiments.e1.artifacts import MANIFEST
    rank = MANIFEST["experiments"][experiment]["seeds"].index(seed) + 1
    actions = [e["payload"] for e in rows if e["kind"] == "action"]
    require([p["attempt"] for p in actions] == list(range(1, len(actions) + 1)) and len(actions) <= LIMITS["max_steps"],
            "public attempts missing or duplicated")
    require([p["seq"] for p in rows] == list(range(1, len(rows) + 1)) and rows[-1]["kind"] == "terminal"
            and [r["offset_ns"] for r in rows] == sorted(r["offset_ns"] for r in rows), "invalid chronological journal")
    terminal = rows[-1]["payload"]
    require(meta["budget_reason"] == terminal["end_reason"] == raw["terminal"], "terminal evidence differs")
    periods = meta.get("private_intervals", [])
    for period in periods:
        segment = rows[period["preceding_events"]:period.get("following_events", len(rows))]
        require(all(r["kind"] in ("oracle", "terminal") for r in segment), "protected pause contains public work")
    measured_time = active_ns(meta["start_monotonic_ns"], meta["end_monotonic_ns"], periods)
    exact = meta["clock_basis"] in ("before_release", "exact")
    cutoff = raw["terminal"] in ("time_limit", "step_limit")
    complete = (exact and raw["terminal"] != "harness_failure" and
                not (cutoff and any("after" not in a or "return" not in a or "failure" in a["return"] for a in actions)))
    facts = {"terminal": raw["terminal"], "steps": len(actions) if exact else None,
             "observed_steps": len(actions), "elapsed_ns": measured_time if exact else None,
             "elapsed_lower_ns": measured_time, "error": meta.get("failure")}
    with TemporaryDirectory(prefix="purser-model-readback-") as temp:
        packs = GitPacks(Path(temp))
        repos = {oid: packs.restore(oid, entry, objects) for oid, entry in candidates.items()}
        if not meta.get("initial_public") or not meta.get("treatment_trace"):
            require(not complete, "completed case lacks original participant")
            return facts
        points = [meta["initial_public"], meta["final_public"]]
        timeline = [r for r in rows if r["kind"] in ("action", "fault") and "before" in r["payload"]]
        for r in timeline:
            points.extend(r["payload"][k] for k in ("before", "after") if k in r["payload"])
        trace = objects.item(meta["treatment_trace"])
        positions, sources = public_points(arm, objects, trace, points)
        previous = positions[encoded(meta["initial_public"])]
        actual_states = []
        for row in timeline:
            p = row["payload"]
            before = positions[encoded(p["before"])]
            require(previous == before or not complete, "unobserved change of public participant state")
            if (row["kind"] == "action" and p["action"] == "context" and "work" in p["input"]
                    and "return" in p and "failure" not in p["return"]):
                point, work = p["before"], p["subject"]
                if arm == "purser":
                    projection = sources[encoded(point)]
                    assignment = WorkView(projection).fields(work)["assignment"]
                    try:
                        context = context_pack({**point, "projection": projection}, assignment)
                    except KernelError as error:
                        context = {"disposition": "refused", "code": str(error)}
                else:
                    context = {k: v for k, v in objects.item(point["view"])[work].items()
                               if k not in ("_publication", plan_versions.KEY)}
                require(context == p["return"], "assignment context differs from the active plan or kernel revision")
            after = positions.get(encoded(p.get("after")))
            if after is None:
                require(not complete, "public work never yielded a measured endpoint")
                continue
            for work in sorted(after):
                if before[work] != after[work]:
                    actual_states.append((p["attempt"], work, before[work][0], *after[work]))
            previous = after
            if row["kind"] == "action" and p.get("response_evidence"):
                require(objects.item(p["response_evidence"]) == capture.response_evidence(p),
                        "public reply differs from original response")
        transitions = [(p["source_action"], p["subject"], p["from"], p["to"], p["candidate"])
                       for r in rows if r["kind"] == "public_state" for p in [r["payload"]]
                       if (p["source_action"], p["subject"]) in {(a,w) for a,w,*_ in actual_states}]
        require(transitions == actual_states or not complete, "public transitions differ from original participant")
        from .observer_receipts import Observed
        observed = Observed(meta.get("isolation_receipts", []), objects, repos, complete,
                            {"registration_sha256": registration_sha256, "unit": [experiment, track, seed, arm]})
        phase = policy.LINEAR
        for row in rows:
            p = row["payload"]
            if row["kind"] == "fault" and experiment == "E2":
                phase = policy.EXPONENTIAL
            if row["kind"] != "action" or not p.get("return") or "failure" in p["return"]:
                continue
            work, oid = p["subject"], p["candidate"]
            if p["action"] == "check" and p["input"].get("check") in ("public task checks", "post-submit public self-review"):
                category = "worker" if p["actor"] == "executor" else "review"
                selector = {"work": work, "phase": phase}
                if category == "worker":
                    fault = fault_for(seed, track) if experiment == "E1" else None
                    selector["skip"] = bool(fault and fault[0] == "skipped_retry_check" and fault[1] == work and
                                            not any(prior["kind"] == "fault" for prior in rows[:row["seq"]-1]))
                reproduced = observed.public(category, experiment, repos[oid], p, selector)
                require(same(reproduced, p["return"]), "public check differs from original observations")
            elif p["action"] == "observe":
                selector = {"criterion": p["input"]["criterion"], "phase": phase}
                reproduced = observed.public("verifier", experiment, repos[oid], p, selector)
                require(same(reproduced, p["return"]["report"]), "equipped observation differs from original guest")
        grades = {}
        for row in rows:
            if row["kind"] == "oracle":
                p = row["payload"]
                oid, label, version = measure.key(p)
                report = objects.item(p["evidence"])
                grade = measure.grade(report, oid, label, seed, rank, version)
                probe_refs = objects.item(p["probe_receipts"])
                if label != "scope/paths" or probe_refs:
                    require(probe_refs == observed.refs.get(p["probe_group"]), "protected checkpoint receipts differ")
                observed.validate(p["probe_group"], oid, label, version, report, seed, rank)
                require(grade == p["verdict"] and oid in repos and (oid, label, version) not in grades,
                        "duplicate or unbound protected observation")
                if label == "scope/paths":
                    require(same(report["cases"][0]["all_changes"], changes(repos[oid], oid)),
                            "protected scope differs from Git tree")
                grades[oid, label, version] = grade
        model_application(actions, records, objects, repos, rows, experiment, complete)
        notices = [r for r in rows if r["kind"] == "fault"]
        require(len(notices) <= (0 if track == "clean" else 1), "unexpected intervention count")
        notice = notices[0] if notices else None
        final = meta["exit_git"]
        oid = final["head"] if not final["status"] else None
        if complete:
            last_edit = START
            for row in rows:
                payload = row["payload"]
                if row["kind"] == "action" and payload["action"] == "edit":
                    last_edit = payload.get("return", {}).get("candidate", last_edit)
                if row["kind"] == "fault" and experiment == "E1":
                    last_edit = payload["candidate"]
            require(last_edit == oid, "final Git head differs from original participant edits")
        states = positions[encoded(meta["final_public"])]
        facts.update(final_candidate=oid, final_parent_closed=is_closed_at(states, PARENT, oid))
        phase = measure.NEW if experiment == "E2" and track != "clean" else measure.OLD
        facts["final_pass"] = all_pass(criteria(oid, grades, phase))
        if experiment == "E1":
            if notice:
                p = notice["payload"]
                variant = fault_for(seed, track)
                require((p["intervention"], p["work"], p["target"]) == variant and p["seed"] == seed,
                        "incorrect registered E1 fault or target")
                validate_overlay(repos[p["candidate"]], p["source_candidate"], p["candidate"], variant, seed)
                submits = [r for r in rows if r["kind"] == "action" and r["payload"]["action"] == "submit"
                           and r["payload"]["subject"] == p["work"]]
                require(submits and submits[0]["seq"] == notice["seq"] + 1 and
                        submits[0]["payload"]["input"] == {"candidate": p["candidate"], "claim": "done, tests pass"},
                        "E1 fault was not the first applicable submitted result")
            observation = outcomes.assess(rows, history_complete=complete, candidates=candidates)
            facts.update(observation.facts, eligible=bool(observation.fault_measured) if track == "primary" else complete)
        else:
            facts.update(intervention_reached=bool(notice) if complete or notice else None,
                         unaffected_both_retained=None, unaffected_reopened=None, rework_steps=None,
                         affected_invalid_closure=None, stale_at_delivery=None)
            if notice:
                p = notice["payload"]
                before = positions[encoded(p["before"])]
                after = positions[encoded(p["after"])]
                old = p["candidate"]
                require(p["released"] == policy.notice(seed) and p["trigger"] == p["before"],
                        "changed premise differs from registered public notice")
                required = checkpoint.required_cases(before, old)
                blobs = {c: evidence.file_ids(repos[c], c) for c in required}
                old_grades = {(c, label): grades.get((c, label, 1)) for c, labels in required.items() for label in labels}
                submissions = [r["payload"] for r in rows if r["kind"] == "action" and
                                r["seq"] < notice["seq"] and r["payload"]["action"] == "submit"
                                and r["payload"]["subject"] == "W2" and r["payload"]["candidate"] == old
                                and (r["payload"].get("return", {}).get("state") == "submitted" or
                                     r["payload"].get("return", {}).get("disposition") == "admitted")]
                require(submissions and all(checkpoint.original_retry_policy(arm, sources[encoded(point)])
                                            for point in (submissions[-1]["after"], p["before"])),
                        "W2 submission or fault checkpoint lacked its operative A1 decision")
                after_source = sources[encoded(p["after"])]
                if arm == "purser":
                    observed = next((r["fields"] for r in after_source["records"] if r["id"] == policy.OBSERVATION), {})
                    require(p["return"]["disposition"] == "admitted" and observed.get("assumption") == policy.ASSUMPTION
                            and observed["finding"] == "contradicts" and
                            objects.blob(trace["kernel_artifacts"][observed["artifact_sha256"]]) == encoded(p["released"]),
                            "public notice was not admitted with its original evidence")
                else:
                    require(after_source["premise"] == p["released"], "changed premise was never written to the plan")
                require(checkpoint.eligible(before, old, track, old_grades, blobs, True) is True,
                        "E2 intervention lacks a qualifying public and behavioral checkpoint")
                require(any(x["checkpoint"] == p["before"] and x["candidate"] == old for x in periods),
                        "E2 premise lacks its original qualification interval")
                following = [r["payload"] for r in rows[notice["seq"]:] if r["kind"] == "action"]
                affected = [(w, point[w][1]) for point in [after, *(positions[encoded(a["after"])] for a in following if a.get("after"))]
                            for w in ("W2", PARENT) if outcomes.is_closed(point[w][0])]
                bad = [all_pass(criteria(c, grades, 2)) if w == PARENT else
                       (grades[c, LEAVES["W2"], 2] == "pass" if (c, LEAVES["W2"], 2) in grades else None)
                       for w, c in affected]
                reopened = {w for a,w,prev,now,_ in actual_states if (a == p["attempt"] or
                            type(a) is int and any(x["attempt"] == a for x in following))
                            and w in ("W1", "W3") and outcomes.is_closed(prev) and not outcomes.is_closed(now)}
                facts.update(checkpoint_candidate=old, stale_at_delivery=not outcomes.is_closed(after["W2"][0]),
                             unaffected_both_retained=False if reopened else True if complete else None,
                             unaffected_reopened=len(reopened) if complete else None,
                             affected_invalid_closure=True if False in bad else False if complete and None not in bad else None,
                             rework_steps=len(following) if complete else None)
            facts["eligible"] = bool(notice) if track != "clean" else complete
    return facts


def is_closed_at(states, work, oid):
    return bool(oid and outcomes.is_closed(states[work][0]) and states[work][1] == oid)
