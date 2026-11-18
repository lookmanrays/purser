"""Identify redundant public views using the same originals as the participant reader."""

from experiments.e1.artifacts import encoded
from experiments.e1.measurement import same

from .public_sources import Originals, identity, view_ref


CHECKPOINT_FIELDS = ("before", "after", "checkpoint", "required_before", "pre_issue_head",
                     "port_before", "port_after", "context_source")


class CorroboratedViews:
    def __init__(self, arm, objects, source, platform, issue, *, unavailable=None):
        self.arm, self.objects, self.issue = arm, objects, issue
        self.originals = Originals(arm, platform, objects.item, issue, unavailable=unavailable)
        self.known, self.derived = {}, {}
        for point in self.originals.publications:
            self.remember(identity(arm, point.checkpoint), point.rows)
        if arm != "purser":
            checkpoints = [platform.get("initial_public"), platform.get("final_public"),
                           (platform.get("checkpoint_measurement") or {}).get("checkpoint")]
            checkpoints.extend(e["payload"][key] for e in source for key in CHECKPOINT_FIELDS if key in e["payload"])
            for ck in checkpoints:
                key = identity(arm, ck)
                if key is not None and key not in self.originals.plan_heads:
                    self.originals.lookup(ck)
            for key, rows in self.originals.plan_heads.items():
                self.remember(key, rows)

    def remember(self, key, rows):
        ref = view_ref(rows)
        self.known[key] = ref
        self.derived[encoded(ref)] = rows

    def item(self, ref):
        key = encoded(ref)
        return self.derived[key] if key in self.derived else self.objects.item(ref)

    def checkpoint(self, ck):
        ref = self.known.get(identity(self.arm, ck))
        if ref is None or same(ck.get("view"), ref):
            return ck
        self.issue("public view reference differs from corroborated participant revision or plan digest")
        return dict(ck, view=ref)

    def events(self, source):
        normalized = []
        for event in source:
            payload = dict(event["payload"])
            for key in CHECKPOINT_FIELDS:
                if key in payload:
                    payload[key] = self.checkpoint(payload[key])
            normalized.append(dict(event, payload=payload))
        return normalized
