"""Recover missing, witnessed E2 SQLite batches on an isolated copy of the case's original prefix."""

import sqlite3
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from experiments.e1.accounting import READ_ERRORS, WORK, digest, kernel_rows, require
from experiments.e1.artifacts import encoded, sha
from experiments.e1.capture import response_evidence
from experiments.e1.measurement import same
from experiments.e1.purser_arm import IDS, PRINCIPAL, SCOPE
from purser.kernel.artifacts import Artifacts
from purser.kernel.gate import admit
from purser.kernel.store import Store
from purser.kernel.wire import KernelError

from . import attribution
from .operations import PORTS
from .public_sources import identity


class MissingProof(Exception):
    """A prerequisite for the next historical batch is unavailable."""


def head(checkpoint):
    return identity("purser", checkpoint)


def observed_heads(journal, platform):
    points = defaultdict(list)

    def add(value):
        key = head(value)
        if key and value not in points[key[0]]:
            points[key[0]].append(value)

    add(platform.get("initial_public"))
    add(platform.get("final_public"))
    add((platform.get("checkpoint_measurement") or {}).get("checkpoint"))
    for row in journal:
        p = row.get("payload", {})
        for field in ("before", "after", "port_before", "port_after", "checkpoint", "context_source", "pre_issue_head"):
            add(p.get(field))
    return points


def committed_artifacts(snapshot):
    named = set()
    for record in snapshot["projection"]["records"]:
        fields = record["fields"]
        if record["kind"] == "Observation":
            named.add(fields["artifact_sha256"])
        elif record["kind"] == "VerificationRun":
            named.update((fields["definition_sha256"], fields["environment_sha256"]))
    return named


class Attachments:
    """Supply only case-recorded bytes or exact content requested by an authenticated kernel record."""

    def __init__(self, store, trace, journal, platform, objects, issue):
        self.store = Artifacts(store.path)
        self.objects, self.issue = objects, issue
        self.refs, self.inline = {}, {}
        retained = trace.get("kernel_artifacts", {})
        definitions = platform.get("definition_artifacts", [])
        for ref in [*(retained.values() if isinstance(retained, dict) else ()),
                    *(definitions if isinstance(definitions, list) else ()), platform.get("environment_artifact")]:
            if ref is not None:
                self.load(ref)
        for checksum, data in trace.get("recovered_bindings", {}).items():
            if sha(encoded(data)) == checksum:
                self.remember(data)
        for p in (row["payload"] for row in journal if row.get("kind") == "action"):
            if p.get("port_method") == "observe":
                operand = p.get("port_input")
                if isinstance(operand, dict) and isinstance(operand.get("observation"), dict):
                    self.remember(operand["observation"])
                for _, reply in self.replies(p):
                    if reply.get("witness"):
                        self.load(reply["witness"])

    def load(self, ref):
        try:
            raw = self.objects.blob(ref)
        except (ValueError, OSError, KeyError, TypeError):
            return None
        checksum = self.store.put(raw)
        self.refs[checksum] = ref
        return raw

    def replies(self, action):
        for field in ("port_return", "return"):
            if isinstance(action.get(field), dict):
                yield field, action[field]
        if action.get("response_evidence"):
            try:
                envelope = self.objects.item(action["response_evidence"])
                if all(same(action[k], envelope[k]) for k in ("attempt", "actor", "subject", "candidate")):
                    if "return" in action and not same(envelope, response_evidence(action)):
                        self.issue("recorded outer response differs from its archived return envelope")
                    if isinstance(envelope["return"], dict):
                        yield "archived outer return", envelope["return"]
                else:
                    self.issue("archived kernel response carries another invocation identity")
            except READ_ERRORS:
                pass  # The exact issued operand and canonical head can still establish the batch.

    def remember(self, data):
        raw = encoded(data)
        self.inline[self.store.put(raw)] = data
        return raw

    def by_digest(self, checksum):
        try:
            return self.store.read(checksum)
        except KernelError as exc:
            if exc.code != "unavailable":
                raise
        # A recorded Observation or VerificationRun already commits to these exact bytes.
        # ZIP pooling may supply the content even if a redundant case reference is lost.
        for suffix in ("bin", "json"):
            name = f"objects/{checksum}.{suffix}"
            try:
                data = self.objects.archive.read(name)
            except (OSError, KeyError):
                continue
            ref = {"path": name, "sha256": checksum, "bytes": len(data)}
            content = self.load(ref)
            if content is not None:
                return content
            self.issue(f"kernel witness bytes disagree with their committed digest {checksum}")
        raise MissingProof(f"kernel witness bytes unavailable for {checksum}")

    def supply_recorded(self, snapshot):
        for checksum in committed_artifacts(snapshot):
            try:
                self.by_digest(checksum)
            except MissingProof:
                pass  # A later gate operation decides which of the surviving bytes are necessary.

    def observation(self, action, candidate, criterion):
        supplied = (action.get("port_input") or {}).get("observation")
        candidates = []
        if isinstance(supplied, dict):
            candidates.append(self.remember(supplied))
        for _, reply in self.replies(action):
            original = self.load(reply["witness"]) if reply.get("witness") else None
            if original is not None and original not in candidates:
                if candidates:
                    self.issue("returned kernel observation disagrees with the actual port operand")
                candidates.append(original)
        if not candidates:
            supplied = (action.get("input") or {}).get("observation")
            if isinstance(supplied, dict):
                candidates.append(self.remember(supplied))
        if not candidates:
            raise MissingProof("original observer operand unavailable")
        definition = self.by_digest(criterion["definition_sha256"])
        environment = self.by_digest(criterion["environment_sha256"])
        return [dict(candidate=candidate, definition=definition, environment=environment, artifact=raw)
                for raw in candidates]

    def retained(self, snapshot):
        named = committed_artifacts(snapshot)
        return ({k: self.refs[k] for k in named & self.refs.keys()},
                {k: self.inline[k] for k in named & self.inline.keys()})


