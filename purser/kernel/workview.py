"""Pure work eligibility, closure witnesses and continuity answers at one revision."""
from collections import defaultdict

from .support import SupportGraph
from .wire import digest


def _reasons(items):
    groups = defaultdict(set)
    for code, refs in items:
        groups[code].update(refs)
    return [{"code": code, "refs": sorted(refs)} for code, refs in sorted(groups.items())]


class WorkView:
    def __init__(self, projection):
        self.records = {r["id"]: r for r in projection["records"]}
        self.graph = SupportGraph(self.records, projection["edges"])
        self.children = defaultdict(list)
        self.related = defaultdict(list)
        self.observations = defaultdict(list)
        self.evidence = defaultdict(list)
        self._outcomes = {}
        for key, row in sorted(self.records.items()):
            f, kind = row["fields"], row["kind"]
            if kind == "WorkItem" and f["parent"]:
                self.children[f["parent"]].append(key)
            if "work" in f:
                self.related[(f["work"], kind)].append(key)
            if kind == "Observation" and "run" in f:
                self.observations[f["run"]].append(key)
            if kind == "Evidence":
                self.evidence[f["run"]].append(key)

    def fields(self, key):
        return self.records[key]["fields"]

    def qualification(self, key):
        obligation = self.fields(key)
        work = self.fields(obligation["work"])
        witness = {"id": key, "version": obligation["version"], "run": None, "evidence": None, "waiver": None}
        if self.graph.issues(obligation["work"]):
            return "open", witness
        waiver = obligation.get("waiver")
        if waiver:
            w = self.fields(waiver)
            if w["result"] == work.get("result") and w["version"] == obligation["version"]:
                return "waived", dict(witness, waiver=waiver)
        run = obligation.get("current_run")
        if not run or self.fields(run)["result"] != work.get("result"):
            return "open", witness
        verdicts = {self.fields(e)["verdict"] for e in self.evidence[run]}
        if verdicts == {"pass"}:
            return "passed", dict(witness, run=run, evidence=self.evidence[run][0])
        state = "conflict" if len(verdicts) > 1 else {"fail": "failed", "inconclusive": "inconclusive"}.get(
            next(iter(verdicts), None), "open")
        return state, witness

    def evaluation(self, key):
        pending = [(key, False)]
        while pending:
            work, ready = pending.pop()
            if work in self._outcomes:
                continue
            if ready:
                self._outcomes[work] = self._evaluate(work)
            else:
                pending.append((work, True))
                pending.extend((child, False) for child in self.children[work] if self.fields(child)["state"] == "closed")
        return self._outcomes[key]

    def _evaluate(self, key):
        work = self.fields(key)
        children, coverage = self.children[key], work.get("coverage")
        issues, witnesses, obligations = self.graph.issues(key), [], []
        for oid in self.related[(key, "Obligation")]:
            state, witness = self.qualification(oid)
            if state in ("passed", "waived"):
                obligations.append(witness)
            else:
                issues.append(("missing_evidence", [oid]))
        if children or coverage:
            qualified = [c for c in children if self.closed(c)]
            if not coverage or not children:
                issues.append(("missing_coverage", children or [key]))
            else:
                required = {"all": len(children), "any": 1, "k_of_n": coverage["k"]}[coverage["kind"]]
                if not 1 <= required <= len(children) or len(qualified) < required:
                    issues.append(("missing_coverage", [c for c in children if c not in qualified] or [key]))
                witnesses = [{"work": c, "outcome": self.outcome(c)} for c in qualified[:required]]
        elif not obligations and not self.related[(key, "Obligation")]:
            issues.append(("missing_evidence", [key]))
        if (not children or work.get("assignment") or self.related[(key, "Obligation")]) and not work["candidate"]:
            issues.append(("missing_result", [key]))
        basis = {"work": key, "generation": work["generation"], "candidate": work["candidate"],
                 "coverage": coverage, "children": list(children), "witnesses": witnesses,
                 "decisions": [{"id": d["id"], "revision": d["revision"]} for d in self.graph.decisions(key)],
                 "obligations": obligations, "effects": []}
        return basis, _reasons(issues), None if issues else digest("outcome", basis)

    def basis(self, work):
        return self.evaluation(work)[0]

    def outcome(self, work):
        return self.evaluation(work)[2]

    def closed(self, work):
        f = self.fields(work)
        if f["state"] != "closed":
            return False
        current = self.outcome(work)
        root = self.fields(self.records[work]["commitment"])["root"] == work
        return current is not None and current == f.get("closed_outcome") and (not root or current == f.get("accepted_outcome"))

    def reasons(self, key):
        record = self.records[key]
        f = record["fields"]
        commitment = self.fields(record["commitment"])
        if commitment["state"] == "proposed":
            waiting = [r["id"] for r in self.records.values() if r["kind"] == "Approval"
                       and r["commitment"] == record["commitment"] and r["fields"]["state"] == "pending"]
            return _reasons([("awaiting_activation", waiting or [record["commitment"]])])
        if record["kind"] in ("Decision", "Assumption"):
            if f["state"] in ("reconsidering", "invalidated"):
                return _reasons([("invalidated_support", [key, *f["causes"],
                                *(self.fields(c)["assumption"] for c in f["causes"])])])
            if f["state"] == "superseded":
                return [{"code": "superseded", "refs": [f["superseded_by"]]}]
            return []
        if record["kind"] == "Observation" and "assumption" in f:
            return self.reasons(f["assumption"])
        if record["kind"] == "DecisionOpinion":
            return self.reasons(f["decision"])
        work = key if record["kind"] == "WorkItem" else f.get("work")
        if not work and "run" in f:
            work = self.fields(f["run"])["work"]
        if not work:
            work = commitment["root"]
        _, issues, outcome = self.evaluation(work)
        if outcome and work == commitment["root"] and self.fields(work).get("accepted_outcome") != outcome:
            return _reasons([("awaiting_acceptance", [work])])
        return issues

    def reads(self, work):
        """Current work, descendants and each qualifying/rejecting record read to evaluate them."""
        seen, work_seen = set(), set()
        stack = [work]
        while stack:
            key = stack.pop()
            if key in work_seen:
                continue
            work_seen.add(key)
            seen.update({key, *self.graph.supports(key)})
            stack.extend(self.children[key])
            f = self.fields(key)
            seen.update(v for v in (f.get("assignment"), f.get("result")) if v)
            for oid in self.related[(key, "Obligation")]:
                seen.add(oid)
                criterion = self.fields(oid)
                run, waiver = criterion.get("current_run"), criterion.get("waiver")
                if waiver:
                    seen.add(waiver)
                if run:
                    seen.add(run)
                    for evidence in self.evidence[run]:
                        seen.update((evidence, self.fields(evidence)["observation"]))
        return seen

    def artifacts(self, work):
        """Immutable bytes referenced by this current outcome and its selected child outcomes."""
        result, pending = set(), [work]
        while pending:
            basis = self.basis(pending.pop())
            candidate = basis["candidate"]
            if candidate and candidate["kind"] == "artifact.bytes":
                result.add(candidate["digest"])
            pending.extend(w["work"] for w in basis["witnesses"])
            for witness in basis["obligations"]:
                if witness["evidence"]:
                    run = self.fields(witness["run"])
                    obs = self.fields(self.fields(witness["evidence"])["observation"])
                    result.update((run["definition_sha256"], run["environment_sha256"], obs["artifact_sha256"]))
        return sorted(result)

    def answers(self):
        answers = {name: [] for name in ("q02", "q03", "q04", "q07", "q08", "q09", "q10")}
        actions = set()
        for key, row in sorted(self.records.items()):
            f, kind = row["fields"], row["kind"]
            commitment = self.fields(row["commitment"])
            active = commitment["state"] == "active"
            if kind == "WorkItem":
                answers["q02"].append({"work": key, **{k: f[k] for k in ("state", "generation", "candidate")}})
                reasons = self.reasons(key)
                answers["q08"].append({"work": key, "reasons": reasons})
                if active and not self.closed(key):
                    if f["state"] == "stale":
                        actions.add(("rework", key))
                    elif self.graph.issues(key):
                        continue
                    elif self.outcome(key):
                        actions.add(("principal_accept" if commitment["root"] == key else "evaluate_close", key))
                    elif f.get("assignment") and not f.get("result"):
                        actions.add(("execute", key))
                    elif not f.get("assignment") and (not self.children[key] or self.related[(key, "Obligation")]):
                        actions.add(("assign", key))
            elif kind == "Obligation":
                state, witness = self.qualification(key)
                ids = [v for v in (witness["evidence"], witness["waiver"]) if v]
                answers["q07"].append({"obligation": key, "work": f["work"], "version": f["version"],
                                       "state": state, "evidence": ids})
                work = self.fields(f["work"])
                if self.graph.issues(f["work"]):
                    continue
                if active and work.get("result") and state == "open":
                    run = f.get("current_run")
                    if not run or self.fields(run)["result"] != work["result"]:
                        actions.add(("observe", key))
                    elif not self.observations[run]:
                        actions.add(("observe", run))
                    else:
                        actions.update(("verify", obs) for obs in self.observations[run])
                elif active and state in ("conflict", "failed", "inconclusive"):
                    actions.add(("rework", f["work"]))
            elif kind == "Decision" and f["state"] != "superseded":
                answers["q03"].append({"decision": key, "state": f["state"], "support": list(f["support"])})
                if active and f["state"] == "reconsidering":
                    actions.add(("reconsider", key))
            elif kind == "Assumption" and f["state"] == "invalidated":
                answers["q04"].append({"assumption": key, "affected": list(f["affected"])})
            elif kind == "Commitment":
                answers["q09"].append({"commitment": key, "root": f["root"], "missing": self.reasons(f["root"])})
            elif kind == "Approval" and f["state"] == "pending":
                actions.add(("principal_approve", key))
        answers["q10"] = [{"action": a, "subject": s} for a, s in sorted(actions)]
        return answers
