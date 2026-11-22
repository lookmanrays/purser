package store

import (
	"errors"
	"path/filepath"
	"testing"
	"time"

	"github.com/lookmanrays/purser/internal/kernel/codec"
	"github.com/lookmanrays/purser/internal/kernel/projection"
	"github.com/lookmanrays/purser/internal/testvectors"
)

// AC-1: Replaying the wire vector's batches through Transact produces
// byte-identical events and the same head hash after each batch.
func TestAC1_TransactWireBatchesReplay(t *testing.T) {
	defer guard(t)

	s, w := newTestStore(t)
	if _, err := s.Initialize(); err != nil {
		t.Fatalf("Initialize: %v", err)
	}

	t.Run("Batch1", func(t *testing.T) {
		defer guard(t)

		snap, events := playBatch(t, s, 0, w)
		if snap.Revision != 2 {
			t.Errorf("batch 1 revision = %d, want 2", snap.Revision)
		}
		wantHead, ok := w.Expected.StatusByRevision[0]["head_hash"].(string)
		if !ok {
			t.Fatal("missing status_by_revision[0].head_hash")
		}
		if snap.HeadHash != wantHead {
			t.Errorf("batch 1 head_hash = %q, want %q", snap.HeadHash, wantHead)
		}
		if len(events) != 2 {
			t.Fatalf("batch 1 returned %d events, want 2", len(events))
		}
		for i, ev := range events {
			wantHash, ok := w.Events[i]["hash"].(string)
			if !ok {
				t.Fatalf("missing wire event[%d].hash", i)
			}
			if ev.Hash != wantHash {
				t.Errorf("event[%d].Hash = %q, want %q", i, ev.Hash, wantHash)
			}
			wantBytes, err := codec.Encode(w.Events[i])
			if err != nil {
				t.Fatalf("Encode wire event[%d]: %v", i, err)
			}
			gotBytes, err := codec.Encode(ev.Map())
			if err != nil {
				t.Fatalf("Encode committed event[%d]: %v", i, err)
			}
			if string(gotBytes) != string(wantBytes) {
				t.Errorf("event[%d] byte mismatch:\ngot:  %s\nwant: %s", i, string(gotBytes), string(wantBytes))
			}
		}
	})

	t.Run("Batch2", func(t *testing.T) {
		defer guard(t)

		snap, events := playBatch(t, s, 1, w)
		if snap.Revision != 4 {
			t.Errorf("batch 2 revision = %d, want 4", snap.Revision)
		}
		wantHead, ok := w.Expected.StatusByRevision[1]["head_hash"].(string)
		if !ok {
			t.Fatal("missing status_by_revision[1].head_hash")
		}
		if snap.HeadHash != wantHead {
			t.Errorf("batch 2 head_hash = %q, want %q", snap.HeadHash, wantHead)
		}
		if len(events) != 2 {
			t.Fatalf("batch 2 returned %d events, want 2", len(events))
		}
		for i, ev := range events {
			wireIdx := i + 2
			wantHash, ok := w.Events[wireIdx]["hash"].(string)
			if !ok {
				t.Fatalf("missing wire event[%d].hash", wireIdx)
			}
			if ev.Hash != wantHash {
				t.Errorf("event[%d].Hash = %q, want %q", wireIdx, ev.Hash, wantHash)
			}
			wantBytes, err := codec.Encode(w.Events[wireIdx])
			if err != nil {
				t.Fatalf("Encode wire event[%d]: %v", wireIdx, err)
			}
			gotBytes, err := codec.Encode(ev.Map())
			if err != nil {
				t.Fatalf("Encode committed event[%d]: %v", wireIdx, err)
			}
			if string(gotBytes) != string(wantBytes) {
				t.Errorf("event[%d] byte mismatch:\ngot:  %s\nwant: %s", wireIdx, string(gotBytes), string(wantBytes))
			}
		}
	})

	t.Run("Batch3", func(t *testing.T) {
		defer guard(t)

		snap, events := playBatch(t, s, 2, w)
		if snap.Revision != 8 {
			t.Errorf("batch 3 revision = %d, want 8", snap.Revision)
		}
		wantHead, ok := w.Expected.StatusByRevision[2]["head_hash"].(string)
		if !ok {
			t.Fatal("missing status_by_revision[2].head_hash")
		}
		if snap.HeadHash != wantHead {
			t.Errorf("batch 3 head_hash = %q, want %q", snap.HeadHash, wantHead)
		}
		if len(events) != 4 {
			t.Fatalf("batch 3 returned %d events, want 4", len(events))
		}
		for i, ev := range events {
			wireIdx := i + 4
			wantHash, ok := w.Events[wireIdx]["hash"].(string)
			if !ok {
				t.Fatalf("missing wire event[%d].hash", wireIdx)
			}
			if ev.Hash != wantHash {
				t.Errorf("event[%d].Hash = %q, want %q", wireIdx, ev.Hash, wantHash)
			}
			wantBytes, err := codec.Encode(w.Events[wireIdx])
			if err != nil {
				t.Fatalf("Encode wire event[%d]: %v", wireIdx, err)
			}
			gotBytes, err := codec.Encode(ev.Map())
			if err != nil {
				t.Fatalf("Encode committed event[%d]: %v", wireIdx, err)
			}
			if string(gotBytes) != string(wantBytes) {
				t.Errorf("event[%d] byte mismatch:\ngot:  %s\nwant: %s", wireIdx, string(gotBytes), string(wantBytes))
			}
		}
	})

	t.Run("AllRawEventsByteIdentical", func(t *testing.T) {
		defer guard(t)

		raws, err := s.RawEvents(1, 8)
		if err != nil {
			t.Fatalf("RawEvents(1, 8) failed: %v", err)
		}
		if len(raws) != 8 {
			t.Fatalf("len(RawEvents) = %d, want 8", len(raws))
		}
		for i, raw := range raws {
			wantBytes, err := codec.Encode(w.Events[i])
			if err != nil {
				t.Fatalf("Encode event %d: %v", i, err)
			}
			if string(raw) != string(wantBytes) {
				t.Errorf("event %d canonical bytes mismatch:\ngot:  %s\nwant: %s", i+1, string(raw), string(wantBytes))
			}
		}
	})
}

