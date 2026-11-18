"""Read each actual E2 invocation and every committed public state, live or from the ZIP."""

from experiments.e1.accounting import READ_ERRORS, WORK, journal_prefix, require
from experiments.e1.artifacts import encoded, sha
from experiments.e1.capture import durable_decision, response_evidence, returned_decision
from experiments.e1.measurement import same
from purser.kernel.workview import WorkView

from . import attribution as attr
from . import operations, policy
from .public_sources import Originals, identity


def event(kind, payload, original=None):
    return {**(original or {}), "kind": kind, "payload": payload}


class Participant:
    def __init__(self, arm, journal, platform, read, issue, *, originals=None, objects=None, seed=None,
                 track=None, unavailable=None):
        self.arm, self.read, self.issue = arm, read, issue
        self.unavailable = unavailable or issue
        self.source = originals or Originals(arm, platform, read, issue, unavailable=unavailable,
                                             journal=journal, objects=objects)
        self.seed, self.track = seed, track
        self.qualified_predecessor = (platform.get("checkpoint_measurement") or {}).get("checkpoint")
        self.trace = self.source.trace
        self.public_complete = self.source.complete
        self.modern = type(platform.get("work_admission_version")) is int and platform["work_admission_version"] == 2
        self.issue_gaps = []
        self.preceding_issue = None
        self.actions_complete = self.public_complete and self.modern
        self.ordering_complete = True
        self.receipt_complete = self.public_complete
        self.reads_complete = True
        self.clean_exposure = None
        self.prior_changed_exposure = None
        self.fault_receipt = None
        self.first_delivered = None
        self.action_lower_bound = 0
        self.timeline, self.journal = [], []
        self.final = self.checkpoint(platform.get("final_public"))
        self.ordered, self.claimed = [], {}
        self.detached_claims = {}
        self.submissions = {}
        self.requests = {}
        self.kernel_transmissions = []
        self.known_issues = set()
        self.arrivals = []
        self.visible_change = False
        self.start = self.source.setup
        self.points = self.source.publications
        self.numbered = {}
        self.decisions = {}
        self.received_contexts = []
        self.recorded_states = []
        self.faults = []
        original = journal if type(journal) is list else []
        if any(type(row) is dict and row.get("kind") == "public_gap" for row in original):
            ordered = False
        else:
            original, ordered = journal_prefix(journal, arm, issue, allowed_actions=operations.ACTIONS)
        if not ordered:
            self.incomplete("public invocation journal is discontinuous")
            if not any(row.get("kind") == "public_gap" for row in original):
                self.issue_gaps.append(None)
        if not self.modern:
            self.unavailable("historical producer cannot establish complete physical work-release coverage")
        for index, p in enumerate(self.points):
            if self.start is None or index <= self.start:
                continue
            if p.receipt is not None:
                self.requests[attr.receipt_key(p.receipt)] = index
        for slot, row in enumerate(original):
            p = row.get("payload", {})
            try:
                if row["kind"] == "public_gap":
                    self.issue_gaps.append(self.preceding_issue)
                elif row["kind"] == "action":
                    self.action(row, slot)
                elif row["kind"] == "fault":
                    self.fault(row, slot)
                elif row["kind"] == "public_state":
                    self.recorded_states.append((slot,row))
                    states = self.checkpoint(p.get("checkpoint"))
                    if states is not None:
                        require(states[p["subject"]] == (p["to"], p["candidate"]),
                                "recorded public state differs from participant bytes")
                    if self.source.position(p.get("checkpoint")) is None:
                        self.order(None, 1, slot, row)
                elif row["kind"] in ("terminal", "oracle"):
                    self.order(len(self.points) + 1, 3, slot, row)
            except READ_ERRORS as error:
                self.incomplete(f"public event {row.get('seq', slot + 1)}: {error}")
            if row["kind"] in ("action", "fault") and type(p.get("issued_monotonic_ns")) is int:
                self.preceding_issue = p["issued_monotonic_ns"]
        uncertain_clocks = self.nesting()
        self.publications()
        if self.arm != "purser":
            self.source.order_plan_calls(self.numbered.values(), self.faults, platform, uncertain_clocks)
        self.reconcile_decisions()
        self.order_actions()
        if not self.public_complete:
            # With no complete original chain, ordinal comparisons between independent
            # surviving side views are unavailable. Their recorded order still proves endpoints.
            self.ordered.sort(key=lambda item: (item[2], item[1]))
        else:
            self.ordered.sort(key=lambda item: item[:3])
        self.journal = [dict(row, seq=seq) for seq, (*_, row) in enumerate(self.ordered, 1)]
        if self.arm != "purser":
            self.visible_change |= any(attr.revised(row) for _, row in self.source.plan_contexts("W2"))
        if self.visible_change or self.source.initial_linear is False:
            self.clean_exposure = False
        elif self.public_complete and self.actions_complete and self.reads_complete and self.source.initial_linear is True:
            self.clean_exposure = True
        if True in self.arrivals:
            self.fault_receipt = True
        elif self.public_complete and None not in self.arrivals:
            self.fault_receipt = False

    def checkpoint(self, ck):
        return self.source.lookup(ck)

    def issues_through(self, end=None):
        """The versioned issuer charges before dispatch. A later gap cannot hide earlier work."""
        return self.modern and all(end is not None and lower is not None and lower >= end
                                   for lower in self.issue_gaps)

    @property
    def issued_coverage(self):
        return self.issues_through()

    def public_states_after(self, fault):
        receipt = fault["payload"].get("after")
        reported = {identity(self.arm, receipt): fault["seq"]}
        for row in self.journal:
            if row["kind"] == "public_state":
                state = row["payload"]
                ck = state.get("checkpoint")
                after_sample = (row["seq"] >= fault["seq"] and not state.get("original_publication")
                                and (self.arm == "purser" or "issued_monotonic_ns" not in fault["payload"]
                                     or state.get("recovery_read_utc")))
                if after_sample or self.source.compare(receipt, ck) in (-1, 0):
                    reported.setdefault(identity(self.arm, ck), row["seq"])
            if row["kind"] == "action" and row["payload"].get("after_intervention") is True:
                for field in ("before", "port_before", "context_source", "port_after", "after"):
                    ck = row["payload"].get(field)
                    key = identity(self.arm, ck)
                    if key is None:
                        continue
                    if self.arm != "purser" or self.checkpoint(ck) is not None and field in ("port_after", "after"):
                        reported.setdefault(key, row["seq"])
        return self.source.states_after(receipt, reported)

    def incomplete(self, message):
        self.actions_complete = False
        self.issue(message)

    def missing(self, message):
        self.actions_complete = False
        self.unavailable(message)

    def dispute(self, message):
        self.receipt_complete = False
        self.issue(message)

    def submission(self, pub, work, oid, attempt, action=None):
        key = identity(self.arm, pub.checkpoint)
        self.submissions.setdefault(key, {"subject": work, "candidate": oid, "attempt": attempt,
                                          "submission_publication": pub.checkpoint})
        if action is not None:
            action.update(publicly_submitted=True, submission_publication=pub.checkpoint)

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
        for name in ("pre_issue_head", "before", "after", "port_before", "port_after"):
            if name in p:
                self.checkpoint(p[name])
        before = self.source.position(p.get("before"))
        after = self.source.position(p.get("after"))
        entered = self.source.position(p.get("port_before", p.get("before")))
        exited = self.source.position(p.get("port_after", p.get("after")))
        if before is None or after is None:
            if self.public_complete:
                self.missing(f"public attempt {p['attempt']} lacks a completed public interval")
        else:
            require(before <= after, "invalid invocation boundaries")
            if method and entered is not None and exited is not None:
                require(before <= entered <= exited <= after, "actual port boundaries outside invocation")
        return before, entered, exited

    def action(self, row, slot):
        p = dict(row["payload"])
        for derived in ("work_publication", "prepared_verified", "delivery_observed_ns", "intended_context_received"):
            p.pop(derived, None)
        number = p["attempt"]
        require(type(number) is int and number > 0 and number not in self.numbered, "duplicate attempt identity")
        self.action_lower_bound += 1
        self.numbered[number] = p
        self.timeline.append(dict(p, kind="action", preceding_issue_ns=self.preceding_issue))
        method = attr.expected(p)
        if self.modern:
            p["rework_eligible"] = attr.rework_eligibility(p)
            try:
                attr.work_request(p, method)
            except READ_ERRORS as error:
                descriptions = [p, p.get("work_request"), p.get("work_started")]
                subjects = [record.get("subject") if isinstance(record, dict) else None
                            for record in descriptions]
                # Keep the physical issue and any scalar that all its descriptions establish.
                self.issue(f"public attempt {number} work identity: {error} "
                           f"(action, request and work entry subjects {subjects})")
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
        p["changed_exposure"] = None
        authenticated_return = self.evidence(p)
        p["return_authenticated"] = authenticated_return
        if "parent_attempt" not in p or type(p.get("issued_monotonic_ns")) is not int:
            self.issue_gaps.append(self.preceding_issue)
            self.missing(f"public attempt {number} has no physical issue and parent identity")
        if "finished_monotonic_ns" in p and (type(p["finished_monotonic_ns"]) is not int or
                type(p.get("issued_monotonic_ns")) is not int or p["finished_monotonic_ns"] < p["issued_monotonic_ns"]):
            self.incomplete(f"public attempt {number} has invalid physical completion")
        try:
            _, entered, exited = self.port_bounds(p, method)
            if method in ("context", "inspect"):
                self.read_action(p, method, entered, exited)
                return
            if method not in attr.PORTS:
                if self.modern and p.get("work_started") is None:
                    if authenticated_return:
                        self.incomplete(f"public attempt {number} returned without physical work entry")
                    else:
                        self.reads_complete = False
                elif method is not None:
                    operand = p.get("work_started", {}).get("input", p["input"])
                    p["changed_exposure"] = attr.revised(operand)
                    self.visible_change |= p["changed_exposure"]
                return
            if p.get("port_method") is not None:
                require(p["port_method"] == method, "observed port disagrees with public operation")
                op = attr.PORTS[method][2]
                if "port_input" in p:
                    require(same(p["input"].get(op), p["port_input"].get(op)), "observed port operand differs")
                    if not self.modern or p.get("work_started") is not None:
                        p["changed_exposure"] = attr.revised(p["port_input"])
                        self.visible_change |= p["changed_exposure"]
                else:
                    self.missing(f"public attempt {number} lacks its original port operand")
            elif method and p.get("return") is not None and "failure" not in p["return"]:
                if self.modern:
                    self.incomplete(f"public attempt {number} lacks its actual participant-port witness")
                else:
                    self.missing(f"public attempt {number} lacks its actual participant-port witness")
            if self.arm == "purser":
                self.kernel_action(p, method, entered, exited)
            elif method and (self.points or self.source.plan_heads):
                self.plan_action(p, method)
        except READ_ERRORS as error:
            self.incomplete(f"public attempt {number} attribution: {error}")
            self.dispute(f"unresolved public operation at attempt {number}")

    def read_action(self, p, method, entered, exited):
        number = p["attempt"]
        if not self.modern:
            self.reads_complete = False
            if method == "context" and p["return_authenticated"]:
                try:
                    released = self.source.context(p.get("before"), p["subject"])
                    if same(released, p["return"]):
                        self.visible_change |= attr.revised(released)
                except READ_ERRORS:
                    pass
            return

        if p.get("port_method") is not None:
            require(p["port_method"] == method, "physical read disagrees with work request")
        try:
            delivered, packet = attr.delivered(p, self.read)
            receipt = p.get("delivery") or {}
            actual_work = receipt.get("subject")
            if "observed_monotonic_ns" in receipt:
                observed = receipt["observed_monotonic_ns"]
                start, end = p.get("issued_monotonic_ns"), p.get("finished_monotonic_ns")
                if type(observed) is int and type(start) is int and start <= observed and (end is None or observed <= end):
                    p["delivery_observed_ns"] = observed
                else:
                    self.dispute(f"receiver observation falls outside public attempt {number}")
            if delivered is True:
                require(actual_work in WORK, "public receipt has no registered subject")
                if method == "context":
                    require(packet["work"]["id"] == actual_work, "receiver subject differs from its packet")
                p["changed_exposure"] = attr.revised(packet)
                self.visible_change |= p["changed_exposure"]
            elif delivered is None:
                self.reads_complete = False
            else:
                p["changed_exposure"] = False
            p["public_delivery"] = delivered
        except READ_ERRORS as error:
            self.reads_complete = False
            self.dispute(f"public attempt {number} receiver: {error}")
            delivered, packet, actual_work = None, None, None
            p["public_delivery"] = None
        if method == "inspect":
            p["received_subject"] = actual_work
            if p.get("work_started") is not None:
                if attr.revised(p["input"]):
                    self.visible_change = p["changed_exposure"] = True
            return

        prepared = None
        if p.get("prepared_context") is not None:
            try:
                require(p.get("port_method") == "context", "prepared context has no matching public port")
                source = p["context_source"]
                position = self.source.position(source)
                if self.public_complete or position is not None:
                    require(position is not None and (entered is None or entered <= position)
                            and (exited is None or position <= exited), "packet source outside the actual read")
                prepared = self.read(p["prepared_context"])
                expected = self.source.context(source, p["subject"])
                require(same(prepared, expected), "prepared work differs from its original participant context")
                require(p.get("port_input", {}).get("work") == p["subject"], "prepared context has wrong work operand")
                state = self.checkpoint(source)
                if p["candidate"] is not None:
                    require(state is not None and state[p["subject"]][1] == p["candidate"],
                            "context request candidate differs from its source")
                p["prepared_verified"] = True
            except READ_ERRORS as error:
                p["prepared_verified"] = False
                if self.public_complete or isinstance(error, ValueError):
                    self.dispute(f"public attempt {number} preparation: {error}")
                else:
                    self.unavailable(f"public attempt {number} original context unavailable: {error}")
        elif delivered:
            self.dispute(f"received context {number} lacks its prepared packet")
        intended = None if delivered is None else False
        if delivered is True:
            if actual_work != p["subject"] or prepared is not None and not same(prepared, packet):
                intended = False
            else:
                intended = True if p.get("prepared_verified") is True else None
        p["intended_context_received"] = intended
        p["received_subject"] = actual_work
        self.received_contexts.append({"attempt": number, "subject": p["subject"], "received_subject": actual_work,
                                      "received": delivered, "intended_received": intended})

    def kernel_action(self, p, method, entered, exited):
        requests = p.get("requests", [])
        if not requests and p.get("proposal"):
            requests = [p["proposal"]]
        if not requests:
            if not self.public_complete and method and ("port_return" in p or "return" in p):
                self.missing(f"public attempt {p['attempt']} lacks its original scoped request")
                return
            require(not method or "return" not in p or "failure" in p["return"],
                    "participant port returned without an original scoped request")
            return
        require(method is not None and ("proposal" not in p or same(p["proposal"], requests[0])),
                "primary issued proposal ambiguous")
        unique = {}
        for req in requests:
            unique[attr.request_key(req)] = req
        require(len(unique) == 1, "one charged work port issued multiple distinct proposals")
        key, req = next(iter(unique.items()))
        index = self.requests.get(key)
        if not self.public_complete and (index is None or entered is None or exited is None):
            from .kernel_history import transmitted
            self.kernel_transmissions.append(transmitted(p, method, req, self.read, self.checkpoint,
                                                        self.issue, self.unavailable))
            self.missing(f"public attempt {p['attempt']} lacks its canonical kernel batch or call boundaries")
            return
        require(index is not None, "no matching original receipt for issued body, actor and proposal")
        pub = self.points[index]
        prior = self.points[index - 1]
        # Repeated transport of the same request can return its already durable receipt.
        require(entered is not None and exited is not None and index <= exited and
                (entered < index or index in self.claimed), "original admission outside this issued call")
        attr.operation(p, method, req, prior, pub)
        self.claimed.setdefault(index, p["attempt"])
        if method == "submit" and pub.receipt["disposition"] == "admitted":
            self.submission(pub, p["subject"], p["candidate"], p["attempt"], p)
        elif method == "submit":
            p["submission_refused_at"] = pub.checkpoint
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
            original = next((e["data"] for e in pub.events if e["type"] == "ObservationRecorded"), None)
            require(original is not None and original["artifact_sha256"] == sha(encoded(witness)) and
                    ("port_input" not in p or same(witness, p["port_input"]["observation"])),
                    "returned observer witness differs from the actually observed candidate")
        if method == "verify" and "diagnosis" in answer:
            inputs = p.get("port_input", p["input"])
            label = inputs["criterion"]
            verdict = inputs.get("observation", {}).get("verdict", p["input"].get("verdict"))
            require(same(answer["diagnosis"], [] if verdict == "pass" else [label]),
                    "returned verifier diagnosis differs from verdict")
        if method == "decide":
            work = p["subject"]
            reasons = WorkView(pub.snapshot["projection"]).reasons(work)
            missing = {ref for reason in reasons if reason["code"] == "missing_evidence" for ref in reason["refs"]}
            diagnosis = sorted(label for label, ref in attr.IDS.items() if ref in missing)
            for field in ("port_return", "return"):
                if field in p and "failure" not in p[field]:
                    reply = p[field]
                    require(same(reply["row"], pub.rows[work]) and same(reply["reasons"], reasons)
                            and same(reply["diagnosis"], diagnosis), "returned closure facts differ from original work")

    def plan_action(self, p, method):
        before, after = p.get("port_before", p.get("before")), p.get("port_after", p.get("after"))
        if method not in attr.PLAN_WRITES:
            if "port_input" not in p and ("return" not in p or "failure" in p["return"]):
                return
            original = self.source.plan_at(before)
            if original is None:
                self.missing(f"public plan attempt {p['attempt']} lacks its original port entry")
                return
            attr.plan_read(p, method, original)
            if method == "observe" and "witness" in p.get("return", {}):
                require(same(self.read(p["return"]["witness"]), p["port_input"]["observation"]),
                        "returned plan observer witness differs from actual input")
            return
        writes = list(self.source.plan_writes(p["attempt"], before, after))
        if not writes and ("return" not in p or "failure" in p["return"]):
            return
        if not writes and not self.public_complete:
            self.missing(f"public plan attempt {p['attempt']} lacks an attributable publication")
            return
        require(len(writes) == 1, "plan port has no unique attributable publication")
        pub, = writes
        index = self.source.position(pub.checkpoint)
        require(index is None or index not in self.claimed, "plan publication claimed by another action")
        edge = self.source.plan_edge(pub.checkpoint)
        if edge is not None:
            reply = attr.plan_operation(p, method, *edge)
        elif method == "submit" and p.get("port_method") == method and p.get("work_started"):
            # Submission overwrites these public work fields independently of their previous state.
            attr.work_request(p, method)
            reply = attr.plan_submission(p, pub)
        else:
            self.missing(f"public plan attempt {p['attempt']} lacks the original preceding work state")
            return
        if index is not None:
            self.claimed[index] = p["attempt"]
        else:
            self.detached_claims[identity(self.arm, pub.checkpoint)] = p["attempt"]
        p["work_publication"] = pub.checkpoint
        if method == "submit":
            self.submission(pub, p["subject"], p["candidate"], p["attempt"], p)
        try:
            attr.plan_response(p, method, reply)
        except READ_ERRORS as error:
            p["return_authenticated"] = False
            self.dispute(f"public plan attempt {p['attempt']}: {error}")

    def notice_at(self, pub, fallback=None):
        """Identify a physically published notice using its original plan write or SQLite event."""
        if self.arm != "purser":
            row = pub.rows["W2"]
            marker = row.get("_publication", {})
            notice = row.get("premise")
            if (not isinstance(notice, dict) or pub.attempt != notice.get("id")
                    or marker.get("attempt") != pub.attempt or marker.get("actor") != "fixture_observer"
                    or marker.get("subject") != "W2"):
                return None
        else:
            record = next((e["data"] for e in pub.events if e["type"] == "PremiseObserved"
                           and e["actor_id"] == attr.PORTS["receive"][-2] and e["data"]["id"] == policy.OBSERVATION), None)
            if not record or record.get("assumption") != policy.ASSUMPTION or record.get("finding") != "contradicts":
                return None
            checksum = record.get("artifact_sha256")
            notice = None
            ref = self.trace.get("kernel_artifacts", {}).get(checksum)
            if ref is not None:
                try:
                    content = self.read(ref)
                    require(checksum == sha(encoded(content)), "premise artifact digest mismatch")
                    notice = content
                except READ_ERRORS as error:
                    self.source.read_failure("original premise artifact", error)
            if notice is None:
                for candidate in (self.trace.get("recovered_bindings", {}).get(checksum), fallback,
                                  policy.notice(self.seed) if self.seed is not None else None):
                    if candidate is not None and sha(encoded(candidate)) == checksum:
                        notice = candidate
                        break
        return notice if isinstance(notice, dict) and same(notice, policy.notice(notice.get("seed"))) else None

    def canonical_fault(self, before, after, message):
        return {"attempt": message["id"], "observation_id": message["id"], "released": message,
                "released_sha256": sha(encoded(message)), "seed": message["seed"], "subject": "W2",
                "actor": "fixture_observer", "candidate": before.states["W2"][1],
                "before": before.checkpoint, "after": after.checkpoint, "publicly_delivered": True}

    def fault(self, row, slot):
        p = dict(row["payload"])
        self.timeline.append(dict(p, kind="fault", preceding_issue_ns=self.preceding_issue))
        self.evidence(p)
        self.checkpoint(p.get("before"))
        self.checkpoint(p.get("after"))
        before, after = self.source.position(p.get("before")), self.source.position(p.get("after"))
        index, delivered = None, None
        message = p.get("released")
        try:
            require(same(message, policy.notice(p["seed"])) and p["released_sha256"] == sha(encoded(message))
                    and p["attempt"] == p["observation_id"] == message["id"] and p["actor"] == "fixture_observer",
                    "released fixture input differs from registered bytes")
            if self.arm == "purser" and p.get("proposal"):
                request = p["proposal"]
                index = self.requests.get(attr.request_key(request))
                if index is not None:
                    pub = self.points[index]
                    attr.operation(p, "receive", request, self.points[index-1], pub)
                    delivered = pub.receipt["disposition"] == "admitted"
                    try:
                        attr.receipt_response(p, "receive", pub.receipt)
                    except READ_ERRORS as error:
                        self.dispute(f"public fault {p['attempt']}: {error}")
            if index is None:
                candidates = [i for i in range((self.start or 0)+1, len(self.points))
                              if i not in self.claimed and (before is None or before < i)
                              and (after is None or i <= after) and same(message, self.notice_at(self.points[i], message))]
                if len(candidates) == 1:
                    index, = candidates
                    delivered = True
                    if self.arm != "purser":
                        reply = attr.plan_operation(p, "receive", self.points[index-1], self.points[index])
                        try:
                            attr.plan_response(p, "receive", reply)
                        except READ_ERRORS as error:
                            self.dispute(f"public fault {p['attempt']}: {error}")
            if index is not None:
                if (before is not None and before >= index) or (after is not None and after < index):
                    self.dispute("original ingress receipt fell outside the reported interval")
                if delivered and not same(message, self.notice_at(self.points[index], message)):
                    self.dispute("original ingress publication does not establish the registered bytes")
                    delivered = None
                self.claimed[index] = p["attempt"]
                p.update(before=self.points[index-1].checkpoint, after=self.points[index].checkpoint)
            elif self.arm != "purser" and not self.public_complete:
                edge = self.source.plan_edge(p.get("after"))
                if (edge and identity(self.arm, edge[0].checkpoint) == identity(self.arm, p.get("before"))
                        and not same(edge[0].rows["W2"].get("premise"), message)):
                    attr.plan_operation(p, "receive", *edge)
                    delivered = True
                    self.detached_claims[identity(self.arm, edge[1].checkpoint)] = p["attempt"]
            if delivered is None and index is None:
                if self.public_complete and (p.get("disposition") == "head_moved" or before is not None and before == after):
                    delivered = False
                else:
                    self.unavailable("fixture ingress lacks its attributable original publication")
        except READ_ERRORS as error:
            self.dispute(f"invalid original fixture receipt: {error}")
            delivered = None
        if delivered is True:
            self.visible_change = True
        p["publicly_delivered"] = delivered
        self.faults.append((index, p))
        self.arrivals.append(delivered)
        self.order(index if index is not None else before, 0 if index is not None else 2, slot, event("fault", p, row))

    def nesting(self):
        issued, uncertain = [], set()
        for number, p in self.numbered.items():
            start = p.get("issued_monotonic_ns")
            if type(start) is not int:
                continue
            reversed_issues = {previous for previous, at in issued if at > start}
            if reversed_issues:
                uncertain.update(reversed_issues | {number})
                self.issue_gaps.append(start)
                self.incomplete(f"public attempt {number} issue precedes a prior issue")
            issued.append((number, start))
            parent = p.get("parent_attempt")
            if parent is None:
                continue
            outer = self.numbered.get(parent) if type(parent) is int else None
            if type(parent) is not int or parent >= number or not outer:
                uncertain.add(number)
                self.issue_gaps.append(start)
                self.incomplete(f"public attempt {number} names an impossible physical parent")
                continue
            end = p.get("finished_monotonic_ns")
            outer_end = outer.get("finished_monotonic_ns")
            if type(outer_end) is int and (start > outer_end or type(end) is int and end > outer_end):
                uncertain.update((parent, number))
                self.incomplete(f"public attempt {number} extends past its physical parent")
        return uncertain

    def publications(self):
        for index, before, pub in self.source.transitions():
            key = identity(self.arm, pub.checkpoint)
            slot = index if index is not None else len(self.ordered)
            if self.arm == "purser":
                self.visible_change |= any(attr.revised(e["data"]) for e in pub.events)
            source = self.claimed.get(index) if index is not None else self.detached_claims.get(key)
            if source is None:
                message = self.notice_at(pub)
                if message is not None:
                    self.recover_fault(index, before, pub, message, slot)
                    source = message["id"]
                else:
                    owner = self.numbered.get(pub.attempt)
                    lower = owner.get("issued_monotonic_ns") if owner else None
                    if lower is None and type(pub.attempt) is int:
                        # The charged issuer serializes attempt numbers. This bounds a
                        # missing issue without dating the completion of its publication.
                        lower = max((a["issued_monotonic_ns"] for n, a in self.numbered.items()
                                     if n < pub.attempt and type(a.get("issued_monotonic_ns")) is int), default=None)
                    self.issue_gaps.append(lower)
                    label = index if index is not None else key
                    self.incomplete(f"original participant publication {label} has no attributable registered invocation")
                    self.dispute(f"unmatched original publication {label}")
                    if index not in self.known_issues:
                        source = self.recover_work(index, pub, before, slot)
            owner = self.numbered.get(source)
            decision = None
            if owner is not None:
                try:
                    decision = durable_decision(pub.rows, owner, pub.snapshot)
                except READ_ERRORS as error:
                    self.dispute(f"decision at original publication {index}: {error}")
            for work in WORK:
                release = decision if decision and owner["subject"] == work else None
                if before.states[work] == pub.states[work] and release is None:
                    continue
                state, oid = pub.states[work]
                data = {"subject": work, "from": before.states[work][0], "to": state, "candidate": oid,
                        "source_action": source, "checkpoint": pub.checkpoint, "original_publication": True,
                        "diagnosis": release["diagnosis"] if release else [], "evidence": release,
                        "effect_disposition": None}
                if release is not None:
                    self.decisions[index,source,work] = data
                self.order(index, 1, slot, event("public_state", data))

    def recover_fault(self, index, before, pub, message, event_slot):
        p = self.canonical_fault(before, pub, message)
        # A missing return cannot undo the published input. A surviving issue
        # record for this digest is the same physical arrival, not a second fault.
        for slot,(prior, recorded) in enumerate(self.faults):
            if (prior is None and recorded.get("publicly_delivered") is None
                    and recorded.get("released_sha256") == p["released_sha256"]):
                recorded.update(p)
                self.faults[slot] = (index, recorded)
                self.arrivals[slot] = True
                for position, (_, _, original_slot, entry) in enumerate(self.ordered):
                    if entry["kind"] == "fault" and entry["payload"] is recorded:
                        self.ordered[position] = (index, 0, original_slot, entry)
                        break
                break
        else:
            p.update(inferred_from_original=True, trigger=self.track, intervention="linear_to_exponential")
            if identity(self.arm, self.qualified_predecessor) == identity(self.arm, before.checkpoint):
                p["required_before"] = self.qualified_predecessor
            self.faults.append((index,p))
            self.arrivals.append(True)
            self.timeline.append(dict(p, kind="fault"))
            self.order(index, 0, event_slot, event("fault", p))
        self.visible_change = True
        if index is not None:
            self.claimed[index] = message["id"]

    def recover_work(self, index, pub, before, slot):
        if self.arm == "purser":
            for e in pub.events:
                method = next((name for name,spec in attr.PORTS.items() if spec[-1] == e["type"]
                               and spec[-2] == e["actor_id"]), None)
                if method is None or method == "receive":
                    continue
                fields = e["data"]
                work = fields.get("work") or pub.records.get(fields.get("run"), {}).get("work")
                if method == "decide":
                    work = fields["id"]
                elif method == "revise":
                    work = "W2"
                kind, actor, *_ = attr.PORTS[method]
                attempt = f"original-{pub.receipt['revision']}"
                break
            else:
                return None
        else:
            if type(pub.attempt) is not int:
                return None
            markers = [(w, row["_publication"]) for w,row in pub.rows.items() if
                       (row.get("_publication") or {}).get("attempt") == pub.attempt and
                       not same(row.get("_publication"),before.rows[w].get("_publication"))]
            if len(markers) != 1:
                return None
            work, marker = markers[0]
            actor, attempt = marker.get("actor"), pub.attempt
            kind = {"executor":"submit","planner":"plan","closure":"decide","independent_verifier":"decide"}.get(actor)
            if kind is None:
                return None
            method = "submit" if kind == "submit" else None
        if work not in WORK:
            return None
        oid = pub.states[work][1]
        if self.arm != "purser" and method == "submit":
            reconstructed = {"subject": work, "candidate": oid, "actor": actor, "attempt": attempt,
                             "input": {"candidate": oid}}
            try:
                attr.plan_operation(reconstructed, method, before, pub)
            except READ_ERRORS as error:
                self.dispute(f"original plan submission {attempt}: {error}")
                return None
        # A retained issue whose original request/return is missing can be this
        # very write. Only a demonstrably additional invocation raises the bound.
        candidates = [p for p in self.numbered.values() if self.arm == "purser"
                      and not (p.get("requests") or p.get("proposal"))
                      and (p["actor"], p["subject"], attr.expected(p)) == (actor, work, method)
                      and self.source.position(p.get("before")) in (None, index-1)]
        if attempt in self.numbered:
            candidates.append(self.numbered[attempt])
        matching = candidates[0] if len(candidates) == 1 else None
        if method == "submit":
            # A retained publication still identifies its real subject when
            # an overlapping caller description is damaged or missing.
            if matching and (matching["actor"], matching["action"], matching["subject"], matching["candidate"]
                             ) != (actor, kind, work, oid):
                matching = None
            self.submission(pub, work, oid, matching["attempt"] if matching else attempt, matching)
        if candidates:
            return matching["attempt"] if matching else None
        self.action_lower_bound += 1
        p = {"attempt": attempt, "actor": actor, "subject": work, "action": kind, "candidate": oid, "input": {},
             "before": before.checkpoint, "after": pub.checkpoint, "publicly_submitted": method == "submit",
             "inferred_from_original": True}
        if method == "submit":
            p["submission_publication"] = pub.checkpoint
        self.order(index, 0, slot, event("action", p))
        return attempt

    def reconcile_decisions(self):
        for slot, row in self.recorded_states:
            p = row["payload"]
            if p.get("evidence") is None:
                continue
            key = self.source.position(p.get("checkpoint")), p.get("source_action"), p.get("subject")
            known = self.decisions.get(key)
            if known is not None:
                evidence = p["evidence"]
                if (not same(known["diagnosis"], p.get("diagnosis"))
                        or not same(known["diagnosis"], evidence.get("diagnosis"))
                        or evidence.get("source_action") != p["source_action"]):
                    self.dispute(f"recorded decision differs from original release at attempt {key[1]}")
            elif self.public_complete:
                action = self.numbered.get(key[1])
                if action is not None and action["actor"] not in ("closure", "independent_verifier"):
                    # A public worker/reviewer can return a diagnosis without a
                    # participant write. Attribute it to its actual recorded reply.
                    response = returned_decision(action) if action.get("return_authenticated") else None
                    if (response and same(response, p["evidence"]) and same(response["diagnosis"], p["diagnosis"])
                            and action["subject"] == p["subject"] and key[0] == self.source.position(action.get("after"))):
                        self.order(key[0], 2, slot, row)
                        continue
                self.dispute(f"decision has no attributable original release at attempt {key[1]}")

    def order_actions(self):
        actual = [(i,notice) for i,notice in self.faults if notice.get("publicly_delivered") is True]
        first_deliveries = [(i, notice) for i, notice in actual if all(
            notice is other or self.source.compare(notice.get("after"), other.get("after")) in (-1, 0)
            for _, other in actual)]
        if not first_deliveries:
            self.ordering_complete = not self.faults
            return
        index, notice = first_deliveries[0]
        self.first_delivered = notice
        first, last = notice.get("issued_monotonic_ns"), notice.get("finished_monotonic_ns")
        prefix = self.issues_through(last) and index is not None and self.source.initial_linear is True
        for _, other in self.faults:
            if other.get("publicly_delivered") is None:
                issued = other.get("issued_monotonic_ns")
                if not (type(issued) is int and type(last) is int and issued >= last
                        or self.source.compare(notice.get("after"), other.get("before")) in (-1, 0)):
                    prefix = False
        prior_changed = self.source.initial_linear is False
        boundary = notice.get("after")
        if self.arm != "purser":
            for key, row in self.source.plan_contexts("W2"):
                if self.source.compare({"plan_sha256": key}, boundary) == -1:
                    prior_changed |= attr.revised(row)
        elif index is not None:
            for pub in self.points[:index]:
                prior_changed |= any(attr.revised(e["data"]) for e in pub.events)
        for _,_,_,entry in self.ordered:
            if entry["kind"] != "action":
                continue
            action = entry["payload"]
            issued = action.get("issued_monotonic_ns")
            end = self.source.position(action.get("after"))
            before = self.source.position(action.get("before"))
            bounds = set()
            if type(first) is int and type(issued) is int and issued < first:
                bounds.add(False)
            elif type(last) is int and type(issued) is int and issued >= last:
                bounds.add(True)
            if self.source.compare(boundary, action.get("pre_issue_head")) in (-1, 0):
                bounds.add(True)
            if index is not None and end is not None and end < index:
                bounds.add(False)
            elif index is not None and before is not None and before < index and self.modern:
                # Version 2 samples its original "before" head immediately after
                # charging the attempt. This proves a pre-notice issue.
                bounds.add(False)
            elif not self.modern and before is not None and index is not None and before >= index:
                # Historical controllers sampled before assigning the issue number.
                bounds.add(True)
            if len(bounds) > 1:
                self.dispute(f"physical issue and public heads disagree on premise order at attempt {action['attempt']}")
            post = next(iter(bounds)) if len(bounds) == 1 else None
            action["after_intervention"] = post
            if post is None:
                self.ordering_complete = False
            if post is True or action.get("changed_exposure") is False:
                continue
            if (action.get("prepared_verified")
                    and self.source.compare(boundary, action.get("context_source")) in (-1, 0)):
                continue
            finished = action.get("finished_monotonic_ns")
            received = action.get("delivery_observed_ns")
            returned_before = (index is not None and end is not None and end < index
                               or type(finished) is int and type(first) is int and finished <= first
                               or type(received) is int and type(first) is int and received < first)
            if returned_before and action.get("changed_exposure") is True:
                prior_changed = True
            else:
                # The issue can precede the premise while the actual receiver
                # obtained its packet later. Preserve uncertainty about exposure.
                prefix = False
        self.prior_changed_exposure = True if prior_changed else False if prefix else None


def read_participant(arm, journal, platform, read, issue, *, originals=None, objects=None, seed=None,
                     track=None, unavailable=None):
    """Reconstruct public facts from the originals and independently retained physical issues and returns."""
    return Participant(arm, journal, platform, read, issue, originals=originals, objects=objects, seed=seed, track=track,
                       unavailable=unavailable)
