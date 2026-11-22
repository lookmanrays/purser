package store

import (
	"errors"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/lookmanrays/purser/internal/kernel/codec"
	"github.com/lookmanrays/purser/internal/kernel/projection"
	"github.com/lookmanrays/purser/internal/testvectors"
)

func guard(t *testing.T) {
	if r := recover(); r != nil {
		t.Fatalf("panic: %v", r)
	}
}

func hasCode(err error, want string) bool {
	if err == nil {
		return false
	}
	var ce *codec.Error
	if errors.As(err, &ce) && ce.Code == want {
		return true
	}
	type coder interface{ Code() string }
	var c coder
	if errors.As(err, &c) && c.Code() == want {
		return true
	}
	return strings.Contains(err.Error(), want)
}

func newTestStore(t *testing.T) (*Store, *testvectors.WireVector) {
	t.Helper()
	w := testvectors.Wire()
	if w == nil {
		t.Fatal("Wire vector is nil")
	}
	dbPath := filepath.Join(t.TempDir(), "store.db")
	s, err := Open(dbPath, Options{
		Registry:    projection.Base(),
		BusyTimeout: 5 * time.Second,
	})
	if err != nil {
		t.Fatalf("Open failed: %v", err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s, w
}

func playBatch(t *testing.T, s *Store, batchIdx int, w *testvectors.WireVector) (Snapshot, []codec.Event) {
	t.Helper()
	tInstant, err := codec.ParseTime(w.ClockByBatch[batchIdx])
	if err != nil {
		t.Fatalf("parse clock %d: %v", batchIdx, err)
	}
	clock := func() time.Time { return tInstant }

	var (
		actorID    string
		proposalID string
		specs      []codec.EventSpec
	)
	switch batchIdx {
	case 0:
		actorID = w.Events[0]["actor_id"].(string)
		proposalID = w.Events[0]["proposal_id"].(string)
		specs = []codec.EventSpec{
			{Type: w.Events[0]["type"].(string), EventVersion: w.Events[0]["event_version"].(int64), Data: w.Events[0]["data"].(map[string]any)},
			{Type: w.Events[1]["type"].(string), EventVersion: w.Events[1]["event_version"].(int64), Data: w.Events[1]["data"].(map[string]any)},
		}
	case 1:
		actorID = w.Events[2]["actor_id"].(string)
		proposalID = w.Events[2]["proposal_id"].(string)
		specs = []codec.EventSpec{
			{Type: w.Events[2]["type"].(string), EventVersion: w.Events[2]["event_version"].(int64), Data: w.Events[2]["data"].(map[string]any)},
			{Type: w.Events[3]["type"].(string), EventVersion: w.Events[3]["event_version"].(int64), Data: w.Events[3]["data"].(map[string]any)},
		}
	case 2:
		actorID = w.Events[4]["actor_id"].(string)
		proposalID = w.Events[4]["proposal_id"].(string)
		specs = []codec.EventSpec{
			{Type: w.Events[4]["type"].(string), EventVersion: w.Events[4]["event_version"].(int64), Data: w.Events[4]["data"].(map[string]any)},
			{Type: w.Events[5]["type"].(string), EventVersion: w.Events[5]["event_version"].(int64), Data: w.Events[5]["data"].(map[string]any)},
			{Type: w.Events[6]["type"].(string), EventVersion: w.Events[6]["event_version"].(int64), Data: w.Events[6]["data"].(map[string]any)},
			{Type: w.Events[7]["type"].(string), EventVersion: w.Events[7]["event_version"].(int64), Data: w.Events[7]["data"].(map[string]any)},
		}
	default:
		t.Fatalf("invalid batchIdx: %d", batchIdx)
	}

	snap, events, err := s.Transact(actorID, proposalID, clock, func(_ Snapshot, _ time.Time) ([]codec.EventSpec, error) {
		return specs, nil
	})
	if err != nil {
		t.Fatalf("Transact batch %d failed: %v", batchIdx, err)
	}
	return snap, events
}

// AC-7: A fresh store has revision 0, an empty projection and a head of 64 zeroes.
func TestAC7_FreshStoreZeroHeadEmptyProjection(t *testing.T) {
	defer guard(t)

	s, _ := newTestStore(t)

	snap, err := s.Initialize()
	if err != nil {
		t.Fatalf("Initialize failed: %v", err)
	}

	if snap.Revision != 0 {
		t.Errorf("fresh store revision = %d, want 0", snap.Revision)
	}
	if snap.HeadHash != codec.ZeroHash {
		t.Errorf("fresh store head_hash = %q, want %q", snap.HeadHash, codec.ZeroHash)
	}
	if len(snap.Projection.Records) != 0 {
		t.Errorf("fresh store records count = %d, want 0", len(snap.Projection.Records))
	}
	if len(snap.Projection.Receipts) != 0 {
		t.Errorf("fresh store receipts count = %d, want 0", len(snap.Projection.Receipts))
	}
	if len(snap.Projection.Edges) != 0 {
		t.Errorf("fresh store edges count = %d, want 0", len(snap.Projection.Edges))
	}

	// Also verify reading head snapshot on fresh store
	headSnap, err := s.Snapshot(nil)
	if err != nil {
		t.Fatalf("Snapshot(nil) on fresh store failed: %v", err)
	}
	if headSnap.Revision != 0 || headSnap.HeadHash != codec.ZeroHash {
		t.Errorf("Snapshot(nil) on fresh store = %+v", headSnap)
	}
}

// AC-2: The snapshots at revisions 4 and 8 equal snapshot_at_4 and snapshot_at_8
// exactly, and Hash("projection", …) at each revision listed in status_by_revision
// equals its projection_hash.
func TestAC2_SnapshotRevisionsAndProjectionHashes(t *testing.T) {
	defer guard(t)

	s, w := newTestStore(t)
	if _, err := s.Initialize(); err != nil {
		t.Fatalf("Initialize: %v", err)
	}

	// Play all three wire batches
	playBatch(t, s, 0, w)
	playBatch(t, s, 1, w)
	playBatch(t, s, 2, w)

	t.Run("SnapshotAt4AndChecksum", func(t *testing.T) {
		defer guard(t)

		rev4 := int64(4)
		s4, err := s.Snapshot(&rev4)
		if err != nil {
			t.Fatalf("Snapshot(4) failed: %v", err)
		}
		if s4.Revision != 4 {
			t.Errorf("Snapshot(4).Revision = %d, want 4", s4.Revision)
		}

		wantHead4, ok := w.Expected.StatusByRevision[1]["head_hash"].(string)
		if !ok {
			t.Fatal("missing status_by_revision[1].head_hash")
		}
		if s4.HeadHash != wantHead4 {
			t.Errorf("Snapshot(4).HeadHash = %q, want %q", s4.HeadHash, wantHead4)
		}

		c4, err := s4.Projection.Checksum()
		if err != nil {
			t.Fatalf("Checksum of snapshot(4) failed: %v", err)
		}
		wantChecksum4, ok := w.Expected.StatusByRevision[1]["projection_hash"].(string)
		if !ok {
			t.Fatal("missing status_by_revision[1].projection_hash")
		}
		if c4 != wantChecksum4 {
			t.Errorf("snapshot(4) projection checksum = %q, want %q", c4, wantChecksum4)
		}
	})

	t.Run("SnapshotAt8AndChecksum", func(t *testing.T) {
		defer guard(t)

		rev8 := int64(8)
		s8, err := s.Snapshot(&rev8)
		if err != nil {
			t.Fatalf("Snapshot(8) failed: %v", err)
		}
		if s8.Revision != 8 {
			t.Errorf("Snapshot(8).Revision = %d, want 8", s8.Revision)
		}

		wantHead8, ok := w.Expected.StatusByRevision[2]["head_hash"].(string)
		if !ok {
			t.Fatal("missing status_by_revision[2].head_hash")
		}
		if s8.HeadHash != wantHead8 {
			t.Errorf("Snapshot(8).HeadHash = %q, want %q", s8.HeadHash, wantHead8)
		}

		c8, err := s8.Projection.Checksum()
		if err != nil {
			t.Fatalf("Checksum of snapshot(8) failed: %v", err)
		}
		wantChecksum8, ok := w.Expected.StatusByRevision[2]["projection_hash"].(string)
		if !ok {
			t.Fatal("missing status_by_revision[2].projection_hash")
		}
		if c8 != wantChecksum8 {
			t.Errorf("snapshot(8) projection checksum = %q, want %q", c8, wantChecksum8)
		}

		// Snapshot(nil) must equal snapshot at head (rev 8)
		headSnap, err := s.Snapshot(nil)
		if err != nil {
			t.Fatalf("Snapshot(nil) failed: %v", err)
		}
		if headSnap.Revision != 8 || headSnap.HeadHash != wantHead8 {
			t.Errorf("Snapshot(nil) = %+v, want revision 8 and head %q", headSnap, wantHead8)
		}
	})

	t.Run("SnapshotAt2Checksum", func(t *testing.T) {
		defer guard(t)

		rev2 := int64(2)
		s2, err := s.Snapshot(&rev2)
		if err != nil {
			t.Fatalf("Snapshot(2) failed: %v", err)
		}
		c2, err := s2.Projection.Checksum()
		if err != nil {
			t.Fatalf("Checksum of snapshot(2) failed: %v", err)
		}
		wantChecksum2, ok := w.Expected.StatusByRevision[0]["projection_hash"].(string)
		if !ok {
			t.Fatal("missing status_by_revision[0].projection_hash")
		}
		if c2 != wantChecksum2 {
			t.Errorf("snapshot(2) projection checksum = %q, want %q", c2, wantChecksum2)
		}
	})

	t.Run("BadRevisionErrors", func(t *testing.T) {
		defer guard(t)

		// Revisions inside batches (1, 3, 5, 6, 7), negative, and beyond head (9, 100)
		badRevisions := []int64{-1, 1, 3, 5, 6, 7, 9, 100}
		for _, bad := range badRevisions {
			rev := bad
			_, err := s.Snapshot(&rev)
			if err == nil {
				t.Errorf("Snapshot(%d) succeeded, want bad_revision error", bad)
			} else if !hasCode(err, "bad_revision") {
				t.Errorf("Snapshot(%d) error = %v, want bad_revision", bad, err)
			}
		}
	})
}

// TestStore_InitializeExistingStore tests that Initialize on an existing
// store verifies the event chain and returns the existing head snapshot.
func TestStore_InitializeExistingStore(t *testing.T) {
	defer guard(t)

	dbPath := filepath.Join(t.TempDir(), "existing.db")
	w := testvectors.Wire()

	// Create and populate store
	s1, err := Open(dbPath, Options{Registry: projection.Base(), BusyTimeout: 5 * time.Second})
	if err != nil {
		t.Fatalf("Open 1: %v", err)
	}
	if _, err := s1.Initialize(); err != nil {
		t.Fatalf("Initialize 1: %v", err)
	}
	playBatch(t, s1, 0, w)
	playBatch(t, s1, 1, w)
	playBatch(t, s1, 2, w)
	_ = s1.Close()

	// Reopen and initialize
	s2, err := Open(dbPath, Options{Registry: projection.Base(), BusyTimeout: 5 * time.Second})
	if err != nil {
		t.Fatalf("Open 2: %v", err)
	}
	t.Cleanup(func() { _ = s2.Close() })

	snap, err := s2.Initialize()
	if err != nil {
		t.Fatalf("Initialize on existing store failed: %v", err)
	}
	if snap.Revision != 8 {
		t.Errorf("existing store revision = %d, want 8", snap.Revision)
	}
	wantHead, ok := w.Expected.StatusByRevision[2]["head_hash"].(string)
	if !ok {
		t.Fatal("missing status_by_revision[2].head_hash")
	}
	if snap.HeadHash != wantHead {
		t.Errorf("existing store head_hash = %q, want %q", snap.HeadHash, wantHead)
	}
}

// TestStore_EventsAndRawEvents tests reading events and canonical raw bytes.
func TestStore_EventsAndRawEvents(t *testing.T) {
	defer guard(t)

	s, w := newTestStore(t)
	if _, err := s.Initialize(); err != nil {
		t.Fatalf("Initialize: %v", err)
	}
	playBatch(t, s, 0, w)
	playBatch(t, s, 1, w)
	playBatch(t, s, 2, w)

	t.Run("EventsRange", func(t *testing.T) {
		defer guard(t)

		events, err := s.Events(1, 8)
		if err != nil {
			t.Fatalf("Events(1, 8) failed: %v", err)
		}
		if len(events) != 8 {
			t.Fatalf("len(Events(1, 8)) = %d, want 8", len(events))
		}

		for i, ev := range events {
			wantRev := int64(i + 1)
			if ev.Revision != wantRev {
				t.Errorf("event[%d].Revision = %d, want %d", i, ev.Revision, wantRev)
			}
			wantHash, ok := w.Events[i]["hash"].(string)
			if !ok {
				t.Fatalf("missing event[%d].hash", i)
			}
			if ev.Hash != wantHash {
				t.Errorf("event[%d].Hash = %q, want %q", i, ev.Hash, wantHash)
			}
			wantType, ok := w.Events[i]["type"].(string)
			if !ok {
				t.Fatalf("missing event[%d].type", i)
			}
			if ev.Type != wantType {
				t.Errorf("event[%d].Type = %q, want %q", i, ev.Type, wantType)
			}
		}
	})

	t.Run("RawEventsCanonicalBytes", func(t *testing.T) {
		defer guard(t)

		raws, err := s.RawEvents(1, 8)
		if err != nil {
			t.Fatalf("RawEvents(1, 8) failed: %v", err)
		}
		if len(raws) != 8 {
			t.Fatalf("len(RawEvents(1, 8)) = %d, want 8", len(raws))
		}

		for i, raw := range raws {
			wantBytes, err := codec.Encode(w.Events[i])
			if err != nil {
				t.Fatalf("Encode wire event[%d]: %v", i, err)
			}
			if string(raw) != string(wantBytes) {
				t.Errorf("raw event[%d] mismatch:\ngot:  %s\nwant: %s", i, string(raw), string(wantBytes))
			}
		}
	})
}
