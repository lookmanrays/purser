"""Typed dependency traversal over one immutable projection."""
from collections import defaultdict


class SupportGraph:
    def __init__(self, records, edges):
        self.records = records
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

    def issues(self, work, *, reassign=False):
        found = self.supports(work)
        invalid, waiting = set(), set()
        for key in found | {work}:
            row = self.records[key]
            kind, f = row["kind"], row["fields"]
            if kind == "WorkItem":
                if key in found and f["state"] != "closed":
                    waiting.add(key)
                if f["state"] == "stale" and (key != work or not reassign):
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
