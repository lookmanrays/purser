package gate

import (
	"bytes"
	"reflect"
	"testing"
	"time"

	"github.com/lookmanrays/purser/internal/kernel/codec"
	"github.com/lookmanrays/purser/internal/kernel/store"
	"github.com/lookmanrays/purser/internal/testvectors"
)

// AC-1: Every admission reply in wire-v1.json is reproduced exactly, including revisions and details.
func TestAC1_WireAdmissionReplies(t *testing.T) {
	defer guard(t)

	s, w := newTestStore(t)
	clockIdx := 0
	clock := func() time.Time {
		ts, err := codec.ParseTime(w.ClockByBatch[clockIdx])
		if err != nil {
			t.Fatalf("parse clock %d (%s): %v", clockIdx, w.ClockByBatch[clockIdx], err)
		}
		return ts
	}
	g := newTestGate(t, s, clock)
	b := bindingFromWire(w)

	for i, propMap := range w.Proposals {
		clockIdx = i
		raw := encodeProposal(t, propMap)

		parsed, err := ParseProposal(raw)
		if err != nil {
			t.Fatalf("ParseProposal batch %d failed: %v", i, err)
		}
		if parsed.V != 1 {
			t.Errorf("batch %d: parsed.V = %d, want 1", i, parsed.V)
		}
		if parsed.ProposalID != propMap["proposal_id"].(string) {
			t.Errorf("batch %d: parsed.ProposalID = %s, want %s", i, parsed.ProposalID, propMap["proposal_id"].(string))
		}
		if parsed.Commitment != propMap["commitment"].(string) {
			t.Errorf("batch %d: parsed.Commitment = %s, want %s", i, parsed.Commitment, propMap["commitment"].(string))
		}

		digest, err := RequestDigest(b, parsed)
		if err != nil {
			t.Fatalf("RequestDigest batch %d failed: %v", i, err)
		}
		if digest != w.RequestDigests[i] {
			t.Errorf("batch %d: RequestDigest = %s, want %s", i, digest, w.RequestDigests[i])
		}

		receipt, err := g.Admit(b, raw)
		if err != nil {
			t.Fatalf("Admit batch %d failed: %v", i, err)
		}

		wantReceiptMap := w.Expected.AdmissionReplies[i]["stdout"].(map[string]any)["receipt"].(map[string]any)
		if receipt.ActorID != wantReceiptMap["actor_id"].(string) {
			t.Errorf("batch %d: receipt.ActorID = %s, want %s", i, receipt.ActorID, wantReceiptMap["actor_id"].(string))
		}
		if receipt.ProposalID != wantReceiptMap["proposal_id"].(string) {
			t.Errorf("batch %d: receipt.ProposalID = %s, want %s", i, receipt.ProposalID, wantReceiptMap["proposal_id"].(string))
		}
		if receipt.Digest != wantReceiptMap["digest"].(string) {
			t.Errorf("batch %d: receipt.Digest = %s, want %s", i, receipt.Digest, wantReceiptMap["digest"].(string))
		}
		if receipt.Disposition != wantReceiptMap["disposition"].(string) {
			t.Errorf("batch %d: receipt.Disposition = %s, want %s", i, receipt.Disposition, wantReceiptMap["disposition"].(string))
		}
		if receipt.Code != wantReceiptMap["code"].(string) {
			t.Errorf("batch %d: receipt.Code = %s, want %s", i, receipt.Code, wantReceiptMap["code"].(string))
		}
		if receipt.Revision != wantReceiptMap["revision"].(int64) {
			t.Errorf("batch %d: receipt.Revision = %d, want %d", i, receipt.Revision, wantReceiptMap["revision"].(int64))
		}
		wantDetails := wantReceiptMap["details"].(map[string]any)
		if !reflect.DeepEqual(receipt.Details, wantDetails) {
			t.Errorf("batch %d: receipt.Details = %v, want %v", i, receipt.Details, wantDetails)
		}
	}

	// Verify that the stored raw events match the wire events byte for byte
	rawEvents, err := s.RawEvents(1, 8)
	if err != nil {
		t.Fatalf("s.RawEvents(1, 8) failed: %v", err)
	}
	if len(rawEvents) != 8 {
		t.Fatalf("len(rawEvents) = %d, want 8", len(rawEvents))
	}
	for j := 0; j < 8; j++ {
		wantRaw, err := codec.Encode(w.Events[j])
		if err != nil {
			t.Fatalf("codec.Encode w.Events[%d] failed: %v", j, err)
		}
		if !bytes.Equal(rawEvents[j], wantRaw) {
			t.Errorf("event %d raw bytes mismatch:\ngot  %s\nwant %s", j+1, string(rawEvents[j]), string(wantRaw))
		}
	}
}

