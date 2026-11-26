package gate

import (
	"path/filepath"
	"reflect"
	"testing"
	"time"

	"github.com/lookmanrays/purser/internal/kernel/codec"
	"github.com/lookmanrays/purser/internal/kernel/projection"
	"github.com/lookmanrays/purser/internal/kernel/store"
	"github.com/lookmanrays/purser/internal/testvectors"
)

type fixtureMarkOp struct{}

func (o *fixtureMarkOp) Name() string { return "fixture.mark" }

func (o *fixtureMarkOp) Apply(t Tentative, op map[string]any, reads *ReadSet) (Outcome, error) {
	id, _ := op["id"].(string)
	mark, _ := op["mark"].(string)
	reads.Absent(id)
	return Outcome{
		Events: []codec.EventSpec{
			{
				Type:         "FixtureMarked",
				EventVersion: 1,
				Data: map[string]any{
					"id":         id,
					"commitment": t.Commitment,
					"mark":       mark,
				},
			},
		},
	}, nil
}

type fixtureConsumeOp struct {
	called        bool
	sawProjection projection.Projection
	sawRecord     projection.Record
	emitted       []codec.EventSpec
}

func (o *fixtureConsumeOp) Name() string { return "fixture.consume" }

func (o *fixtureConsumeOp) Apply(t Tentative, op map[string]any, _ *ReadSet) (Outcome, error) {
	o.called = true
	target, _ := op["target"].(string)
	id, _ := op["id"].(string)
	expected, _ := op["expected"].(string)

	rec, ok := t.Projection.Record(target)
	if !ok || rec.Kind != "FixtureMark" || rec.Fields["mark"] != expected {
		return Outcome{
			Rejection: &Rejection{
				Code:    "not_found",
				Details: map[string]any{"missing": target},
			},
		}, nil
	}

	raw, err := codec.Encode(t.Projection.Map())
	if err != nil {
		return Outcome{}, err
	}
	obj, err := codec.DecodeObject(raw)
	if err != nil {
		return Outcome{}, err
	}
	copied, err := projection.FromMap(obj)
	if err != nil {
		return Outcome{}, err
	}
	o.sawProjection = copied
	o.sawRecord = rec

	spec := codec.EventSpec{
		Type:         "FixtureMarked",
		EventVersion: 1,
		Data: map[string]any{
			"id":         id,
			"commitment": t.Commitment,
			"mark":       "confirmed:" + expected,
		},
	}
	o.emitted = []codec.EventSpec{spec}
	return Outcome{Events: []codec.EventSpec{spec}}, nil
}

func newFixtureRegistry(t *testing.T) *projection.Registry {
	t.Helper()
	reg := projection.Base()
	err := reg.Register("FixtureMarked", 1, func(p projection.Projection, data map[string]any, revision, _ int64) (projection.Projection, projection.Changes, error) {
		id, _ := data["id"].(string)
		commitment, _ := data["commitment"].(string)
		mark, _ := data["mark"].(string)
		if id == "" || commitment == "" || mark == "" {
			return p, projection.Changes{}, &codec.Error{Code: "integrity_error", Revision: revision, Detail: "invalid fixture event"}
		}
		m := p.Map()
		recs := m["records"].([]any)
		recs = append(recs, map[string]any{
			"id":         id,
			"kind":       "FixtureMark",
			"commitment": commitment,
			"changed":    revision,
			"fields":     map[string]any{"mark": mark},
		})
		m["records"] = recs
		next, err := projection.FromMap(m)
		if err != nil {
			return p, projection.Changes{}, err
		}
		return next, projection.Changes{Records: []string{id}}, nil
	})
	if err != nil {
		t.Fatalf("reg.Register(FixtureMarked) failed: %v", err)
	}
	return reg
}

func openFixtureStore(t *testing.T, reg *projection.Registry) (*store.Store, *testvectors.WireVector) {
	t.Helper()
	w := testvectors.Wire()
	if w == nil {
		t.Fatal("wire vector is nil")
	}
	dbPath := filepath.Join(t.TempDir(), "store.db")
	s, err := store.Open(dbPath, store.Options{
		Registry:    reg,
		BusyTimeout: 5 * time.Second,
	})
	if err != nil {
		t.Fatalf("store.Open failed: %v", err)
	}
	t.Cleanup(func() { _ = s.Close() })
	if _, err := s.Initialize(); err != nil {
		t.Fatalf("store.Initialize failed: %v", err)
	}
	return s, w
}