// AC-3: A failure injected after the event insert and before commit leaves the
// previous head, events and projection unchanged.
func TestAC3_FaultAfterInsertLeavesStateUnchanged(t *testing.T) {
	defer guard(t)

	// Create a registry that fails on a specific injected event
	customReg := projection.NewRegistry()
	baseReg := projection.Base()

	// Replicate base reducers
	for _, entry := range []struct {
		eventType string
		version   int64
	}{
		{"CommitmentCreated", 1},
		{"CommitmentCreated", 0},
		{"CommitmentEdited", 1},
		{"ApprovalRequested", 1},
		{"ApprovalResolved", 1},
		{"CommitmentActivated", 1},
		{"ProposalReceipted", 1},
	} {
		et, v := entry.eventType, entry.version
		_ = customReg.Register(et, v, func(p projection.Projection, data map[string]any, rev, batchEnd int64) (projection.Projection, projection.Changes, error) {
			ev := codec.Event{
				Type:         et,
				EventVersion: v,
				Data:         data,
				Revision:     rev,
				BatchStart:   rev,
				BatchSize:    batchEnd - rev + 1,
			}
			return baseReg.Apply(p, ev)
		})
	}

	// Register a fault-injecting event type
	faultErr := errors.New("injected fault between insert and commit")
	_ = customReg.Register("FaultEvent", 1, func(_ projection.Projection, _ map[string]any, _, _ int64) (projection.Projection, projection.Changes, error) {
		return projection.Projection{}, projection.Changes{}, faultErr
	})

	dbPath := filepath.Join(t.TempDir(), "fault.db")
	s, err := Open(dbPath, Options{Registry: customReg, BusyTimeout: 5 * time.Second})
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	t.Cleanup(func() { _ = s.Close() })

	if _, err := s.Initialize(); err != nil {
		t.Fatalf("Initialize: %v", err)
	}

	w := testvectors.Wire()

	// Play batch 1 successfully (commits revisions 1 and 2)
	snap1, _ := playBatch(t, s, 0, w)
	headBefore := snap1.HeadHash
	revBefore := snap1.Revision

	// Now transact a batch where first event is valid but second event triggers fault
	clock := func() time.Time { return time.Date(2026, 11, 18, 12, 1, 0, 0, time.UTC) }
	_, _, err = s.Transact("principal:local", "p-fault", clock, func(_ Snapshot, _ time.Time) ([]codec.EventSpec, error) {
		return []codec.EventSpec{
			{
				Type:         "CommitmentCreated",
				EventVersion: 1,
				Data: map[string]any{
					"id":        "C-valid",
					"root":      "W-valid",
					"principal": "principal:local",
					"statement": "Valid statement",
				},
			},
			{
				Type:         "FaultEvent",
				EventVersion: 1,
				Data:         map[string]any{"trigger": true},
			},
		}, nil
	})

	if err == nil {
		t.Fatal("Transact with fault succeeded unexpectedly")
	}

	// Verify previous head, events and projection are completely unchanged
	snapAfter, err := s.Snapshot(nil)
	if err != nil {
		t.Fatalf("Snapshot(nil) failed: %v", err)
	}
	if snapAfter.Revision != revBefore {
		t.Errorf("revision after fault = %d, want %d", snapAfter.Revision, revBefore)
	}
	if snapAfter.HeadHash != headBefore {
		t.Errorf("head_hash after fault = %q, want %q", snapAfter.HeadHash, headBefore)
	}

	events, err := s.Events(1, 10)
	if err != nil {
		t.Fatalf("Events(1, 10) failed: %v", err)
	}
	if len(events) != 2 {
		t.Errorf("Events count after fault rollback = %d, want 2 (partial rows leaked)", len(events))
	}

	if _, ok := snapAfter.Projection.Record("C-valid"); ok {
		t.Error("partial record C-valid was found in projection after rollback")
	}
}

