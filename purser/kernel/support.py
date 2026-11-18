"""Typed dependency traversal over one immutable projection."""
from collections import defaultdict


class SupportGraph:
    def __init__(self, records, edges):
        self.records = records
        self.parents = {r["fields"]["parent"] for r in records.values()
                        if r["kind"] == "WorkItem" and r["fields"]["parent"]}
        self.outgoing = defaultdict(set)
        self.incoming = defaultdict(set)
        for edge in edges:
            if edge["kind"] == "depends_on":
                dependent, support = edge["from_id"], edge["to_id"]
                self.outgoing[dependent].add(support)
                self.incoming[support].add(dependent)

    def affected(self, start):
        """Reverse reachability. A decision boundary admits only its declared trigger."""
        found = {start}
        pending = [start]
        while pending:
            current = pending.pop()
            for other in self.incoming[current] - found:
                row = self.records[other]
                if row["kind"] == "Decision" and (
                    row["fields"]["state"] == "superseded" or current not in row["fields"]["triggers"]
                ):
                    continue
                found.add(other)
                pending.append(other)
        return found - {start}

    def supports(self, work):
        """A decision owns its trigger state. Work consumes the effective decision revision."""
        found = set()
        pending = list(self.outgoing[work])
        while pending:
            key = pending.pop()
            if key in found:
                continue
            found.add(key)
            if self.records[key]["kind"] == "WorkItem":
                pending.extend(self.outgoing[key] - found)
        return found

    def decisions(self, work):
        result = []
        for key in sorted(self.supports(work)):
            row = self.records[key]
            f = row["fields"]
            if row["kind"] == "Decision" and f["state"] == "effective":
                result.append({"id": key, "revision": f["effective_revision"],
                               "choice": f["choice"], "support": list(f["support"])})
        return result

    def work_outcomes(self, work):
        """Direct WorkItem support only. A missing qualifying closure has no current outcome."""
        result = {}
        for key in sorted(self.outgoing[work]):
            row = self.records[key]
            if row["kind"] == "WorkItem":
                f = row["fields"]
                result[key] = f.get("closed_outcome") if f["state"] == "closed" else None
        return result

    def acquired(self, work):
        f = self.records[work]["fields"]
        if f.get("assignment"):
            return self.records[f["assignment"]]["fields"].get("work_supports", {})
        return f.get("structural_supports", {})

    def changed_outcomes(self, work):
        acquired, live = self.acquired(work), self.work_outcomes(work)
        return {key for key, previous in acquired.items()
                if key not in live or live[key] is not None and live[key] != previous}

    def transition(self, work):
        """Fence worked generations and advance automatic ones when their acquired input changes."""
        row = self.records[work]
        f = row["fields"]
        if not self.outgoing[work] and not self.acquired(work) and f["state"] != "stale":
            return None
        causes = set(f.get("causes", []))
        changed = self.changed_outcomes(work)
        if f.get("assignment"):
            if not changed or f["state"] == "stale":
                return None
            state, generation, supports = "stale", f["generation"], self.acquired(work)
            causes.update(changed)
        else:
            if work not in self.parents and not f.get("coverage"):
                return None
            prior = self.acquired(work)
            supports = {k: outcome or prior.get(k) for k, outcome in self.work_outcomes(work).items()
                        if outcome or k in prior}
            recovered = f["state"] == "stale" and not self.issues(work, reassign=True)
            if prior == supports and not recovered:
                return None
            renew = bool(changed or recovered)
            generation = f["generation"] + int(renew)
            state = "open" if recovered or (renew and f["state"] == "closed") else f["state"]
            if recovered:
                causes.clear()
        return {"id": work, "commitment": row["commitment"], "state": state, "generation": generation,
                "supports": supports, "causes": sorted(causes)}

    def issues(self, work, *, reassign=False):
        found = self.supports(work)
        invalid, waiting = set(), set()
        for key in found | {work}:
            row = self.records[key]
            kind, f = row["kind"], row["fields"]
            if kind == "WorkItem":
                if key in found and f["state"] != "closed":
                    waiting.add(key)
                if key != work or not reassign:
                    changed = self.changed_outcomes(key)
                    if changed:
                        invalid.update([key, *changed])
                    if f["state"] == "stale":
                        invalid.update([key, *f["causes"]])
            elif f["state"] in ("reconsidering", "invalidated"):
                invalid.update([key, *f["causes"]])
        # Preserve the assumption ID even when the cause was reached through a decision.
        for key in tuple(invalid):
            row = self.records.get(key)
            if row and row["kind"] == "Observation" and "assumption" in row["fields"]:
                invalid.add(row["fields"]["assumption"])
        return ([("invalidated_support", sorted(invalid))] if invalid else []) + (
            [("unmet_dependency", sorted(waiting))] if waiting else [])

    def edges_for(self, work):
        nodes = self.supports(work) | {work}
        return [{"kind": "depends_on", "from_id": key, "to_id": dest}
                for key in sorted(nodes) for dest in sorted(self.outgoing[key])]
