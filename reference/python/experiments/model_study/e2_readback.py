"""Supply original model-arm publications, action order and sealed grades to the E2 scorer."""

from experiments.e1.artifacts import encoded
from experiments.e1.report import require
from experiments.e2 import checkpoint, evidence, outcomes, policy
from experiments.e2.public_history import PublishedHistory
from experiments.e2.public_sources import Originals, identity


def history_for(arm, seed, rows, platform, objects):
    disputes, missing = [], []
    source = Originals(arm, platform, objects.item, disputes.append, unavailable=missing.append,
                       journal=rows, objects=objects)
    require(not disputes, "; ".join(disputes))
    # Attribution is a read-only reconstruction. Keep the archived journal intact.
    journal = [{**r, "payload": dict(r["payload"])} for r in rows]
    history = PublishedHistory(source, journal, seed)
    history.diagnostics = missing
    return history


def public_points(history, points, complete):
    """Recover every available checked endpoint and its original plan row or kernel projection."""
    positions, sources = {}, {}
    source = history.source
    for point in points:
        key = encoded(point)
        states = source.lookup(point)
        require(states is not None or not complete, "original E2 public endpoint unavailable")
        if states is None:
            continue
        positions[key] = states
        index = source.position(point)
        publication = source.publications[index] if index is not None else None
        if publication is None and history.arm != "purser":
            publication = source.plan_at(point)
        if publication:
            sources[key] = publication.snapshot["projection"] if history.arm == "purser" else publication.rows["W2"]
    return positions, sources


def qualification(history, fault, predecessor, periods, repos):
    """Bind the actual delivery and prior W2 submission to the originally qualified A1 head."""
    source, arm, old = history.source, history.arm, fault["payload"]["candidate"]
    before = predecessor.states
    submitted = None
    for row in history.journal[:fault["seq"] - 1]:
        p = row["payload"]
        if row["kind"] != "action" or (p["action"], p["subject"], p["candidate"]) != ("submit", "W2", old):
            continue
        end = p.get("after")
        index = source.position(end)
        if index is None or source.compare(end, predecessor.checkpoint) not in (0, -1):
            continue
        pub = source.publications[index]
        if (arm == "purser" and pub.receipt and pub.receipt["disposition"] == "admitted" and
                any(event["type"] == "ResultSubmitted" and event["data"]["work"] == "W2"
                    and event["data"]["candidate"] == {"kind": "git.commit", "algorithm": "sha1", "digest": old}
                    for event in pub.events)
                or arm != "purser" and pub.attempt == p["attempt"] and pub.rows["W2"]["state"] == "submitted"
                and pub.rows["W2"]["candidate"] == old):
            submitted = pub
    blobs = {c: evidence.file_ids(repos[c], c) for c in checkpoint.required_cases(before, old) if c in repos}
    unchanged = {w: checkpoint.unchanged_policy(blobs, w, before[w][1], old) for w in ("W1", "W3")}
    def original(pub):
        value = pub.snapshot["projection"] if arm == "purser" else pub.rows["W2"]
        return checkpoint.original_retry_policy(arm, value)
    pause = any(identity(arm, interval["checkpoint"]) == identity(arm, predecessor.checkpoint)
                and interval["candidate"] == old for interval in periods)
    law = outcomes.conjunction([original(predecessor), original(submitted) if submitted else None, pause or None])
    return law, True if submitted else None, unchanged


def assess(history, track, grades, repos, periods, final_oid, *, endpoint_known, action_sequence_complete):
    source, arm = history.source, history.arm
    faults = [r for r in history.journal if r["kind"] == "fault"]
    requested = policy.notice(history.seed)
    physical = [(before, after) for _, before, after in source.transitions() if history.notice_at(after) == requested]
    require(len(faults) <= 1 and len(physical) <= 1 and (track != "clean" or not faults and not physical),
            "E2 original has multiple notices or a changed clean premise")
    delivery = False if source.complete else None
    law = submitted = None
    unchanged = {}
    if faults:
        fault, = faults
        p = fault["payload"]
        require(p["released"] == requested and p["trigger"] == p["before"], "changed premise differs from its release")
        if physical:
            before, after = physical[0]
            require(identity(arm, before.checkpoint) == identity(arm, p["before"]) and
                    ("after" not in p or identity(arm, after.checkpoint) == identity(arm, p["after"])),
                    "original E2 notice disagrees with its physical publication")
            p["after"] = after.checkpoint
            history.first_delivered = p
            delivery = True
            law, submitted, unchanged = qualification(history, fault, before, periods, repos)
        p["publicly_delivered"] = delivery
        if delivery:
            for r in history.journal:
                if r["kind"] == "action":
                    action = r["payload"]
                    order = source.compare(p["after"], action.get("before"))
                    action["after_intervention"] = order in (-1, 0) if order is not None else None
                    # The collector admits these serial one-subject actions before dispatch.
                    if action["after_intervention"] is not None:
                        require(action["after_intervention"] == (r["seq"] > fault["seq"]),
                                "E2 action order disagrees with the original notice")
    else:
        require(not physical, "physical E2 notice has no original release in the case journal")
    facts = outcomes.assess(history, track, grades, delivery=delivery, qualification=law, submitted=submitted,
                            unchanged=unchanged, final_oid=final_oid, endpoint_known=endpoint_known,
                            action_sequence_complete=action_sequence_complete)
    facts["publicly_delivered"] = delivery
    facts["public_observation_gaps"] = history.diagnostics
    if delivery is True and facts["intervention_reached"] is False:
        facts["qualification_dispute"] = "The observed E2 delivery contradicts its registered checkpoint criterion."
    facts["eligible"] = (facts["intervention_reached"] if track != "clean" else
                         outcomes.conjunction([source.initial_linear, source.complete or None,
                                               None if delivery is None else not delivery, endpoint_known or None]))
    return facts