// AC-2: Every independent_case_branches case produces its expected outcome.
func TestAC2_IndependentCaseBranches(t *testing.T) {
	defer guard(t)

	w := testvectors.Wire()
	b := bindingFromWire(w)

	playToHead := func(t *testing.T, targetHead int64) (*store.Store, *Gate) {
		t.Helper()
		s, _ := newTestStore(t)
		currClock := w.ClockByBatch[0]
		clock := func() time.Time {
			ts, err := codec.ParseTime(currClock)
			if err != nil {
				t.Fatalf("parse clock %s: %v", currClock, err)
			}
			return ts
		}
		g := newTestGate(t, s, clock)

		if targetHead >= 2 {
			currClock = w.ClockByBatch[0]
			if _, err := g.Admit(b, encodeProposal(t, w.Proposals[0])); err != nil {
				t.Fatalf("setup p-create failed: %v", err)
			}
		}
		if targetHead >= 4 {
			currClock = w.ClockByBatch[1]
			if _, err := g.Admit(b, encodeProposal(t, w.Proposals[1])); err != nil {
				t.Fatalf("setup p-hold failed: %v", err)
			}
		}
		if targetHead >= 8 {
			currClock = w.ClockByBatch[2]
			if _, err := g.Admit(b, encodeProposal(t, w.Proposals[2])); err != nil {
				t.Fatalf("setup p-answer failed: %v", err)
			}
		}

		snap, err := s.Snapshot(nil)
		if err != nil {
			t.Fatalf("snapshot failed: %v", err)
		}
		if snap.Revision != targetHead {
			t.Fatalf("setup head = %d, want %d", snap.Revision, targetHead)
		}
		return s, g
	}

	t.Run("Case0_RedeliverCreate", func(t *testing.T) {
		defer guard(t)
		s, g := playToHead(t, 2)
		r, err := g.Admit(b, encodeProposal(t, w.Proposals[0]))
		if err != nil {
			t.Fatalf("redelivery failed: %v", err)
		}
		if r.Revision != 2 || r.Disposition != "admitted" {
			t.Errorf("redelivery = (%s, rev %d), want (admitted, rev 2)", r.Disposition, r.Revision)
		}
		snap, _ := s.Snapshot(nil)
		if snap.Revision != 2 {
			t.Errorf("head revision advanced to %d, want 2 (append 0)", snap.Revision)
		}
	})

	t.Run("Case1_RedeliverHoldAtHead8", func(t *testing.T) {
		defer guard(t)
		s, g := playToHead(t, 8)
		r, err := g.Admit(b, encodeProposal(t, w.Proposals[1]))
		if err != nil {
			t.Fatalf("redeliver hold failed: %v", err)
		}
		if r.Revision != 8 || r.Disposition != "admitted" {
			t.Errorf("receipt = (%s, rev %d), want (admitted, rev 8)", r.Disposition, r.Revision)
		}
		approvalID, _ := r.Details["approval_id"].(string)
		wantApprovalID := "a:19181b8d8107854127291b7431d56405d165271426f0ca411d19e61e7c542884"
		if approvalID != wantApprovalID {
			t.Errorf("approval_id = %s, want %s", approvalID, wantApprovalID)
		}
		snap, _ := s.Snapshot(nil)
		if snap.Revision != 8 {
			t.Errorf("head revision = %d, want 8", snap.Revision)
		}
	})

	t.Run("Case2_RedeliverCreateChangedStatementIDConflict", func(t *testing.T) {
		defer guard(t)
		s, g := playToHead(t, 8)
		modified := map[string]any{
			"v":             int64(1),
			"proposal_id":   "p-create",
			"commitment":    "C-notify",
			"base_revision": int64(0),
			"ops": []any{
				map[string]any{
					"op":        "commitment.create",
					"id":        "C-notify",
					"root":      "W-root",
					"statement": "Changed statement.",
				},
			},
			"justification": []any{},
		}
		_, err := g.Admit(b, encodeProposal(t, modified))
		requireErrorCode(t, err, "id_conflict")

		snap, _ := s.Snapshot(nil)
		if snap.Revision != 8 {
			t.Errorf("head revision = %d, want 8 (append 0)", snap.Revision)
		}
	})

	t.Run("Case3_OrderedProposalsStaleBase", func(t *testing.T) {
		defer guard(t)
		case3 := w.IndependentCaseBranches[3]
		props := toMapSlice(case3["ordered_proposals"])
		expects := case3["expect"].([]any)
		exp0 := expects[0].(map[string]any)
		exp1 := expects[1].(map[string]any)
		exp2 := expects[2].(map[string]any)

		_, g := playToHead(t, 2)

		r0, err := g.Admit(b, encodeProposal(t, props[0]))
		if err != nil {
			t.Fatalf("p-other failed: %v", err)
		}
		if r0.Disposition != exp0["disposition"].(string) || r0.Revision != exp0["revision"].(int64) {
			t.Errorf("p-other = (%s, rev %d), want (%s, rev %d)", r0.Disposition, r0.Revision, exp0["disposition"], exp0["revision"])
		}

		r1, err := g.Admit(b, encodeProposal(t, props[1]))
		if err != nil {
			t.Fatalf("p-edit failed: %v", err)
		}
		if r1.Disposition != exp1["disposition"].(string) || r1.Revision != exp1["revision"].(int64) {
			t.Errorf("p-edit = (%s, rev %d), want (%s, rev %d)", r1.Disposition, r1.Revision, exp1["disposition"], exp1["revision"])
		}

		r2, err := g.Admit(b, encodeProposal(t, props[2]))
		if err != nil {
			t.Fatalf("p-late failed: %v", err)
		}
		if r2.Disposition != exp2["disposition"].(string) || r2.Code != exp2["code"].(string) || r2.Revision != exp2["revision"].(int64) {
			t.Errorf("p-late = (%s, code %s, rev %d), want (%s, %s, rev %d)", r2.Disposition, r2.Code, r2.Revision, exp2["disposition"], exp2["code"], exp2["revision"])
		}
		if !reflect.DeepEqual(r2.Details, exp2["details"].(map[string]any)) {
			t.Errorf("p-late details = %v, want %v", r2.Details, exp2["details"])
		}
	})

	t.Run("Case4_MultiOpBatchIDExists", func(t *testing.T) {
		defer guard(t)
		case4 := w.IndependentCaseBranches[4]
		props := toMapSlice(case4["ordered_proposals"])
		expects := case4["expect"].([]any)
		exp := expects[0].(map[string]any)

		s, g := playToHead(t, 2)
		r, err := g.Admit(b, encodeProposal(t, props[0]))
		if err != nil {
			t.Fatalf("p-batch failed: %v", err)
		}
		if r.Disposition != exp["disposition"].(string) || r.Code != exp["code"].(string) || r.Revision != exp["revision"].(int64) {
			t.Errorf("p-batch = (%s, code %s, rev %d), want (%s, %s, rev %d)", r.Disposition, r.Code, r.Revision, exp["disposition"], exp["code"], exp["revision"])
		}

		snap, _ := s.Snapshot(nil)
		cRec, ok := snap.Projection.Record("C-notify")
		if !ok {
			t.Fatal("C-notify not found in projection")
		}
		stmt := cRec.Fields["statement"].(string)
		wantStmt := exp["unchanged_statement"].(string)
		if stmt != wantStmt {
			t.Errorf("statement = %q, want %q", stmt, wantStmt)
		}
	})

	t.Run("Case5_AnswerAtExactExpiry", func(t *testing.T) {
		defer guard(t)
		s, _ := playToHead(t, 4)

		expiresAtStr := w.Proposals[1]["ops"].([]any)[0].(map[string]any)["expires_at"].(string)
		exactExpiry, err := codec.ParseTime(expiresAtStr)
		if err != nil {
			t.Fatalf("parse expiry: %v", err)
		}
		gAtExpiry := newTestGate(t, s, func() time.Time { return exactExpiry })

		ansProp := map[string]any{
			"v":             int64(1),
			"proposal_id":   "p-answer",
			"commitment":    "C-notify",
			"base_revision": int64(4),
			"ops": []any{
				map[string]any{
					"op":             "approval.answer",
					"id":             "a:19181b8d8107854127291b7431d56405d165271426f0ca411d19e61e7c542884",
					"request_digest": "19181b8d8107854127291b7431d56405d165271426f0ca411d19e61e7c542884",
					"decision":       "approve",
				},
			},
			"justification": []any{},
		}

		ansReceipt, err := gAtExpiry.Admit(b, encodeProposal(t, ansProp))
		if err != nil {
			t.Fatalf("answer admission failed: %v", err)
		}
		if ansReceipt.Disposition != "admitted" {
			t.Errorf("answer receipt disposition = %s, want admitted", ansReceipt.Disposition)
		}
		if ansReceipt.Details["state"] != "expired" {
			t.Errorf("answer details.state = %v, want expired", ansReceipt.Details["state"])
		}

		snap, _ := s.Snapshot(nil)
		holdReceipt, ok := snap.Projection.Receipt("principal:local", "p-hold")
		if !ok {
			t.Fatal("p-hold receipt not found")
		}
		if holdReceipt.Disposition != "rejected" || holdReceipt.Code != "approval_expired" {
			t.Errorf("p-hold origin receipt = (%s, %s), want (rejected, approval_expired)", holdReceipt.Disposition, holdReceipt.Code)
		}

		appID := "a:19181b8d8107854127291b7431d56405d165271426f0ca411d19e61e7c542884"
		appRec, ok := snap.Projection.Record(appID)
		if !ok {
			t.Fatalf("approval record %s not found", appID)
		}
		if appRec.Fields["state"] != "expired" {
			t.Errorf("approval record state = %v, want expired", appRec.Fields["state"])
		}

		cRec, ok := snap.Projection.Record("C-notify")
		if !ok {
			t.Fatal("C-notify record not found")
		}
		if cRec.Fields["state"] != "proposed" {
			t.Errorf("C-notify state = %v, want proposed", cRec.Fields["state"])
		}
	})

	t.Run("Case6_NewAnswerForAlreadyResolvedApproval", func(t *testing.T) {
		defer guard(t)
		s, g := playToHead(t, 8)

		newAnswerProp := map[string]any{
			"v":             int64(1),
			"proposal_id":   "p-answer-2",
			"commitment":    "C-notify",
			"base_revision": int64(8),
			"ops": []any{
				map[string]any{
					"op":             "approval.answer",
					"id":             "a:19181b8d8107854127291b7431d56405d165271426f0ca411d19e61e7c542884",
					"request_digest": "19181b8d8107854127291b7431d56405d165271426f0ca411d19e61e7c542884",
					"decision":       "approve",
				},
			},
			"justification": []any{},
		}

		r, err := g.Admit(b, encodeProposal(t, newAnswerProp))
		if err != nil {
			t.Fatalf("admit new answer failed: %v", err)
		}
		if r.Disposition != "rejected" || r.Code != "already_decided" {
			t.Errorf("receipt = (%s, code %s), want (rejected, already_decided)", r.Disposition, r.Code)
		}

		snap, _ := s.Snapshot(nil)
		holdReceipt, ok := snap.Projection.Receipt("principal:local", "p-hold")
		if !ok || holdReceipt.Disposition != "admitted" {
			t.Errorf("p-hold origin altered: receipt = %v", holdReceipt)
		}
	})
}

