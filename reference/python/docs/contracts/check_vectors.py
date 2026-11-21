"""Check the published M0 design vectors independently of Purser runtime code."""
import hashlib
import json
import unittest
from pathlib import Path


DIRECTORY = Path(__file__).parent


def load(name):
    return json.loads((DIRECTORY / name).read_bytes())


def canonical(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


def digest(tag, value):
    return hashlib.sha256(b"purser/" + tag.encode("ascii") + b"/v1\0" + canonical(value)).hexdigest()


class ContractVectors(unittest.TestCase):
    def setUp(self):
        self.wire = load("wire-v1.json")
        self.trace = load("lifecycle-v1.json")

    def test_bytes_and_wire_chains(self):
        spelling = self.wire["encoding_vector"]
        raw = canonical(spelling["value"])
        self.assertEqual(raw, spelling["canonical_utf8"].encode("utf-8"))
        self.assertEqual(hashlib.sha256(raw).hexdigest(), spelling["sha256_canonical_utf8"])
        actual = [
            digest("proposal", {"actor_id": self.wire["actor"], "proposal": proposal})
            for proposal in self.wire["proposals"]
        ]
        self.assertEqual(actual, self.wire["request_digests"])
        for events in (self.wire["events"], self.wire["legacy"]["events"]):
            previous = "0" * 64
            previous_end = 0
            for number, event in enumerate(events, 1):
                self.assertEqual(event["revision"], number)
                self.assertEqual(event["prev_hash"], previous)
                if number > previous_end:
                    self.assertEqual(event["batch_start"], number)
                    batch = events[number - 1:number - 1 + event["batch_size"]]
                    self.assertEqual(len(batch), event["batch_size"])
                    for field in ("actor_id", "proposal_id", "at", "batch_start", "batch_size"):
                        self.assertEqual({entry[field] for entry in batch}, {event[field]})
                    previous_end += event["batch_size"]
                fields = {key: value for key, value in event.items() if key != "hash"}
                previous = digest("event", fields)
                self.assertEqual(previous, event["hash"])
        first = self.wire["events"][0]
        legacy = self.wire["legacy"]["events"][0]
        new_payload = dict(legacy["data"])
        new_payload["statement"] = new_payload.pop("text")
        self.assertEqual(new_payload, first["data"])
        self.assertNotEqual(first["hash"], legacy["hash"])
        self.assertEqual(self.wire["legacy"]["events"][-1]["hash"], self.wire["legacy"]["expected_head_hash"])

    def test_projection_and_disposition_checkpoints(self):
        expected = self.wire["expected"]
        events = self.wire["events"]
        statuses = {s["revision"]: s for s in expected["status_by_revision"]}
        self.assertEqual(statuses[2]["projection_hash"], self.wire["legacy"]["expected_projection_hash"])
        for rev in (4, 8):
            snapshot = expected[f"snapshot_at_{rev}"]
            state = snapshot["projection"]
            self.assertEqual(digest("projection", state), statuses[rev]["projection_hash"])
            self.assertEqual(statuses[rev]["head_hash"], events[rev - 1]["hash"])
            reconstructed = {}
            for event in events[:rev]:
                if event["type"] == "ProposalReceipted":
                    receipt = dict(event["data"], revision=event["batch_start"] + event["batch_size"] - 1)
                    reconstructed[receipt["actor_id"], receipt["proposal_id"]] = receipt
            self.assertEqual([reconstructed[k] for k in sorted(reconstructed)], state["receipts"])
            commitment = next(x for x in state["records"] if x["kind"] == "Commitment")
            approval = next(x for x in state["records"] if x["kind"] == "Approval")
            self.assertEqual(approval["id"], "a:" + self.wire["request_digests"][1])
            self.assertEqual(approval["fields"]["request"], self.wire["proposals"][1])
            self.assertEqual(approval["fields"]["state"], "pending" if rev == 4 else "approved")
            self.assertEqual(commitment["fields"]["state"], "proposed" if rev == 4 else "active")
        for result, end in zip(expected["admission_replies"], (2, 4, 8), strict=True):
            event = events[end - 1]
            self.assertEqual(result["stdout"]["receipt"], dict(event["data"], revision=end))

    def test_candidate_lineage_and_git_receipt(self):
        trace = self.trace
        earlier, current = trace["evidence_bindings"]
        self.assertNotEqual(earlier["candidate"], current["candidate"])
        self.assertNotEqual(earlier["generation"], current["generation"])
        self.assertEqual(current["candidate"], trace["subjects"]["B"])
        for record in (earlier, current):
            self.assertNotIn(record["producer"], (record["observer"], record["verifier"]))
        effect = trace["immutable_effect"]
        self.assertEqual(digest("effect", effect["intent"]), effect["intent_digest"])
        self.assertEqual(effect["protected_receipt_ref"], "refs/purser/receipts/" + effect["intent_digest"])
        outcomes = {}
        for item in trace["outcome_vectors"]:
            basis = item["basis"]
            self.assertEqual(item["digest"], digest("outcome", basis))
            for witness in basis["witnesses"]:
                self.assertEqual(witness["outcome"], outcomes[witness["work"]])
            outcomes[basis["work"]] = item["digest"]
        final = trace["steps"][-1]["required_observations"]
        self.assertEqual(final["accepted_outcome"], outcomes["W-root"])
        self.assertEqual(final["external_transition_count_for_this_operation"], 1)
        self.assertEqual(trace["alternative_cutoff_orders"][0]["external_transition_count"], 0)
        self.assertTrue(trace["alternative_cutoff_orders"][1]["T1_dispatching_at_revocation"])


if __name__ == "__main__":
    unittest.main()
