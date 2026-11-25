package gate

import (
	"bytes"
	"encoding/json"
	"os"
	"reflect"
	"testing"
	"time"

	"github.com/lookmanrays/purser/internal/kernel/codec"
	"github.com/lookmanrays/purser/internal/kernel/projection"
)

type ac9Receipt struct {
	ActorID     string         `json:"actor_id"`
	ProposalID  string         `json:"proposal_id"`
	Digest      string         `json:"digest"`
	Disposition string         `json:"disposition"`
	Code        string         `json:"code"`
	Details     map[string]any `json:"details"`
	Revision    int64          `json:"revision"`
}

type ac9Case struct {
	Name                 string          `json:"name"`
	Proposal             json.RawMessage `json:"proposal"`
	ExpectedReceipt      ac9Receipt      `json:"expected_receipt"`
	ExpectedHeadRevision int64           `json:"expected_head_revision"`
}

type ac9MultiOpCase struct {
	Proposal             json.RawMessage `json:"proposal"`
	ExpectedReceipt      ac9Receipt      `json:"expected_receipt"`
	ExpectedHeadRevision int64           `json:"expected_head_revision"`
}

type ac9PrecedenceBadRevision struct {
	Proposal        json.RawMessage `json:"proposal"`
	ExpectedReceipt ac9Receipt      `json:"expected_receipt"`
}

type ac9PrecedenceForbidden struct {
	Binding         map[string]any  `json:"binding"`
	Proposal        json.RawMessage `json:"proposal"`
	ExpectedReceipt ac9Receipt      `json:"expected_receipt"`
}

type ac9ConflictCase struct {
	First                 json.RawMessage `json:"first"`
	Changed               json.RawMessage `json:"changed"`
	ExpectedReceipt       ac9Receipt      `json:"expected_receipt"`
	ExpectedConflictError string          `json:"expected_conflict_error"`
	ChangedDigest         string          `json:"changed_digest"`
}

type ac9Fixture struct {
	Comment               string                   `json:"_comment"`
	Cases                 []ac9Case                `json:"cases"`
	MultiOp               ac9MultiOpCase           `json:"multi_op"`
	PrecedenceBadRevision ac9PrecedenceBadRevision `json:"precedence_bad_revision"`
	PrecedenceForbidden   ac9PrecedenceForbidden   `json:"precedence_forbidden"`
	Conflict              ac9ConflictCase          `json:"conflict"`
}

func readAC9Fixture(t *testing.T) *ac9Fixture {
	t.Helper()
	data, err := os.ReadFile("testdata/ac9_non_object_ops.json")
	if err != nil {
		t.Fatalf("read testdata/ac9_non_object_ops.json failed: %v", err)
	}
	var f ac9Fixture
	if err := json.Unmarshal(data, &f); err != nil {
		t.Fatalf("unmarshal testdata/ac9_non_object_ops.json failed: %v", err)
	}
	return &f
}

func assertReceiptMatches(t *testing.T, got projection.Receipt, want ac9Receipt) {
	t.Helper()
	if got.ActorID != want.ActorID {
		t.Errorf("receipt.ActorID = %q, want %q", got.ActorID, want.ActorID)
	}
	if got.ProposalID != want.ProposalID {
		t.Errorf("receipt.ProposalID = %q, want %q", got.ProposalID, want.ProposalID)
	}
	if got.Digest != want.Digest {
		t.Errorf("receipt.Digest = %q, want %q", got.Digest, want.Digest)
	}
	if got.Disposition != want.Disposition {
		t.Errorf("receipt.Disposition = %q, want %q", got.Disposition, want.Disposition)
	}
	if got.Code != want.Code {
		t.Errorf("receipt.Code = %q, want %q", got.Code, want.Code)
	}
	if got.Revision != want.Revision {
		t.Errorf("receipt.Revision = %d, want %d", got.Revision, want.Revision)
	}
	if !reflect.DeepEqual(got.Details, want.Details) {
		t.Errorf("receipt.Details = %v, want %v", got.Details, want.Details)
	}
}

func parseAndCheckDigest(t *testing.T, b Binding, raw []byte, propMap map[string]any, wantDigest string) {
	t.Helper()
	parsed, err := ParseProposal(raw)
	if err != nil {
		t.Fatalf("ParseProposal failed: %v", err)
	}
	gotOps, err := codec.Encode(parsed.Ops)
	if err != nil {
		t.Fatalf("codec.Encode(parsed.Ops) failed: %v", err)
	}
	wantOps, err := codec.Encode(propMap["ops"])
	if err != nil {
		t.Fatalf("codec.Encode(want ops) failed: %v", err)
	}
	if !bytes.Equal(gotOps, wantOps) {
		t.Errorf("parsed.Ops = %s, want %s", string(gotOps), string(wantOps))
	}
	digest, err := RequestDigest(b, parsed)
	if err != nil {
		t.Fatalf("RequestDigest failed: %v", err)
	}
	if digest != wantDigest {
		t.Errorf("RequestDigest = %q, want %q", digest, wantDigest)
	}
}