// AC-3: Redelivering the same request returns the same receipt and revision and appends nothing.
func TestAC3_RedeliveryReturnsSameReceiptAppendsNothing(t *testing.T) {
	defer guard(t)

	s, w := newTestStore(t)
	clock := func() time.Time {
		ts, _ := codec.ParseTime(w.ClockByBatch[0])
		return ts
	}
	g := newTestGate(t, s, clock)
	b := bindingFromWire(w)

	raw := encodeProposal(t, w.Proposals[0])
	r1, err := g.Admit(b, raw)
	if err != nil {
		t.Fatalf("first Admit failed: %v", err)
	}

	snap1, err := s.Snapshot(nil)
	if err != nil {
		t.Fatalf("snapshot 1 failed: %v", err)
	}

	r2, err := g.Admit(b, raw)
	if err != nil {
		t.Fatalf("second Admit failed: %v", err)
	}

	snap2, err := s.Snapshot(nil)
	if err != nil {
		t.Fatalf("snapshot 2 failed: %v", err)
	}

	if !reflect.DeepEqual(r1, r2) {
		t.Errorf("redelivered receipt differs:\ngot  %+v\nwant %+v", r2, r1)
	}
	if snap1.Revision != snap2.Revision {
		t.Errorf("revision advanced from %d to %d", snap1.Revision, snap2.Revision)
	}
	if snap1.HeadHash != snap2.HeadHash {
		t.Errorf("head hash changed from %s to %s", snap1.HeadHash, snap2.HeadHash)
	}
}