// AC-4: An error returned by decide rolls back with no partial rows.
func TestAC4_DecideErrorRollsBackNoPartialRows(t *testing.T) {
	defer guard(t)

	s, _ := newTestStore(t)
	if _, err := s.Initialize(); err != nil {
		t.Fatalf("Initialize: %v", err)
	}

	decideErr := errors.New("policy rejected proposal")
	clock := func() time.Time { return time.Date(2026, 11, 18, 12, 0, 0, 0, time.UTC) }

	_, _, err := s.Transact("principal:local", "p-rejected", clock, func(_ Snapshot, _ time.Time) ([]codec.EventSpec, error) {
		return nil, decideErr
	})

	if err == nil {
		t.Fatal("Transact succeeded, want decide error")
	}
	if !errors.Is(err, decideErr) && err.Error() != decideErr.Error() {
		t.Errorf("Transact error = %v, want %v", err, decideErr)
	}

	// Verify no partial rows written
	snap, err := s.Snapshot(nil)
	if err != nil {
		t.Fatalf("Snapshot(nil) failed: %v", err)
	}
	if snap.Revision != 0 {
		t.Errorf("store revision after rollback = %d, want 0", snap.Revision)
	}
	if snap.HeadHash != codec.ZeroHash {
		t.Errorf("store head_hash after rollback = %q, want %q", snap.HeadHash, codec.ZeroHash)
	}

	events, err := s.Events(1, 10)
	if err != nil {
		t.Fatalf("Events: %v", err)
	}
	if len(events) != 0 {
		t.Errorf("events count = %d, want 0", len(events))
	}
	if len(snap.Projection.Records) != 0 || len(snap.Projection.Receipts) != 0 || len(snap.Projection.Edges) != 0 {
		t.Errorf("projection has partial rows after decide error rollback")
	}
}

// AC-5: Empty event lists change nothing, not even the head.
func TestAC5_EmptyEventListChangesNothing(t *testing.T) {
	defer guard(t)

	s, w := newTestStore(t)
	if _, err := s.Initialize(); err != nil {
		t.Fatalf("Initialize: %v", err)
	}

	// Play batch 1
	snapBefore, _ := playBatch(t, s, 0, w)

	clock := func() time.Time { return time.Date(2026, 11, 18, 12, 10, 0, 0, time.UTC) }

	// Transact with empty event list
	snapReturned, committedEvents, err := s.Transact("principal:local", "p-empty", clock, func(_ Snapshot, _ time.Time) ([]codec.EventSpec, error) {
		return []codec.EventSpec{}, nil
	})
	if err != nil {
		t.Fatalf("Transact with empty events failed: %v", err)
	}
	if len(committedEvents) != 0 {
		t.Errorf("committed events count = %d, want 0", len(committedEvents))
	}
	if snapReturned.Revision != snapBefore.Revision {
		t.Errorf("snapReturned.Revision = %d, want %d", snapReturned.Revision, snapBefore.Revision)
	}
	if snapReturned.HeadHash != snapBefore.HeadHash {
		t.Errorf("snapReturned.HeadHash = %q, want %q", snapReturned.HeadHash, snapBefore.HeadHash)
	}

	// Subsequent snapshot from store must also be unchanged
	snapAfter, err := s.Snapshot(nil)
	if err != nil {
		t.Fatalf("Snapshot(nil): %v", err)
	}
	if snapAfter.Revision != snapBefore.Revision {
		t.Errorf("snapAfter.Revision = %d, want %d", snapAfter.Revision, snapBefore.Revision)
	}
	if snapAfter.HeadHash != snapBefore.HeadHash {
		t.Errorf("snapAfter.HeadHash = %q, want %q", snapAfter.HeadHash, snapBefore.HeadHash)
	}
}

