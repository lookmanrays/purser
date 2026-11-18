"""Read each actual E2 invocation and every committed public state, live or from the ZIP."""

from collections import defaultdict

from experiments.e1.accounting import READ_ERRORS, WORK, journal_prefix, require
from experiments.e1.artifacts import encoded, sha
from experiments.e1.capture import response_evidence
from experiments.e1.measurement import same

from . import attribution as attr
from . import policy
from .public_sources import Originals


def event(kind, payload, original=None):
    return {**(original or {}), "kind": kind, "payload": payload}


class Participant:
    def __init__(self, arm, journal, platform, read, issue):
        self.arm, self.read, self.issue = arm, read, issue
        self.source = Originals(arm, platform, read, issue)
        self.trace = self.source.trace
        self.public_complete = self.source.complete
        self.actions_complete = self.public_complete
        self.receipt_complete = self.public_complete
        self.reads_complete = True
        self.clean_exposure = None
        self.fault_receipt = None
        self.action_lower_bound = 0
        self.timeline, self.journal = [], []
        self.final = self.checkpoint(platform.get("final_public"))
        self.ordered, self.claimed = [], {}
        self.requests, self.plan_attempts = {}, defaultdict(list)
        self.known_issues = set()
        self.denied, self.arrivals = [], []
        self.visible_change = False
        self.start = self.source.initial
        self.points = self.source.publications
        self.numbered = {}
        original = journal if type(journal) is list else []
        if any(type(row) is dict and row.get("kind") == "public_gap" for row in original):
            ordered = False
        else:
            original, ordered = journal_prefix(journal, arm, issue)
        if not ordered:
            self.incomplete("public invocation journal is discontinuous")
        for index, p in enumerate(self.points):
            if self.start is None or index <= self.start:
                continue
            if p.receipt is not None:
                self.requests[attr.receipt_key(p.receipt)] = index
            else:
                self.plan_attempts[p.attempt].append(index)
        for slot, row in enumerate(original):
            p = row.get("payload", {})
            try:
                if row["kind"] == "action":
                    self.action(row, slot)
                elif row["kind"] == "fault":
                    self.fault(row, slot)
                elif row["kind"] == "public_state":
                    states = self.checkpoint(p.get("checkpoint"))
                    if states is not None:
                        require(states[p["subject"]] == (p["to"], p["candidate"]),
                                "recorded public state differs from participant bytes")
                    if not self.public_complete:
                        self.order(None, 1, slot, row)
                elif row["kind"] in ("terminal", "oracle"):
                    self.order(len(self.points) + 1, 3, slot, row)
            except READ_ERRORS as error:
                self.incomplete(f"public event {row.get('seq', slot + 1)}: {error}")
        self.nesting()
        self.publications()
        if not self.public_complete:
            # With no complete original chain, ordinal comparisons between independent
            # surviving side views are unavailable. Their recorded order still proves endpoints.
            self.ordered.sort(key=lambda item: (item[2], item[1]))
        else:
            self.ordered.sort(key=lambda item: item[:3])
        self.journal = [dict(row, seq=seq) for seq, (*_, row) in enumerate(self.ordered, 1)]
        if self.visible_change:
            self.clean_exposure = False
        elif self.public_complete and self.actions_complete and self.reads_complete:
            self.clean_exposure = True
        if len(self.arrivals) == 1:
            self.fault_receipt = self.arrivals[0]
        elif not self.arrivals and self.public_complete:
            self.fault_receipt = False

    def checkpoint(self, ck):
        return self.source.lookup(ck)

    def incomplete(self, message):
        self.actions_complete = False
        self.issue(message)

    def dispute(self, message):
        self.receipt_complete = False
        self.issue(message)

    def order(self, index, phase, slot, row):
        self.ordered.append((index if index is not None else -1, phase, slot, row))

    def evidence(self, action):
        if "return" not in action:
            return False
        if "failure" in action["return"]:
            return False
        try:
            require(same(self.read(action["response_evidence"]), response_evidence(action)),
                    "response envelope differs from observed public return")
        except READ_ERRORS as error:
            self.dispute(f"public response {action.get('attempt')}: {error}")
            return False
        return True

    def port_bounds(self, p, method):
        for name in ("before", "after", "port_before", "port_after"):
            if name in p:
                self.checkpoint(p[name])
        before = self.source.position(p.get("before"))
        after = self.source.position(p.get("after"))
        entered = self.source.position(p.get("port_before", p.get("before")))
        exited = self.source.position(p.get("port_after", p.get("after")))
        if self.public_complete:
            require(before is not None and after is not None and before <= after, "invalid invocation boundaries")
            if method:
                require(entered is not None and exited is not None and before <= entered <= exited <= after,
                        "actual port boundaries outside invocation")
        return before, entered, exited

    def action(self, row, slot):
        p = dict(row["payload"])
        number = p["attempt"]
        require(type(number) is int and number > 0 and number not in self.numbered, "duplicate attempt identity")
        self.action_lower_bound += 1
        self.numbered[number] = p
        self.timeline.append(dict(p, kind="action"))
        method = attr.expected(p)
        if self.arm == "purser":
            for request in p.get("requests") or ([p["proposal"]] if p.get("proposal") else []):
                try:
                    index = self.requests.get(attr.request_key(request))
                    if index is not None:
                        self.known_issues.add(index)
                except READ_ERRORS:
                    pass
        outer = self.source.position(p.get("before"))
        self.order(outer, 2, slot, event("action", p, row))
        # Submission at an admitted original batch is proved separately below.
        p["publicly_submitted"] = False
        authenticated_return = self.evidence(p)
        p["return_authenticated"] = authenticated_return
        if p["action"] == "context":
            if not authenticated_return:
                self.reads_complete = False
            elif attr.revised(p["return"]):
                self.visible_change = True
        self.visible_change |= attr.revised(p.get("input", {})) or attr.revised(p.get("port_input", {}))
        if "parent_attempt" not in p or type(p.get("issued_monotonic_ns")) is not int:
            self.incomplete(f"public attempt {number} has no physical issue and parent identity")
        if "finished_monotonic_ns" in p and (type(p["finished_monotonic_ns"]) is not int or
                type(p.get("issued_monotonic_ns")) is not int or p["finished_monotonic_ns"] < p["issued_monotonic_ns"]):
            self.incomplete(f"public attempt {number} has invalid physical completion")
        try:
            _, entered, exited = self.port_bounds(p, method)
            if p.get("port_method") is not None:
                require(p["port_method"] == method, "observed port disagrees with public operation")
                op = attr.PORTS[method][2]
                if "port_input" in p:
                    require(same(p["input"].get(op), p["port_input"].get(op)), "observed port operand differs")
                else:
                    self.incomplete(f"public attempt {number} lacks its original port operand")
            elif method and p.get("return") is not None and "failure" not in p["return"]:
                self.incomplete(f"public attempt {number} lacks its actual participant-port witness")
            if self.arm == "purser":
                self.kernel_action(p, method, entered, exited)
            elif method and self.points:
                self.plan_action(p, method, entered, exited)
        except READ_ERRORS as error:
            self.incomplete(f"public attempt {number} attribution: {error}")
            self.dispute(f"unresolved public operation at attempt {number}")

    def kernel_action(self, p, method, entered, exited):
        requests = p.get("requests", [])
        if not requests and p.get("proposal"):
            requests = [p["proposal"]]
        if not requests:
            require(not method or "return" not in p or "failure" in p["return"],
                    "participant port returned without an original scoped request")
            return
        require(method is not None and same(p["proposal"], requests[0]), "primary issued proposal ambiguous")
        unique = {}
        for req in requests:
            unique[attr.request_key(req)] = req
        require(len(unique) == 1, "one charged work port issued multiple distinct proposals")
        key, req = next(iter(unique.items()))
        index = self.requests.get(key)
        require(index is not None, "no matching original receipt for issued body, actor and proposal")
        pub = self.points[index]
        prior = self.points[index - 1]
        # Repeated transport of the same request can return its already durable receipt.
        require(entered is not None and exited is not None and index <= exited and
                (entered < index or index in self.claimed), "original admission outside this issued call")
        attr.operation(p, method, req, prior, pub)
        self.claimed.setdefault(index, p["attempt"])
        if method == "submit" and pub.receipt["disposition"] == "admitted":
            p["publicly_submitted"] = True
        try:
            attr.receipt_response(p, method, pub.receipt)
            self.wrapped_response(p, method, pub)
        except READ_ERRORS as error:
            p["return_authenticated"] = False
            self.dispute(f"public attempt {p['attempt']} ({pub.receipt['proposal_id']}): {error}")

    def wrapped_response(self, p, method, pub):
        answer = p.get("return", {})
        if "failure" in answer:
            return
        if method == "observe" and "witness" in answer:
            witness = self.read(answer["witness"])
            original = next(e["data"] for e in pub.events if e["type"] == "ObservationRecorded")
            require(original["artifact_sha256"] == sha(encoded(witness)) and
                    ("port_input" not in p or same(witness, p["port_input"]["observation"])),
                    "returned observer witness differs from the actually observed candidate")
        if method == "verify" and "diagnosis" in answer:
            inputs = p.get("port_input", p["input"])
            label = inputs["criterion"]
            verdict = inputs.get("observation", {}).get("verdict", p["input"].get("verdict"))
            require(same(answer["diagnosis"], [] if verdict == "pass" else [label]),
                    "returned verifier diagnosis differs from verdict")
        if method == "decide":
            for field in ("port_return", "return"):
                if field in p and "failure" not in p[field]:
                    require(same(p[field]["row"], pub.rows[p["subject"]]), "returned closure row differs from work")

    def plan_action(self, p, method, entered, exited):
        require(entered is not None and exited is not None, "plan port lacks its original boundaries")
        if method not in attr.PLAN_WRITES:
            attr.plan_read(p, method, self.points[entered])
            if method == "observe" and "witness" in p.get("return", {}):
                require(same(self.read(p["return"]["witness"]), p["port_input"]["observation"]),
                        "returned plan observer witness differs from actual input")
            return
        indices = [i for i in self.plan_attempts[p["attempt"]] if entered < i <= exited]
        if not indices and ("return" not in p or "failure" in p["return"]):
            return
        require(len(indices) == 1, "plan port has no unique attributable publication")
        index, = indices
        require(index not in self.claimed, "plan publication claimed by another action")
        pub = self.points[index]
        reply = attr.plan_operation(p, method, self.points[index - 1], pub)
        self.claimed[index] = p["attempt"]
        p["publicly_submitted"] = method == "submit"
        try:
            attr.plan_response(p, method, reply)
        except READ_ERRORS as error:
            p["return_authenticated"] = False
            self.dispute(f"public plan attempt {p['attempt']}: {error}")

    def fault(self, row, slot):
        p = dict(row["payload"])
        self.timeline.append(dict(p, kind="fault"))
        self.evidence(p)
        before = self.source.position(p.get("before"))
        after = self.source.position(p.get("after"))
        index = None
        delivered = None
        try:
            message = p["released"]
            require(same(message, policy.notice(p["seed"])) and p["released_sha256"] == sha(encoded(message))
                    and p["attempt"] == p["observation_id"] == message["id"] and p["actor"] == "fixture_observer",
                    "released fixture input differs from registered bytes")
            if self.arm == "purser" and p.get("proposal"):
                request = p["proposal"]
                index = self.requests.get(attr.request_key(request))
                if index is not None:
                    pub = self.points[index]
                    attr.operation(p, "receive", request, self.points[index - 1], pub)
                    delivered = pub.receipt["disposition"] == "admitted"
                    if delivered:
                        self.premise_bytes(message, p["released_sha256"])
                    self.claimed[index] = p["attempt"]
                    try:
                        attr.receipt_response(p, "receive", pub.receipt)
                    except READ_ERRORS as error:
                        self.dispute(f"public fault {p['attempt']}: {error}")
            elif self.arm != "purser":
                indices = [i for i in self.plan_attempts[p["attempt"]] if i not in self.claimed and
                           before is not None and i > before and (after is None or i <= after)]
                if len(indices) == 1:
                    index, = indices
                    pub = self.points[index]
                    reply = attr.plan_operation(p, "receive", self.points[index - 1], pub)
                    delivered = True
                    self.claimed[index] = p["attempt"]
                    try:
                        attr.plan_response(p, "receive", reply)
                    except READ_ERRORS as error:
                        self.dispute(f"public fault {p['attempt']}: {error}")
            if index is not None:
                require(before is not None and before < index and (after is None or index <= after),
                        "original ingress receipt occurred outside the captured attempt")
                p.update(before=self.points[index - 1].checkpoint, after=self.points[index].checkpoint)
            elif self.public_complete and (p.get("disposition") == "head_moved" or before is not None and before == after):
                delivered = False
            else:
                self.dispute("fixture ingress lacks its attributable original publication")
        except READ_ERRORS as error:
            self.dispute(f"invalid original fixture receipt: {error}")
            delivered = None
        self.visible_change |= delivered is True
        self.arrivals.append(delivered)
        p["publicly_delivered"] = delivered
        self.order(index if index is not None else before, 0 if index is not None else 2, slot, event("fault", p, row))

    def premise_bytes(self, message, checksum):
        # The authenticated original PremiseObserved contains the SHA of these exact frozen bytes.
        # A missing redundant archive reference is a quality dispute, independent of delivery.
        try:
            ref = self.trace["kernel_artifacts"][checksum]
            require(ref["sha256"] == checksum and same(self.read(ref), message),
                    "original premise artifact differs from its committed digest")
        except READ_ERRORS as error:
            self.dispute(f"original premise artifact unavailable: {error}")

    def nesting(self):
        last = None
        for number, p in self.numbered.items():
            start = p.get("issued_monotonic_ns")
            if type(start) is not int:
                continue
            if last is not None and start < last:
                self.incomplete(f"public attempt {number} issue precedes a prior issue")
            last = start
            parent = p.get("parent_attempt")
            if parent is None:
                continue
            outer = self.numbered.get(parent)
            if type(parent) is not int or parent >= number or not outer:
                self.incomplete(f"public attempt {number} names an impossible physical parent")
                continue
            end = p.get("finished_monotonic_ns")
            outer_end = outer.get("finished_monotonic_ns")
            if type(outer_end) is int and (start > outer_end or type(end) is int and end > outer_end):
                self.incomplete(f"public attempt {number} extends past its physical parent")

    def publications(self):
        if self.start is None:
            return
        for i in range(self.start + 1, len(self.points)):
            before, pub = self.points[i - 1], self.points[i]
            if self.arm == "purser":
                self.visible_change |= any(attr.revised(e["data"]) for e in pub.events)
            else:
                self.visible_change |= attr.revised(pub.rows["W2"])
            source = self.claimed.get(i)
            if source is None:
                self.incomplete(f"original participant publication {i} has no attributable registered invocation")
                self.dispute(f"unmatched original publication {i}")
                if self.arm == "purser" and i not in self.known_issues:
                    self.unmatched_kernel(i, pub, before)
            for work in WORK:
                if before.states[work] == pub.states[work]:
                    continue
                state, oid = pub.states[work]
                data = {"subject": work, "from": before.states[work][0], "to": state, "candidate": oid,
                        "source_action": source, "checkpoint": pub.checkpoint, "original_publication": True}
                self.order(i, 1, i, event("public_state", data))

    def unmatched_kernel(self, i, pub, before):
        # A complete extra batch proves a public write even when a controller omitted the invocation.
        # Internal derived work/reopen/certificate events never become additional charges.
        for e in pub.events:
            methods = [m for m, spec in attr.PORTS.items() if spec[-1] == e["type"] and spec[-2] == e["actor_id"]]
            if not methods:
                continue
            method, = methods
            fields = e["data"]
            if method == "receive":
                self.unmatched_fault(i, pub, before, fields)
                return
            kind, actor, *_ = attr.PORTS[method]
            work = fields.get("work") or pub.records.get(fields.get("run"), {}).get("work")
            if method == "decide":
                work = fields["id"]
            if method == "revise":
                work = "W2"
            if work not in WORK:
                return
            candidate = pub.states[work][1]
            self.action_lower_bound += 1
            p = {"attempt": f"original-{pub.receipt['revision']}", "actor": actor, "subject": work,
                 "action": kind, "candidate": candidate, "input": {}, "before": before.checkpoint,
                 "after": pub.checkpoint, "publicly_submitted": method == "submit", "inferred_from_original": True}
            self.order(i, 0, i, event("action", p))
            return

    def unmatched_fault(self, i, pub, before, fields):
        checksum = fields.get("artifact_sha256")
        try:
            message = self.read(self.trace["kernel_artifacts"][checksum])
            require(same(policy.notice(message["seed"]), message) and sha(encoded(message)) == checksum,
                    "unidentified original premise")
        except READ_ERRORS:
            return
        if any(r["kind"] == "fault" and r["payload"].get("released_sha256") == checksum
               for *_, r in self.ordered):
            return
        self.arrivals.append(True)
        p = {"attempt": message["id"], "observation_id": message["id"], "released": message,
             "released_sha256": checksum, "seed": message["seed"], "subject": "W2", "actor": "fixture_observer",
             "before": before.checkpoint, "after": pub.checkpoint, "candidate": before.states["W2"][1],
             "publicly_delivered": True, "inferred_from_original": True}
        self.order(i, 0, i, event("fault", p))


def read_participant(arm, journal, platform, read, issue):
    """Return independently established states, attempts, receipt disputes, exposure and physical intervals."""
    return Participant(arm, journal, platform, read, issue)