// AC-4: The same actor and proposal ID with a different digest returns id_conflict and appends nothing.
func TestAC4_DifferentDigestReturnsIDConflictAppendsNothing(t *testing.T) {
	defer guard(t)

	s, w := newTestStore(t)
	clock := func() time.Time {
		ts, _ := codec.ParseTime(w.ClockByBatch[0])
		return ts
	}
	g := newTestGate(t, s, clock)
	b := bindingFromWire(w)

	raw1 := encodeProposal(t, w.Proposals[0])
	if _, err := g.Admit(b, raw1); err != nil {
		t.Fatalf("initial Admit failed: %v", err)
	}

	snap1, _ := s.Snapshot(nil)

	differentProposal := map[string]any{
		"v":             int64(1),
		"proposal_id":   "p-create",
		"commitment":    "C-notify",
		"base_revision": int64(0),
		"ops": []any{
			map[string]any{
				"op":        "commitment.create",
				"id":        "C-notify",
				"root":      "W-root",
				"statement": "Different statement content.",
			},
		},
		"justification": []any{},
	}
	raw2 := encodeProposal(t, differentProposal)

	_, err := g.Admit(b, raw2)
	requireErrorCode(t, err, "id_conflict")

	snap2, _ := s.Snapshot(nil)
	if snap1.Revision != snap2.Revision {
		t.Errorf("head revision changed from %d to %d", snap1.Revision, snap2.Revision)
	}
	if snap1.HeadHash != snap2.HeadHash {
		t.Errorf("head hash changed from %s to %s", snap1.HeadHash, snap2.HeadHash)
	}
}