def bounds(action):
    return (action.get("port_before") or action.get("before"),
            action.get("port_after") or action.get("after"))


def calls(journal, start):
    pending = []
    for row in journal:
        p = row.get("payload", {})
        if row.get("kind") not in ("action", "fault") or not (p.get("proposal") or p.get("requests")):
            continue
        before, after = (head(value) for value in bounds(p))
        if after and after[0] <= start:
            continue
        # Issue order can differ from the order of commits in nested physical calls.
        expected = after[0] if after else (before[0] if before else start) + 1
        pending.append((expected, row))
    return [row for _, row in sorted(pending, key=lambda entry: entry[0])]


def issued_request(row):
    p = row["payload"]
    requests = p.get("requests") or []
    if p.get("proposal"):
        requests = [p["proposal"], *requests]
    require(requests and all(same(req, requests[0]) for req in requests), "ambiguous issued gate proposal")
    req = requests[0]
    actor, _, _ = attribution.request_key(req)
    method = "receive" if row["kind"] == "fault" else p.get("port_method") or attribution.expected(p)
    require(method in PORTS, "issued proposal is outside the registered public ports")
    kind, participant, _, opcode, authority, _ = PORTS[method]
    op, = req["body"]["ops"]
    require((p.get("action", row["kind"]), p.get("actor"), op["op"], actor) == (kind, participant, opcode, authority),
            "issued proposal/actor differs from the registered operation")
    return req, method, op