// AC-10: With a store opened on a registry that adds a fixture event type to projection.Base(),
// a second op sees the tentative effect of the first op's fixture event, and the committed
// projection equals what the second op saw plus its own events.
func TestAC10_TentativeUsesStoreRegistry(t *testing.T) {
	defer guard(t)

	t.Run("SecondOpSeesFixtureEffectAndMatchesCommitted", func(t *testing.T) {
		defer guard(t)

		reg := newFixtureRegistry(t)
		if projection.Base().Known("FixtureMarked", 1) {
			t.Fatal("projection.Base() must return a fresh registry without FixtureMarked")
		}

		s, w := openFixtureStore(t, reg)
		clock := func() time.Time {
			ts, _ := codec.ParseTime(w.ClockByBatch[0])
			return ts
		}
		g := newTestGate(t, s, clock)
		op1 := &fixtureMarkOp{}
		op2 := &fixtureConsumeOp{}
		if err := g.Register(op1); err != nil {
			t.Fatalf("Register(fixture.mark) failed: %v", err)
		}
		if err := g.Register(op2); err != nil {
			t.Fatalf("Register(fixture.consume) failed: %v", err)
		}

		b := bindingFromWire(w)
		prop := map[string]any{
			"v":             int64(1),
			"proposal_id":   "p-ac10-apply",
			"commitment":    "C-fixture",
			"base_revision": int64(0),
			"ops": []any{
				map[string]any{
					"op":   "fixture.mark",
					"id":   "M-tentative",
					"mark": "from-op-1",
				},
				map[string]any{
					"op":       "fixture.consume",
					"target":   "M-tentative",
					"id":       "M-confirmed",
					"expected": "from-op-1",
				},
			},
			"justification": []any{},
		}

		r, err := g.Admit(b, encodeProposal(t, prop))
		if err != nil {
			t.Fatalf("Admit failed: %v", err)
		}
		if r.Disposition != "admitted" || r.Code != "ok" {
			t.Fatalf("receipt = (%s, %s), want (admitted, ok)", r.Disposition, r.Code)
		}
		if r.Revision != 3 {
			t.Errorf("receipt.Revision = %d, want 3", r.Revision)
		}
		if !op2.called {
			t.Fatal("second op was not called")
		}
		if op2.sawRecord.ID != "M-tentative" || op2.sawRecord.Kind != "FixtureMark" || op2.sawRecord.Fields["mark"] != "from-op-1" {
			t.Errorf("second op saw record = %+v, want M-tentative with mark from-op-1", op2.sawRecord)
		}

		snap, err := s.Snapshot(nil)
		if err != nil {
			t.Fatalf("Snapshot failed: %v", err)
		}
		if snap.Revision != 3 {
			t.Fatalf("head revision = %d, want 3", snap.Revision)
		}
		events, err := s.Events(1, 3)
		if err != nil {
			t.Fatalf("Events(1, 3) failed: %v", err)
		}
		if len(events) != 3 {
			t.Fatalf("len(events) = %d, want 3", len(events))
		}
		if events[0].Type != "FixtureMarked" || events[1].Type != "FixtureMarked" || events[2].Type != "ProposalReceipted" {
			t.Fatalf("event types = (%s, %s, %s), want (FixtureMarked, FixtureMarked, ProposalReceipted)",
				events[0].Type, events[1].Type, events[2].Type)
		}
		if !reflect.DeepEqual(events[1].Data, op2.emitted[0].Data) {
			t.Errorf("committed second op event data = %v, want %v", events[1].Data, op2.emitted[0].Data)
		}

		withOp2, _, err := reg.Apply(op2.sawProjection, events[1])
		if err != nil {
			t.Fatalf("reg.Apply(op2 event) failed: %v", err)
		}
		if !reflect.DeepEqual(withOp2.Records, snap.Projection.Records) {
			t.Errorf("records after op2 = %+v, want %+v", withOp2.Records, snap.Projection.Records)
		}
		if !reflect.DeepEqual(withOp2.Edges, snap.Projection.Edges) {
			t.Errorf("edges after op2 = %+v, want %+v", withOp2.Edges, snap.Projection.Edges)
		}

		expectedFull, _, err := reg.Apply(withOp2, events[2])
		if err != nil {
			t.Fatalf("reg.Apply(ProposalReceipted) failed: %v", err)
		}
		gotSum, err := snap.Projection.Checksum()
		if err != nil {
			t.Fatalf("snap.Projection.Checksum failed: %v", err)
		}
		wantSum, err := expectedFull.Checksum()
		if err != nil {
			t.Fatalf("expectedFull.Checksum failed: %v", err)
		}
		if gotSum != wantSum {
			t.Errorf("committed checksum = %s, want %s", gotSum, wantSum)
		}
		if !reflect.DeepEqual(snap.Projection.Map(), expectedFull.Map()) {
			t.Errorf("committed projection = %v, want %v", snap.Projection.Map(), expectedFull.Map())
		}
	})

	t.Run("SecondOpRejectsWhenFixtureEffectMissing", func(t *testing.T) {
		defer guard(t)

		reg := newFixtureRegistry(t)
		s, w := openFixtureStore(t, reg)
		clock := func() time.Time {
			ts, _ := codec.ParseTime(w.ClockByBatch[0])
			return ts
		}
		g := newTestGate(t, s, clock)
		if err := g.Register(&fixtureMarkOp{}); err != nil {
			t.Fatalf("Register(fixture.mark) failed: %v", err)
		}
		op2 := &fixtureConsumeOp{}
		if err := g.Register(op2); err != nil {
			t.Fatalf("Register(fixture.consume) failed: %v", err)
		}

		b := bindingFromWire(w)
		prop := map[string]any{
			"v":             int64(1),
			"proposal_id":   "p-ac10-missing",
			"commitment":    "C-fixture",
			"base_revision": int64(0),
			"ops": []any{
				map[string]any{
					"op":       "fixture.consume",
					"target":   "M-tentative",
					"id":       "M-confirmed",
					"expected": "from-op-1",
				},
			},
			"justification": []any{},
		}

		r, err := g.Admit(b, encodeProposal(t, prop))
		if err != nil {
			t.Fatalf("Admit failed: %v", err)
		}
		if r.Disposition != "rejected" || r.Code != "not_found" {
			t.Fatalf("receipt = (%s, %s), want (rejected, not_found)", r.Disposition, r.Code)
		}
		if !op2.called {
			t.Fatal("second op was not called")
		}

		snap, err := s.Snapshot(nil)
		if err != nil {
			t.Fatalf("Snapshot failed: %v", err)
		}
		if snap.Revision != 1 {
			t.Errorf("head revision = %d, want 1", snap.Revision)
		}
		if len(snap.Projection.Records) != 0 {
			t.Errorf("records = %+v, want none", snap.Projection.Records)
		}
	})

	t.Run("StoreRegistryReturnsConfiguredOrBase", func(t *testing.T) {
		defer guard(t)

		reg := newFixtureRegistry(t)
		s, _ := openFixtureStore(t, reg)
		if s.Registry() != reg {
			t.Errorf("s.Registry() = %p, want %p", s.Registry(), reg)
		}
		if !s.Registry().Known("FixtureMarked", 1) {
			t.Error("s.Registry() does not know FixtureMarked v1")
		}

		sNil, _ := openFixtureStore(t, nil)
		r1 := sNil.Registry()
		r2 := sNil.Registry()
		if r1 == nil {
			t.Fatal("sNil.Registry() returned nil")
		}
		if r1 != r2 {
			t.Errorf("sNil.Registry() returned different pointers: %p vs %p", r1, r2)
		}
		if !r1.Known("CommitmentCreated", 1) {
			t.Error("sNil.Registry() does not know CommitmentCreated v1")
		}
		if r1.Known("FixtureMarked", 1) {
			t.Error("sNil.Registry() unexpectedly knows FixtureMarked v1")
		}
	})
}