// AC-5: A change to a read dependency after the base gives stale_base with the right conflicts; an unrelated change does not.
func TestAC5_ReadDependencyChangeVsUnrelatedChange(t *testing.T) {
	defer guard(t)

	s, w := newTestStore(t)
	clockTime := func() time.Time {
		ts, _ := codec.ParseTime(w.ClockByBatch[0])
		return ts
	}
	g := newTestGate(t, s, clockTime)
	b := bindingFromWire(w)

	// Revision 2: create C-notify
	if _, err := g.Admit(b, encodeProposal(t, w.Proposals[0])); err != nil {
		t.Fatalf("create C-notify failed: %v", err)
	}

	// Revision 4: unrelated change: create C-doc
	otherProp := map[string]any{
		"v":             int64(1),
		"proposal_id":   "p-doc",
		"commitment":    "C-doc",
		"base_revision": int64(2),
		"ops": []any{
			map[string]any{
				"op":        "commitment.create",
				"id":        "C-doc",
				"root":      "W-doc",
				"statement": "Check release anchors.",
			},
		},
		"justification": []any{},
	}
	rDoc, err := g.Admit(b, encodeProposal(t, otherProp))
	if err != nil {
		t.Fatalf("create C-doc failed: %v", err)
	}
	if rDoc.Disposition != "admitted" || rDoc.Revision != 4 {
		t.Fatalf("rDoc = (%s, rev %d), want (admitted, rev 4)", rDoc.Disposition, rDoc.Revision)
	}

	// Propose editing C-notify with base_revision 2.
	// C-doc changed at 4, but C-notify did not change after revision 2.
	// Unrelated change must NOT stale the proposal!
	editProp := map[string]any{
		"v":             int64(1),
		"proposal_id":   "p-edit",
		"commitment":    "C-notify",
		"base_revision": int64(2),
		"ops": []any{
			map[string]any{
				"op":        "commitment.edit",
				"id":        "C-notify",
				"statement": "Add bounded notification retries.",
			},
		},
		"justification": []any{},
	}
	rEdit, err := g.Admit(b, encodeProposal(t, editProp))
	if err != nil {
		t.Fatalf("edit C-notify on base 2 failed: %v", err)
	}
	if rEdit.Disposition != "admitted" || rEdit.Revision != 6 {
		t.Errorf("rEdit = (%s, rev %d), want (admitted, rev 6)", rEdit.Disposition, rEdit.Revision)
	}

	// Now C-notify changed at revision 5.
	// Propose another edit with base_revision 2: must be rejected with stale_base.
	lateProp := map[string]any{
		"v":             int64(1),
		"proposal_id":   "p-late",
		"commitment":    "C-notify",
		"base_revision": int64(2),
		"ops": []any{
			map[string]any{
				"op":        "commitment.edit",
				"id":        "C-notify",
				"statement": "Another change.",
			},
		},
		"justification": []any{},
	}
	rLate, err := g.Admit(b, encodeProposal(t, lateProp))
	if err != nil {
		t.Fatalf("late edit failed: %v", err)
	}
	if rLate.Disposition != "rejected" || rLate.Code != "stale_base" {
		t.Errorf("rLate = (%s, code %s), want (rejected, stale_base)", rLate.Disposition, rLate.Code)
	}
	conflicts, ok := rLate.Details["conflicts"].([]any)
	if !ok || len(conflicts) != 1 {
		t.Fatalf("conflicts = %v, want 1 conflict", rLate.Details["conflicts"])
	}
	c0 := conflicts[0].(map[string]any)
	if c0["key"] != "C-notify" || c0["changed"].(int64) != 5 {
		t.Errorf("conflict = %v, want {key: C-notify, changed: 5}", c0)
	}
}