def transmitted(action, method, request, read, lookup, issue, unavailable):
    """Record a physical request/return beyond a canonical gap, with its own scope."""
    actor, proposal, checksum = attribution.request_key(request)
    op, = request["body"]["ops"]
    kind, role, _, opcode, authority, _ = PORTS[method]
    require((action["action"], action["actor"], op["op"], actor) == (kind, role, opcode, authority),
            "transmitted request differs from the registered gate operation")
    work = action["subject"]
    oid = action.get("candidate")
    require(work in WORK and (oid is None or digest(oid, 40)), "invalid transmitted work identity")
    if method == "submit":
        candidate = op["candidate"]
        require(same(candidate, {"kind": "git.commit", "algorithm": "sha1", "digest": oid}),
                "transmitted submission names a different Git subject")
    elif method == "decide":
        require(op["id"] == work, "transmitted closure names a different work item")
    elif method == "assign_again":
        require(op["work"] == work, "transmitted assignment names a different work item")

    after = action.get("port_after") or action.get("after")
    end = head(after)
    record = dict(attempt=action["attempt"], actor=actor, operation=opcode, subject=work, candidate=oid,
                  proposal_id=proposal, request_digest=checksum, operand=op, receipt_revision=None,
                  disposition=None, observed_head=dict(revision=end[0], head_hash=end[1]) if end else None,
                  closure_row=None, receipt_sources=[])
    replies = {}
    if action.get("port_method") == method and isinstance(action.get("port_return"), dict):
        replies["port_return"] = action["port_return"]
    if action.get("response_evidence"):
        try:
            envelope = read(action["response_evidence"])
        except READ_ERRORS:
            unavailable(f"archived kernel return unavailable for {proposal}")
        else:
            if isinstance(envelope, dict) and all(same(envelope.get(k), action[k])
                    for k in ("actor", "attempt", "subject", "candidate")):
                replies["archived_return"] = envelope.get("return")
            else:
                issue(f"archived kernel return names another invocation for {proposal}")

    receipts, closure_rows = {}, []
    for origin, reply in replies.items():
        if not isinstance(reply, dict) or "failure" in reply:
            continue
        try:
            receipt = attribution.response_receipt(method, reply, outer=origin == "archived_return")
            require(attribution.receipt_key(receipt) == (actor, proposal, checksum),
                    "returned receipt does not identify the issued proposal")
            revision = receipt["revision"]
            require(type(revision) is int and revision > 0 and (end is None or revision <= end[0])
                    and receipt["disposition"] in ("admitted", "rejected", "pending")
                    and isinstance(receipt["code"], str) and isinstance(receipt["details"], dict),
                    "returned receipt has invalid fields or postdates its captured endpoint")
            receipts[origin] = receipt
            if method == "decide" and isinstance(reply.get("row"), dict):
                row = reply["row"]
                require(row["work"] == work and isinstance(row["state"], str)
                        and (row["candidate"] is None if oid is None else same(
                            row["candidate"], {"kind":"git.commit", "algorithm":"sha1", "digest":oid})),
                        "returned closure row names different work or candidate")
                states = lookup(after) if end and end[0] == revision else None
                require(states is None or states[work] == (row["state"], oid),
                        "returned closure row differs from the independently observed public head")
                closure_rows.append(row)
        except READ_ERRORS as error:
            issue(f"transmitted kernel {origin} for {proposal}: {error}")
    if receipts:
        first = next(iter(receipts.values()))
        record["receipt_sources"] = list(receipts)
        if all(same(first, reply) for reply in receipts.values()):
            record.update(receipt_revision=first["revision"], disposition=first["disposition"])
        else:
            record["conflicting_receipts"] = receipts
            issue(f"conflicting transmitted gate receipts for {proposal}")
    if closure_rows:
        if all(same(closure_rows[0], row) for row in closure_rows):
            record["closure_row"] = closure_rows[0]
        else:
            issue(f"conflicting transmitted closure rows for {proposal}")
    return record


def bindings(snapshot, p, req, method, op, material):
    actor = req["actor_id"]
    binding = dict(role=actor.split(":")[0], actor_id=actor, principal_id=PRINCIPAL, commitment=SCOPE, assignment=None)
    if method == "receive":
        message = p.get("released")
        if not isinstance(message, dict):
            raise MissingProof("original received premise unavailable")
        raw = material.remember(message)
        require(sha(raw) == p.get("released_sha256"), "premise bytes disagree with recorded digest")
        binding.update(assignment=op["assumption"], premise={"artifact": raw})
    elif method == "submit":
        binding["assignment"] = op["assignment"]
    elif method in ("observe", "verify"):
        binding["assignment"] = op["run"]
        if method == "observe":
            # Treatment.observe supplies this work's submission and the definition for
            # the actual criterion. The gate independently compares both to the run.
            records = {r["id"]: r["fields"] for r in snapshot["projection"]["records"]}
            operand = p.get("port_input") or p.get("input") or {}
            criterion = records.get(IDS.get(operand.get("criterion")))
            candidate = records.get(p.get("subject"), {}).get("candidate")
            if not criterion or not candidate:
                raise MissingProof("cannot recover the observer's submitted work and criterion")
            return [dict(binding, witness=w) for w in material.observation(p, candidate, criterion)]
    return [binding]


def compare_returns(p, method, receipt, material):
    # The physical port can already have returned even though its caller never completed.
    for field, reply in material.replies(p):
        if "failure" in reply:
            continue
        try:
            seen = attribution.response_receipt(method, reply, outer=field != "port_return")
            if not same(seen, receipt):
                material.issue(f"reconstructed gate receipt differs from {field} for {receipt['proposal_id']}")
        except READ_ERRORS:
            material.issue(f"invalid retained {field} for {receipt['proposal_id']}")