// AC-6: An unregistered event type or version fails with unsupported_version,
// and the projection stays as it was.
func TestAC6_TransactUnregisteredEventFails(t *testing.T) {
	defer guard(t)

	s, w := newTestStore(t)
	if _, err := s.Initialize(); err != nil {
		t.Fatalf("Initialize: %v", err)
	}

	// Play batch 1
	snapBefore, _ := playBatch(t, s, 0, w)

	clock := func() time.Time { return time.Date(2026, 11, 18, 12, 5, 0, 0, time.UTC) }

	_, _, err := s.Transact("principal:local", "p-unsupported", clock, func(_ Snapshot, _ time.Time) ([]codec.EventSpec, error) {
		return []codec.EventSpec{
			{
				Type:         "UnknownEventType",
				EventVersion: 1,
				Data:         map[string]any{"id": "unknown"},
			},
		}, nil
	})

	if err == nil {
		t.Fatal("Transact with unknown event type succeeded, want unsupported_version")
	}
	if !hasCode(err, "unsupported_version") {
		t.Errorf("Transact error = %v, want unsupported_version", err)
	}

	// Verify state and projection unchanged
	snapAfter, err := s.Snapshot(nil)
	if err != nil {
		t.Fatalf("Snapshot(nil): %v", err)
	}
	if snapAfter.Revision != snapBefore.Revision || snapAfter.HeadHash != snapBefore.HeadHash {
		t.Errorf("state changed after unsupported_version failure: %+v", snapAfter)
	}
}

// TestTransact_Validation tests preconditions and clock sampling.
func TestTransact_Validation(t *testing.T) {
	defer guard(t)

	s, _ := newTestStore(t)
	if _, err := s.Initialize(); err != nil {
		t.Fatalf("Initialize: %v", err)
	}

	clockInstant := time.Date(2026, 11, 18, 12, 0, 0, 0, time.UTC)
	clockCount := 0
	clock := func() time.Time {
		clockCount++
		return clockInstant
	}

	t.Run("EmptyActorIDRejected", func(t *testing.T) {
		defer guard(t)

		_, _, err := s.Transact("", "p-valid", clock, func(_ Snapshot, _ time.Time) ([]codec.EventSpec, error) {
			return []codec.EventSpec{}, nil
		})
		if err == nil {
			t.Error("Transact with empty actorID succeeded, want error")
		}
	})

	t.Run("InvalidProposalIDRejected", func(t *testing.T) {
		defer guard(t)

		_, _, err := s.Transact("principal:local", "invalid/proposal!id", clock, func(_ Snapshot, _ time.Time) ([]codec.EventSpec, error) {
			return []codec.EventSpec{}, nil
		})
		if err == nil {
			t.Error("Transact with invalid proposalID succeeded, want error")
		}
	})

	t.Run("ClockCalledExactlyOnce", func(t *testing.T) {
		defer guard(t)

		clockCount = 0
		var sampledAt time.Time
		_, _, err := s.Transact("principal:local", "p-clock", clock, func(_ Snapshot, at time.Time) ([]codec.EventSpec, error) {
			sampledAt = at
			return []codec.EventSpec{}, nil
		})
		if err != nil {
			t.Fatalf("Transact: %v", err)
		}
		if clockCount != 1 {
			t.Errorf("clock called %d times, want exactly 1", clockCount)
		}
		if !sampledAt.Equal(clockInstant) {
			t.Errorf("sampled instant = %v, want %v", sampledAt, clockInstant)
		}
	})
}