// AC-6: When several errors apply, the code follows the specified precedence.
func TestAC6_ErrorPrecedence(t *testing.T) {
	defer guard(t)

	// 1. Envelope error takes precedence over bad base revision
	t.Run("InvalidInput_Over_BadRevision", func(t *testing.T) {
		defer guard(t)
		s, w := newTestStore(t)
		clock := func() time.Time { ts, _ := codec.ParseTime(w.ClockByBatch[0]); return ts }
		g := newTestGate(t, s, clock)
		bPrincipal := bindingFromWire(w)

		malformedJSONWithNegativeBase := []byte(`{"v": 1, "proposal_id": "p-bad", "commitment": "C-notify", "base_revision": -1, "extra": "forbidden", "ops": [], "justification": []}`)
		_, err := g.Admit(bPrincipal, malformedJSONWithNegativeBase)
		requireErrorCode(t, err, "invalid_input")
	})

	// 2. Ingress unauthenticated takes precedence over invalid op
	t.Run("Unauthenticated_Over_InvalidOp", func(t *testing.T) {
		defer guard(t)
		s, w := newTestStore(t)
		clock := func() time.Time { ts, _ := codec.ParseTime(w.ClockByBatch[0]); return ts }
		g := newTestGate(t, s, clock)

		bBad := Binding{ActorID: "", Role: ""}
		propWithInvalidOp := map[string]any{
			"v":             int64(1),
			"proposal_id":   "p-test",
			"commitment":    "C-notify",
			"base_revision": int64(0),
			"ops": []any{
				map[string]any{"op": "commitment.unknown"},
			},
			"justification": []any{},
		}
		_, err := g.Admit(bBad, encodeProposal(t, propWithInvalidOp))
		requireErrorCode(t, err, "unauthenticated")
	})

	// 3. Bad base revision takes precedence over invalid/unknown op
	t.Run("BadRevision_Over_InvalidOp", func(t *testing.T) {
		defer guard(t)
		s, w := newTestStore(t)
		clock := func() time.Time { ts, _ := codec.ParseTime(w.ClockByBatch[0]); return ts }
		g := newTestGate(t, s, clock)
		bPrincipal := bindingFromWire(w)

		if _, err := g.Admit(bPrincipal, encodeProposal(t, w.Proposals[0])); err != nil {
			t.Fatalf("setup failed: %v", err)
		}

		propBadRevInvalidOp := map[string]any{
			"v":             int64(1),
			"proposal_id":   "p-test-rev",
			"commitment":    "C-notify",
			"base_revision": int64(999), // beyond head revision 2
			"ops": []any{
				map[string]any{"op": "commitment.destroy"}, // unknown op
			},
			"justification": []any{},
		}
		r, err := g.Admit(bPrincipal, encodeProposal(t, propBadRevInvalidOp))
		if err != nil {
			t.Fatalf("admit returned unexpected transport error: %v", err)
		}
		if r.Disposition != "rejected" || r.Code != "bad_revision" {
			t.Errorf("receipt = (%s, code %s), want (rejected, bad_revision)", r.Disposition, r.Code)
		}
	})

	// 4. Invalid op syntax takes precedence over forbidden
	t.Run("InvalidOp_Over_Forbidden", func(t *testing.T) {
		defer guard(t)
		s, w := newTestStore(t)
		clock := func() time.Time { ts, _ := codec.ParseTime(w.ClockByBatch[0]); return ts }
		g := newTestGate(t, s, clock)
		bPrincipal := bindingFromWire(w)

		if _, err := g.Admit(bPrincipal, encodeProposal(t, w.Proposals[0])); err != nil {
			t.Fatalf("setup failed: %v", err)
		}

		cNotify := "C-notify"
		bPlanner := Binding{
			ActorID:     "planner-1",
			Role:        "planner",
			PrincipalID: "principal:local",
			Commitment:  &cNotify,
		}
		propMissingField := map[string]any{
			"v":             int64(1),
			"proposal_id":   "p-syntax-forbid",
			"commitment":    "C-notify",
			"base_revision": int64(2),
			"ops": []any{
				map[string]any{"op": "commitment.create", "id": "C-notify"}, // missing root & statement
			},
			"justification": []any{},
		}
		r, err := g.Admit(bPlanner, encodeProposal(t, propMissingField))
		if err != nil {
			t.Fatalf("admit returned transport error: %v", err)
		}
		if r.Disposition != "rejected" || r.Code != "invalid_op" {
			t.Errorf("receipt = (%s, code %s), want (rejected, invalid_op)", r.Disposition, r.Code)
		}
	})

	// 5. Op syntax takes precedence over read conflicts (stale base)
	t.Run("InvalidOp_Over_StaleBase", func(t *testing.T) {
		defer guard(t)
		s, w := newTestStore(t)
		clock := func() time.Time { ts, _ := codec.ParseTime(w.ClockByBatch[0]); return ts }
		g := newTestGate(t, s, clock)
		bPrincipal := bindingFromWire(w)

		if _, err := g.Admit(bPrincipal, encodeProposal(t, w.Proposals[0])); err != nil {
			t.Fatalf("setup failed: %v", err)
		}

		// Advance C-notify to revision 4
		edit1 := map[string]any{
			"v":             int64(1),
			"proposal_id":   "p-edit-adv",
			"commitment":    "C-notify",
			"base_revision": int64(2),
			"ops": []any{
				map[string]any{"op": "commitment.edit", "id": "C-notify", "statement": "Advanced statement"},
			},
			"justification": []any{},
		}
		if _, err := g.Admit(bPrincipal, encodeProposal(t, edit1)); err != nil {
			t.Fatalf("advance edit failed: %v", err)
		}

		// Propose batch on stale base 2 with op 1 reading C-notify and op 2 invalid_op
		propInvalidOnStale := map[string]any{
			"v":             int64(1),
			"proposal_id":   "p-inv-stale",
			"commitment":    "C-notify",
			"base_revision": int64(2), // stale base!
			"ops": []any{
				map[string]any{"op": "commitment.edit", "id": "C-notify", "statement": "Valid edit"},
				map[string]any{"op": "commitment.edit", "id": "C-notify", "statement": ""}, // empty statement: invalid_op
			},
			"justification": []any{},
		}
		r, err := g.Admit(bPrincipal, encodeProposal(t, propInvalidOnStale))
		if err != nil {
			t.Fatalf("admit returned transport error: %v", err)
		}
		if r.Disposition != "rejected" || r.Code != "invalid_op" {
			t.Errorf("receipt = (%s, code %s), want (rejected, invalid_op)", r.Disposition, r.Code)
		}
	})

	// 6. Stale base takes precedence over lifecycle failure
	t.Run("StaleBase_Over_Lifecycle", func(t *testing.T) {
		defer guard(t)
		s, w := newTestStore(t)
		currTime, _ := codec.ParseTime(w.ClockByBatch[0])
		clock := func() time.Time { return currTime }
		g := newTestGate(t, s, clock)
		bPrincipal := bindingFromWire(w)

		// Play wire proposals 0, 1, 2: C-notify is active at head 8
		if _, err := g.Admit(bPrincipal, encodeProposal(t, w.Proposals[0])); err != nil {
			t.Fatalf("p-create failed: %v", err)
		}
		currTime, _ = codec.ParseTime(w.ClockByBatch[1])
		if _, err := g.Admit(bPrincipal, encodeProposal(t, w.Proposals[1])); err != nil {
			t.Fatalf("p-hold failed: %v", err)
		}
		currTime, _ = codec.ParseTime(w.ClockByBatch[2])
		if _, err := g.Admit(bPrincipal, encodeProposal(t, w.Proposals[2])); err != nil {
			t.Fatalf("p-answer failed: %v", err)
		}

		// Edit on stale base 2 yields stale_base
		editOnStale := map[string]any{
			"v":             int64(1),
			"proposal_id":   "p-edit-stale",
			"commitment":    "C-notify",
			"base_revision": int64(2),
			"ops": []any{
				map[string]any{"op": "commitment.edit", "id": "C-notify", "statement": "Edit active"},
			},
			"justification": []any{},
		}
		rStale, err := g.Admit(bPrincipal, encodeProposal(t, editOnStale))
		if err != nil {
			t.Fatalf("admit stale failed: %v", err)
		}
		if rStale.Disposition != "rejected" || rStale.Code != "stale_base" {
			t.Errorf("receipt = (%s, code %s), want (rejected, stale_base)", rStale.Disposition, rStale.Code)
		}

		// Same edit on head base 8 yields invalid_transition
		editOnHead := map[string]any{
			"v":             int64(1),
			"proposal_id":   "p-edit-head",
			"commitment":    "C-notify",
			"base_revision": int64(8),
			"ops": []any{
				map[string]any{"op": "commitment.edit", "id": "C-notify", "statement": "Edit active"},
			},
			"justification": []any{},
		}
		rHead, err := g.Admit(bPrincipal, encodeProposal(t, editOnHead))
		if err != nil {
			t.Fatalf("admit head failed: %v", err)
		}
		if rHead.Disposition != "rejected" || rHead.Code != "invalid_transition" {
			t.Errorf("receipt = (%s, code %s), want (rejected, invalid_transition)", rHead.Disposition, rHead.Code)
		}
	})
}

