"""Pure work eligibility, closure witnesses and continuity answers at one revision."""
from collections import defaultdict

from .wire import digest


def _reasons(items):
    groups = defaultdict(set)
    for code, refs in items:
        groups[code].update(refs)
    return [{"code": code, "refs": sorted(refs)} for code, refs in sorted(groups.items())]


class WorkView:
    def __init__(self, projection):
        self.records = {r["id"]: r for r in projection["records"]}
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
            if kind == "Observation":
                self.observations[f["run"]].append(key)
            if kind == "Evidence":
                self.evidence[f["run"]].append(key)

    def fields(self, key):
        return self.records[key]["fields"]

    def qualification(self, key):
        obligation = self.fields(key)
        work = self.fields(obligation["work"])
        witness = {"id": key, "version": obligation["version"], "run": None, "evidence": None, "waiver": None}
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
        if key in self._outcomes:
            return self._outcomes[key]
        work = self.fields(key)
        children, coverage = self.children[key], work.get("coverage")
        issues, witnesses, obligations = [], [], []
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
                 "decisions": [], "obligations": obligations, "effects": []}
        result = basis, _reasons(issues), None if issues else digest("outcome", basis)
        self._outcomes[key] = result
        return result

    def basis(self, work):
        return self.evaluation(work)[0]

    def outcome(self, work):
        return self.evaluation(work)[2]

    def closed(self, work):
        f = self.fields(work)
        return f["state"] == "closed" and f.get("closed_outcome") == self.outcome(work) and self.outcome(work) is not None

    def reasons(self, key):
        record = self.records[key]
        f = record["fields"]
        commitment = self.fields(record["commitment"])
        if commitment["state"] == "proposed":
            waiting = [r["id"] for r in self.records.values() if r["kind"] == "Approval"
                       and r["commitment"] == record["commitment"] and r["fields"]["state"] == "pending"]
            return _reasons([("awaiting_activation", waiting or [record["commitment"]])])
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
        seen = set()
        stack = [work]
        while stack:
            key = stack.pop()
            if key in seen:
                continue
            seen.add(key)
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
        answers = {name: [] for name in ("q02", "q07", "q08", "q09", "q10")}
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
                    if self.outcome(key):
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
            elif kind == "Commitment":
                answers["q09"].append({"commitment": key, "root": f["root"], "missing": self.reasons(f["root"])})
            elif kind == "Approval" and f["state"] == "pending":
                actions.add(("principal_approve", key))
        answers["q10"] = [{"action": a, "subject": s} for a, s in sorted(actions)]
        return answers