def compare_head(snapshot, points, issue):
    rev, wanted = snapshot["revision"], snapshot["head_hash"]
    if not points:
        raise MissingProof(f"no independently observed kernel head at revision {rev}")
    matching = [p for p in points if head(p) == (rev, wanted)]
    require(matching, f"recomputed kernel head differs from original at r{rev}")
    if len(matching) != len(points):
        issue(f"contradictory public kernel heads at r{rev}")
    rows = encoded(kernel_rows(snapshot["projection"]))
    checksum = sha(rows)
    expected = {"sha256": checksum, "path": f"objects/{checksum}.json", "bytes": len(rows)}
    if any(p.get("view") is not None and not same(p["view"], expected) for p in matching):
        issue(f"public view reference differs from independently reproduced rows at r{rev}")


def restore_copy(store, trace):
    store.initialize()
    with sqlite3.connect(store.path) as db:
        db.executemany("INSERT INTO events VALUES (?,?)",
                       ((i, raw.encode()) for i, raw in enumerate(trace["original_events"], 1)))
    snapshot = store.replay()
    require(same(snapshot, trace["snapshot"]), "canonical kernel prefix differs from its snapshot")
    return snapshot


def discard_trial(store, revision):
    # Only the disposable candidate batch is removed. The source case is opened read-only.
    with sqlite3.connect(store.path) as db:
        db.execute("DELETE FROM events WHERE revision > ?", (revision,))
        db.execute("DELETE FROM head")
    store.replay()


def extend(store, current, row, points, material, issue):
    p = row["payload"]
    req, method, op = issued_request(row)
    before, after = (head(value) for value in bounds(p))
    rev = current["revision"]
    if before is not None and before[0] > rev:
        raise MissingProof(f"gap from r{rev} to invocation at r{before[0]}")
    if before is not None:
        prior = current if before[0] == rev else store.snapshot(revision=before[0])
        if before[1] != prior["head_hash"]:
            require(head(p.get("before")) == (rev, current["head_hash"]),
                    f"issued predecessor differs from canonical r{before[0]}")
            issue(f"inner kernel predecessor differs from the authenticated caller head at r{rev}")
    if after is not None and after[0] < rev:
        raise MissingProof("invocation predates the recovered predecessor")
    compare_head(current, points.get(rev), issue)
    choices = bindings(current, p, req, method, op, material)
    for index, binding in enumerate(choices, 1):
        receipt = admit(store, binding, req["body"], clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
        following = store.snapshot()
        if following["revision"] == rev:
            # Stable transport redelivery returns its earlier receipt without a new batch.
            compare_returns(p, method, receipt, material)
            return current
        witnesses = points.get(following["revision"], [])
        if after and after[0] < following["revision"]:
            raise ValueError("reconstructed batch falls after the captured invocation")
        if index < len(choices) and not any(head(w) == (following["revision"], following["head_hash"]) for w in witnesses):
            discard_trial(store, rev)
            continue
        compare_head(following, witnesses, issue)
        compare_returns(p, method, receipt, material)
        return following


def recover_suffix(trace, journal, platform, objects, issue, unavailable):
    """Extend an authenticated original prefix through each independently witnessed gate head."""
    first = trace["snapshot"]["revision"]
    result = dict(trace, original_prefix_revision=trace.get("original_prefix_revision", first),
                  recovered_through_revision=first)
    points = observed_heads(journal, platform)
    if not points or max(points) <= first:
        return result
    with tempfile.TemporaryDirectory(prefix="purser-e2-kernel-prefix-") as tmp:
        store = Store(Path(tmp) / "kernel.sqlite")
        current = restore_copy(store, trace)
        material = Attachments(store, trace, journal, platform, objects, issue)
        material.supply_recorded(current)
        for row in calls(journal, first):
            try:
                current = extend(store, current, row, points, material, issue)
            except MissingProof as error:
                unavailable(f"kernel recovery after r{current['revision']}: {error}")
                break
            except KernelError as error:
                notify = unavailable if error.code == "unavailable" else issue
                notify(f"kernel recovery after r{current['revision']}: {error}")
                break
            except (*READ_ERRORS, OSError) as error:
                issue(f"kernel recovery after r{current['revision']}: {error}")
                break
        final = current["revision"]
        if final < max(points):
            unavailable(f"original kernel batches beyond recovered r{final} are unavailable")
        with sqlite3.connect(store.path) as db:
            events = [raw.decode() for raw, in db.execute("SELECT body FROM events WHERE revision<=? ORDER BY revision",
                                                          (final,))]
        refs, inline = material.retained(current)
        return dict(result, original_events=events, snapshot=current, recovered_through_revision=final,
                    kernel_artifacts=refs, recovered_bindings=inline)