// AC-7: An approval answered at exactly its expiry resolves as expired.
func TestAC7_ApprovalAnsweredAtExpiryResolvesAsExpired(t *testing.T) {
	defer guard(t)

	s, w := newTestStore(t)
	currTime, _ := codec.ParseTime(w.ClockByBatch[0])
	clock := func() time.Time { return currTime }
	g := newTestGate(t, s, clock)
	b := bindingFromWire(w)

	// Step 1: create commitment C-notify (head 2)
	if _, err := g.Admit(b, encodeProposal(t, w.Proposals[0])); err != nil {
		t.Fatalf("create failed: %v", err)
	}

	// Step 2: request acceptance expiring at 2026-11-19T12:00:00.000000Z (head 4)
	currTime, _ = codec.ParseTime(w.ClockByBatch[1])
	rHold, err := g.Admit(b, encodeProposal(t, w.Proposals[1]))
	if err != nil {
		t.Fatalf("hold failed: %v", err)
	}
	if rHold.Disposition != "pending" || rHold.Code != "needs_principal" {
		t.Fatalf("hold receipt = (%s, %s), want (pending, needs_principal)", rHold.Disposition, rHold.Code)
	}
	approvalID := rHold.Details["approval_id"].(string)

	// Step 3: Answer at EXACT expiry instant read from the proposal
	expiresAtStr := w.Proposals[1]["ops"].([]any)[0].(map[string]any)["expires_at"].(string)
	currTime, _ = codec.ParseTime(expiresAtStr)
	ansProp := map[string]any{
		"v":             int64(1),
		"proposal_id":   "p-ans-expiry",
		"commitment":    "C-notify",
		"base_revision": int64(4),
		"ops": []any{
			map[string]any{
				"op":             "approval.answer",
				"id":             approvalID,
				"request_digest": w.RequestDigests[1],
				"decision":       "approve",
			},
		},
		"justification": []any{},
	}

	ansReceipt, err := g.Admit(b, encodeProposal(t, ansProp))
	if err != nil {
		t.Fatalf("admit answer failed: %v", err)
	}
	if ansReceipt.Disposition != "admitted" {
		t.Errorf("ansReceipt.Disposition = %s, want admitted", ansReceipt.Disposition)
	}
	if ansReceipt.Details["state"] != "expired" {
		t.Errorf("ansReceipt.Details.state = %v, want expired", ansReceipt.Details["state"])
	}

	// Origin approval request receipt must be resolved as rejected / approval_expired
	snap, _ := s.Snapshot(nil)
	originReceipt, ok := snap.Projection.Receipt("principal:local", "p-hold")
	if !ok {
		t.Fatal("origin receipt not found")
	}
	if originReceipt.Disposition != "rejected" || originReceipt.Code != "approval_expired" {
		t.Errorf("originReceipt = (%s, code %s), want (rejected, approval_expired)", originReceipt.Disposition, originReceipt.Code)
	}

	// Approval record must have state "expired"
	appRec, ok := snap.Projection.Record(approvalID)
	if !ok {
		t.Fatalf("approval record %s not found", approvalID)
	}
	if appRec.Fields["state"] != "expired" {
		t.Errorf("approval record state = %v, want expired", appRec.Fields["state"])
	}

	// Commitment must NOT be activated; must remain proposed
	cRec, ok := snap.Projection.Record("C-notify")
	if !ok {
		t.Fatal("C-notify record not found")
	}
	if cRec.Fields["state"] != "proposed" {
		t.Errorf("C-notify state = %v, want proposed", cRec.Fields["state"])
	}

	// Contrast with an approval answered 1 microsecond before deadline
	t.Run("OneMicrosecondBeforeExpiryApproves", func(t *testing.T) {
		defer guard(t)
		s2, w2 := newTestStore(t)
		time2, _ := codec.ParseTime(w2.ClockByBatch[0])
		g2 := newTestGate(t, s2, func() time.Time { return time2 })

		if _, err := g2.Admit(b, encodeProposal(t, w2.Proposals[0])); err != nil {
			t.Fatalf("setup create failed: %v", err)
		}
		time2, _ = codec.ParseTime(w2.ClockByBatch[1])
		if _, err := g2.Admit(b, encodeProposal(t, w2.Proposals[1])); err != nil {
			t.Fatalf("setup hold failed: %v", err)
		}

		expiryInstant, _ := codec.ParseTime(expiresAtStr)
		time2 = expiryInstant.Add(-time.Microsecond)
		rBefore, err := g2.Admit(b, encodeProposal(t, ansProp))
		if err != nil {
			t.Fatalf("admit answer before expiry failed: %v", err)
		}
		if rBefore.Details["state"] != "approved" {
			t.Errorf("state = %v, want approved", rBefore.Details["state"])
		}
		snap2, _ := s2.Snapshot(nil)
		cRec2, _ := snap2.Projection.Record("C-notify")
		if cRec2.Fields["state"] != "active" {
			t.Errorf("commitment state = %v, want active", cRec2.Fields["state"])
		}
	})
}

