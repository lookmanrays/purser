"""Recover a missing checkpoint view only from an independently matching public identity."""

from experiments.e1.accounting import READ_ERRORS, kernel_history, public_rows
from experiments.e1.artifacts import encoded, sha
from experiments.e1.measurement import same


class CorroboratedViews:
    def __init__(self, arm, objects, source, platform, issue):
        self.arm, self.objects, self.issue = arm, objects, issue
        self.known, self.derived = {}, {}
        self.original_kernel = False
        checkpoints = [platform.get("initial_public"), platform.get("final_public")]
        measure = platform.get("checkpoint_measurement") or {}
        checkpoints.append(measure.get("checkpoint"))
        for e in source:
            checkpoints.extend(e["payload"].get(k) for k in ("before","after","checkpoint","required_before"))
        trace = None
        ref = platform.get("treatment_trace")
        if ref:
            try:
                trace = objects.item(ref)
            except READ_ERRORS:
                pass
        if arm == "purser" and trace:
            try:
                revisions = {ck["revision"] for ck in checkpoints if type(ck) is dict and type(ck.get("revision")) is int}
                verified = kernel_history(trace,revisions)
                self.original_kernel = True
                for rev, (head,rows,_) in verified.items():
                    self.remember((rev,head),rows)
            except READ_ERRORS:
                # The full original history is independently checked by PublicHistory.
                pass
        if arm != "purser":
            if trace and "plan" in trace:
                plan = trace["plan"]
                self.remember(sha(encoded(plan)),plan)
            for ck in checkpoints:
                identity = self.identity(ck)
                if identity is None or identity in self.known:
                    continue
                try:
                    public = objects.item(ck["view"])
                    if sha(encoded(public)) == identity:
                        public_rows(public,arm)
                        self.remember(identity,public)
                except READ_ERRORS:
                    pass

    def identity(self, ck):
        if type(ck) is not dict:
            return None
        if self.arm == "purser":
            rev = ck.get("revision")
            return (rev,ck["head_hash"]) if type(rev) is int and type(ck.get("head_hash")) is str else None
        return ck.get("plan_sha256")

    def remember(self, identity, rows):
        raw = encoded(rows)
        digest = sha(raw)
        ref = {"path": f"objects/{digest}.json", "sha256": digest, "bytes": len(raw)}
        self.known[identity] = ref
        self.derived[encoded(ref)] = rows

    def item(self, ref):
        key = encoded(ref)
        if key in self.derived:
            return self.derived[key]
        return self.objects.item(ref)

    def checkpoint(self, ck):
        ref = self.known.get(self.identity(ck))
        if ref is None or same(ck.get("view"),ref):
            return ck
        self.issue("public view reference differs from corroborated participant revision or plan digest")
        return dict(ck,view=ref)

    def events(self, source):
        normalized = []
        for e in source:
            p = dict(e["payload"])
            for key in ("before","after","checkpoint","required_before"):
                if key in p:
                    p[key] = self.checkpoint(p[key])
            normalized.append(dict(e,payload=p))
        return normalized