// AC-9: A non-object op entry gets a rejected invalid_op receipt whose digest matches the reference;
// a changed non-object entry under the same proposal ID returns id_conflict and appends nothing.
func TestAC9_NonObjectOpEntries(t *testing.T) {
	defer guard(t)

	f := readAC9Fixture(t)

	for _, tc := range f.Cases {
		t.Run("SingleOp_"+tc.Name, func(t *testing.T) {
			defer guard(t)
			s, w := newTestStore(t)
			clock := func() time.Time {
				ts, _ := codec.ParseTime(w.ClockByBatch[0])
				return ts
			}
			g := newTestGate(t, s, clock)
			b := bindingFromWire(w)

			propMap, err := codec.DecodeObject(tc.Proposal)
			if err != nil {
				t.Fatalf("codec.DecodeObject failed: %v", err)
			}
			raw := encodeProposal(t, propMap)

			parseAndCheckDigest(t, b, raw, propMap, tc.ExpectedReceipt.Digest)

			r, err := g.Admit(b, raw)
			if err != nil {
				t.Fatalf("Admit failed: %v", err)
			}
			assertReceiptMatches(t, r, tc.ExpectedReceipt)

			snap, err := s.Snapshot(nil)
			if err != nil {
				t.Fatalf("Snapshot failed: %v", err)
			}
			if snap.Revision != tc.ExpectedHeadRevision {
				t.Errorf("head revision = %d, want %d", snap.Revision, tc.ExpectedHeadRevision)
			}
			stored, ok := snap.Projection.Receipt(tc.ExpectedReceipt.ActorID, tc.ExpectedReceipt.ProposalID)
			if !ok {
				t.Fatalf("stored receipt for (%s, %s) not found", tc.ExpectedReceipt.ActorID, tc.ExpectedReceipt.ProposalID)
			}
			assertReceiptMatches(t, stored, tc.ExpectedReceipt)
		})
	}

	t.Run("MultiOp_ValidThenNonObjectDiscardsDomainEvents", func(t *testing.T) {
		defer guard(t)
		s, w := newTestStore(t)
		clock := func() time.Time {
			ts, _ := codec.ParseTime(w.ClockByBatch[0])
			return ts
		}
		g := newTestGate(t, s, clock)
		b := bindingFromWire(w)

		if _, err := g.Admit(b, encodeProposal(t, w.Proposals[0])); err != nil {
			t.Fatalf("setup create failed: %v", err)
		}
		snapBefore, _ := s.Snapshot(nil)
		cBefore, ok := snapBefore.Projection.Record("C-notify")
		if !ok {
			t.Fatal("C-notify record missing before multi-op")
		}
		initialStmt := cBefore.Fields["statement"].(string)

		propMap, err := codec.DecodeObject(f.MultiOp.Proposal)
		if err != nil {
			t.Fatalf("codec.DecodeObject failed: %v", err)
		}
		raw := encodeProposal(t, propMap)
		parseAndCheckDigest(t, b, raw, propMap, f.MultiOp.ExpectedReceipt.Digest)

		r, err := g.Admit(b, raw)
		if err != nil {
			t.Fatalf("Admit failed: %v", err)
		}
		assertReceiptMatches(t, r, f.MultiOp.ExpectedReceipt)

		snapAfter, _ := s.Snapshot(nil)
		if snapAfter.Revision != f.MultiOp.ExpectedHeadRevision {
			t.Errorf("head revision = %d, want %d", snapAfter.Revision, f.MultiOp.ExpectedHeadRevision)
		}
		cAfter, ok := snapAfter.Projection.Record("C-notify")
		if !ok {
			t.Fatal("C-notify record missing")
		}
		if cAfter.Fields["statement"] != initialStmt {
			t.Errorf("statement changed to %q, want %q", cAfter.Fields["statement"], initialStmt)
		}
		events, err := s.Events(snapAfter.Revision, snapAfter.Revision)
		if err != nil {
			t.Fatalf("s.Events failed: %v", err)
		}
		if len(events) != 1 || events[0].Type != "ProposalReceipted" {
			t.Errorf("appended events = %+v, want single ProposalReceipted", events)
		}
	})

	t.Run("Precedence_BadRevisionBeforeNonObjectInvalidOp", func(t *testing.T) {
		defer guard(t)
		s, w := newTestStore(t)
		clock := func() time.Time {
			ts, _ := codec.ParseTime(w.ClockByBatch[0])
			return ts
		}
		g := newTestGate(t, s, clock)
		b := bindingFromWire(w)

		propMap, err := codec.DecodeObject(f.PrecedenceBadRevision.Proposal)
		if err != nil {
			t.Fatalf("codec.DecodeObject failed: %v", err)
		}
		raw := encodeProposal(t, propMap)
		parseAndCheckDigest(t, b, raw, propMap, f.PrecedenceBadRevision.ExpectedReceipt.Digest)

		r, err := g.Admit(b, raw)
		if err != nil {
			t.Fatalf("Admit failed: %v", err)
		}
		assertReceiptMatches(t, r, f.PrecedenceBadRevision.ExpectedReceipt)
	})

	t.Run("Precedence_ForbiddenScopeBeforeNonObjectInvalidOp", func(t *testing.T) {
		defer guard(t)
		s, w := newTestStore(t)
		clock := func() time.Time {
			ts, _ := codec.ParseTime(w.ClockByBatch[0])
			return ts
		}
		g := newTestGate(t, s, clock)
		b := bindingFromMap(f.PrecedenceForbidden.Binding)

		propMap, err := codec.DecodeObject(f.PrecedenceForbidden.Proposal)
		if err != nil {
			t.Fatalf("codec.DecodeObject failed: %v", err)
		}
		raw := encodeProposal(t, propMap)
		parseAndCheckDigest(t, b, raw, propMap, f.PrecedenceForbidden.ExpectedReceipt.Digest)

		r, err := g.Admit(b, raw)
		if err != nil {
			t.Fatalf("Admit failed: %v", err)
		}
		assertReceiptMatches(t, r, f.PrecedenceForbidden.ExpectedReceipt)
	})

	t.Run("RedeliveryAndChangedNonObjectEntryIDConflict", func(t *testing.T) {
		defer guard(t)
		s, w := newTestStore(t)
		clock := func() time.Time {
			ts, _ := codec.ParseTime(w.ClockByBatch[0])
			return ts
		}
		g := newTestGate(t, s, clock)
		b := bindingFromWire(w)

		firstMap, err := codec.DecodeObject(f.Conflict.First)
		if err != nil {
			t.Fatalf("codec.DecodeObject(first) failed: %v", err)
		}
		rawFirst := encodeProposal(t, firstMap)

		r1, err := g.Admit(b, rawFirst)
		if err != nil {
			t.Fatalf("first Admit failed: %v", err)
		}
		assertReceiptMatches(t, r1, f.Conflict.ExpectedReceipt)

		snap1, err := s.Snapshot(nil)
		if err != nil {
			t.Fatalf("snapshot 1 failed: %v", err)
		}

		rRedeliv, err := g.Admit(b, rawFirst)
		if err != nil {
			t.Fatalf("redelivery failed: %v", err)
		}
		if !reflect.DeepEqual(r1, rRedeliv) {
			t.Errorf("redelivered receipt = %+v, want %+v", rRedeliv, r1)
		}
		snapRedeliv, _ := s.Snapshot(nil)
		if snapRedeliv.Revision != snap1.Revision || snapRedeliv.HeadHash != snap1.HeadHash {
			t.Errorf("redelivery mutated store: got (%d, %s), want (%d, %s)",
				snapRedeliv.Revision, snapRedeliv.HeadHash, snap1.Revision, snap1.HeadHash)
		}

		changedMap, err := codec.DecodeObject(f.Conflict.Changed)
		if err != nil {
			t.Fatalf("codec.DecodeObject(changed) failed: %v", err)
		}
		rawChanged := encodeProposal(t, changedMap)
		parseAndCheckDigest(t, b, rawChanged, changedMap, f.Conflict.ChangedDigest)

		_, err = g.Admit(b, rawChanged)
		requireErrorCode(t, err, f.Conflict.ExpectedConflictError)

		snap2, err := s.Snapshot(nil)
		if err != nil {
			t.Fatalf("snapshot 2 failed: %v", err)
		}
		if snap2.Revision != snap1.Revision {
			t.Errorf("head revision changed from %d to %d on id_conflict", snap1.Revision, snap2.Revision)
		}
		if snap2.HeadHash != snap1.HeadHash {
			t.Errorf("head hash changed from %s to %s on id_conflict", snap1.HeadHash, snap2.HeadHash)
		}
		stored, ok := snap2.Projection.Receipt(f.Conflict.ExpectedReceipt.ActorID, f.Conflict.ExpectedReceipt.ProposalID)
		if !ok {
			t.Fatal("earlier receipt missing after id_conflict")
		}
		assertReceiptMatches(t, stored, f.Conflict.ExpectedReceipt)
	})
}