// AC-8: A failing multi-op batch saves no domain events, only its receipt.
func TestAC8_FailingMultiOpBatchSavesNoDomainEvents(t *testing.T) {
	defer guard(t)

	s, w := newTestStore(t)
	clock := func() time.Time {
		ts, _ := codec.ParseTime(w.ClockByBatch[0])
		return ts
	}
	g := newTestGate(t, s, clock)
	b := bindingFromWire(w)

	// Step 1: create C-notify at revision 2
	if _, err := g.Admit(b, encodeProposal(t, w.Proposals[0])); err != nil {
		t.Fatalf("setup create failed: %v", err)
	}

	snapBefore, _ := s.Snapshot(nil)
	cBefore, ok := snapBefore.Projection.Record("C-notify")
	if !ok {
		t.Fatal("C-notify not found")
	}
	initialStatement := cBefore.Fields["statement"].(string)

	// Step 2: submit multi-op batch where op 1 succeeds tentatively and op 2 fails
	batchProposal := map[string]any{
		"v":             int64(1),
		"proposal_id":   "p-failing-batch",
		"commitment":    "C-notify",
		"base_revision": int64(2),
		"ops": []any{
			map[string]any{
				"op":        "commitment.edit",
				"id":        "C-notify",
				"statement": "Tentative edit statement that must not persist.",
			},
			map[string]any{
				"op":        "commitment.create",
				"id":        "C-notify", // Duplicate ID: triggers id_exists
				"root":      "W-dup",
				"statement": "Duplicate.",
			},
		},
		"justification": []any{},
	}

	r, err := g.Admit(b, encodeProposal(t, batchProposal))
	if err != nil {
		t.Fatalf("admit failed: %v", err)
	}
	if r.Disposition != "rejected" || r.Code != "id_exists" {
		t.Fatalf("receipt = (%s, code %s), want (rejected, id_exists)", r.Disposition, r.Code)
	}

	snapAfter, _ := s.Snapshot(nil)
	// Must append exactly 1 event: the rejection receipt
	if snapAfter.Revision != snapBefore.Revision+1 {
		t.Errorf("head revision after rejection = %d, want %d", snapAfter.Revision, snapBefore.Revision+1)
	}

	// Verify projection: statement must NOT have been changed by tentative op 1
	cAfter, ok := snapAfter.Projection.Record("C-notify")
	if !ok {
		t.Fatal("C-notify record missing after rejection")
	}
	if cAfter.Fields["statement"] != initialStatement {
		t.Errorf("C-notify statement changed to %q, want %q", cAfter.Fields["statement"], initialStatement)
	}

	// Verify events in store: only ProposalReceipted event was recorded
	events, err := s.Events(snapAfter.Revision, snapAfter.Revision)
	if err != nil {
		t.Fatalf("read events failed: %v", err)
	}
	if len(events) != 1 {
		t.Fatalf("events length = %d, want 1", len(events))
	}
	if events[0].Type != "ProposalReceipted" {
		t.Errorf("event type = %s, want ProposalReceipted", events[0].Type)
	}
}
