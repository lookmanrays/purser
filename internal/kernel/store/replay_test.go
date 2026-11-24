package store

import (
	"bytes"
	"path/filepath"
	"reflect"
	"sync"
	"testing"
	"time"

	"github.com/lookmanrays/purser/internal/kernel/codec"
	"github.com/lookmanrays/purser/internal/testvectors"
)

// populateLegacyStore initializes a store and populates it with the legacy wire vector events.
func populateLegacyStore(t *testing.T, w *testvectors.WireVector) *Store {
	t.Helper()
	path := filepath.Join(t.TempDir(), "legacy.db")
	s := openStoreForTest(t, path, Options{})
	if _, err := s.Initialize(); err != nil {
		t.Fatalf("Initialize legacy store: %v", err)
	}

	first := w.Legacy.Events[0]
	at, err := codec.ParseTime(first["at"].(string))
	if err != nil {
		t.Fatalf("ParseTime legacy at: %v", err)
	}
	specs := make([]codec.EventSpec, len(w.Legacy.Events))
	for i, event := range w.Legacy.Events {
		specs[i] = codec.EventSpec{
			Type:         event["type"].(string),
			EventVersion: event["event_version"].(int64),
			Data:         event["data"].(map[string]any),
		}
	}
	actorID := first["actor_id"].(string)
	proposalID := first["proposal_id"].(string)
	clock := func() time.Time { return at }
	_, _, err = s.Transact(actorID, proposalID, clock, func(Snapshot, time.Time) ([]codec.EventSpec, error) {
		return specs, nil
	})
	if err != nil {
		t.Fatalf("Transact legacy events: %v", err)
	}
	return s
}

// AC-1: Replaying the legacy vector gives expected_projection_hash and expected_head_hash.
func TestAC1_ReplayLegacyVector(t *testing.T) {
	defer guard(t)

	w := testvectors.Wire()
	if w == nil {
		t.Fatal("Wire vector is nil")
	}

	t.Run("ReplayWithoutTrustedHead", func(t *testing.T) {
		defer guard(t)

		s := populateLegacyStore(t, w)
		snap, err := s.Replay(nil)
		if err != nil {
			t.Fatalf("Replay(nil) failed: %v", err)
		}
		if snap.HeadHash != w.Legacy.ExpectedHeadHash {
			t.Fatalf("HeadHash = %q, want %q", snap.HeadHash, w.Legacy.ExpectedHeadHash)
		}
		if snap.Revision != int64(len(w.Legacy.Events)) {
			t.Fatalf("Revision = %d, want %d", snap.Revision, len(w.Legacy.Events))
		}
		sum, err := snap.Projection.Checksum()
		if err != nil {
			t.Fatalf("Checksum: %v", err)
		}
		if sum != w.Legacy.ExpectedProjectionHash {
			t.Fatalf("Projection checksum = %q, want %q", sum, w.Legacy.ExpectedProjectionHash)
		}
	})

	t.Run("ReplayWithMatchingTrustedHead", func(t *testing.T) {
		defer guard(t)

		s := populateLegacyStore(t, w)
		trusted := &TrustedHead{
			Revision: int64(len(w.Legacy.Events)),
			Hash:     w.Legacy.ExpectedHeadHash,
		}
		snap, err := s.Replay(trusted)
		if err != nil {
			t.Fatalf("Replay(trusted) failed: %v", err)
		}
		if snap.HeadHash != w.Legacy.ExpectedHeadHash {
			t.Fatalf("HeadHash = %q, want %q", snap.HeadHash, w.Legacy.ExpectedHeadHash)
		}
		sum, err := snap.Projection.Checksum()
		if err != nil {
			t.Fatalf("Checksum: %v", err)
		}
		if sum != w.Legacy.ExpectedProjectionHash {
			t.Fatalf("Projection checksum = %q, want %q", sum, w.Legacy.ExpectedProjectionHash)
		}
	})
}

// AC-4: Concurrent readers during replay always see a complete old or new snapshot.
func TestAC4_ConcurrentReadersDuringReplay(t *testing.T) {
	defer guard(t)

	s, w := newTestStore(t)
	if _, err := s.Initialize(); err != nil {
		t.Fatalf("Initialize: %v", err)
	}

	// Populate batch 0
	oldSnap, _ := playBatch(t, s, 0, w)
	oldSum, err := oldSnap.Projection.Checksum()
	if err != nil {
		t.Fatalf("Checksum oldSnap: %v", err)
	}

	// Insert batch 1 events into the events table directly
	for i := 2; i <= 3; i++ {
		evMap := w.Events[i]
		raw, encodeErr := codec.Encode(evMap)
		if encodeErr != nil {
			t.Fatalf("Encode event %d: %v", i, encodeErr)
		}
		rev := evMap["revision"].(int64)
		execStoreSQL(t, s, "INSERT INTO events (revision, body) VALUES (?, ?)", rev, raw)
	}

	newHash := w.Expected.SnapshotAt4["head_hash"].(string)
	newRev := w.Expected.SnapshotAt4["revision"].(int64)

	done := make(chan struct{})
	defer func() {
		select {
		case <-done:
		default:
			close(done)
		}
	}()

	var readerErr error
	var errMu sync.Mutex
	setReaderErr := func(err error) {
		errMu.Lock()
		defer errMu.Unlock()
		if readerErr == nil {
			readerErr = err
		}
	}

	const readerCount = 4
	var wg sync.WaitGroup
	for r := 0; r < readerCount; r++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for {
				select {
				case <-done:
					return
				default:
					snap, snapErr := s.Snapshot(nil)
					if snapErr != nil {
						setReaderErr(snapErr)
						return
					}
					sum, sumErr := snap.Projection.Checksum()
					if sumErr != nil {
						setReaderErr(sumErr)
						return
					}
					isOld := snap.Revision == oldSnap.Revision && snap.HeadHash == oldSnap.HeadHash && sum == oldSum
					isNew := snap.Revision == newRev && snap.HeadHash == newHash
					if !isOld && !isNew {
						setReaderErr(bytes.ErrTooLarge) // Sentinel for invalid torn read
						return
					}
				}
			}
		}()
	}

	// Run replay in the main test thread
	replayedSnap, replayErr := s.Replay(nil)
	close(done)
	wg.Wait()

	if replayErr != nil {
		t.Fatalf("Replay failed: %v", replayErr)
	}
	if readerErr != nil {
		t.Fatalf("concurrent reader observed invalid/torn state or error: %v", readerErr)
	}
	if replayedSnap.Revision != newRev || replayedSnap.HeadHash != newHash {
		t.Fatalf("Replay snapshot = (rev=%d, hash=%s), want (rev=%d, hash=%s)",
			replayedSnap.Revision, replayedSnap.HeadHash, newRev, newHash)
	}
}

// AC-5: Replay at the head returns the same snapshot as Snapshot(nil).
func TestAC5_ReplayAtHeadEqualsSnapshot(t *testing.T) {
	defer guard(t)

	w := testvectors.Wire()
	if w == nil {
		t.Fatal("Wire vector is nil")
	}

	t.Run("MultiBatchStore", func(t *testing.T) {
		defer guard(t)

		s, _ := newTestStore(t)
		if _, err := s.Initialize(); err != nil {
			t.Fatalf("Initialize: %v", err)
		}
		for i := range w.ClockByBatch {
			playBatch(t, s, i, w)
		}

		snapBefore, err := s.Snapshot(nil)
		if err != nil {
			t.Fatalf("Snapshot(nil) before: %v", err)
		}
		replayed, err := s.Replay(nil)
		if err != nil {
			t.Fatalf("Replay(nil): %v", err)
		}

		if replayed.Revision != snapBefore.Revision {
			t.Fatalf("Revision: replayed=%d, snap=%d", replayed.Revision, snapBefore.Revision)
		}
		if replayed.HeadHash != snapBefore.HeadHash {
			t.Fatalf("HeadHash: replayed=%s, snap=%s", replayed.HeadHash, snapBefore.HeadHash)
		}

		sumSnap, err := snapBefore.Projection.Checksum()
		if err != nil {
			t.Fatalf("Checksum snapBefore: %v", err)
		}
		sumReplay, err := replayed.Projection.Checksum()
		if err != nil {
			t.Fatalf("Checksum replayed: %v", err)
		}
		if sumReplay != sumSnap {
			t.Fatalf("Projection checksum: replayed=%s, snap=%s", sumReplay, sumSnap)
		}

		if !reflect.DeepEqual(replayed.Projection.Records, snapBefore.Projection.Records) {
			t.Fatal("Projection records differ between Replay and Snapshot")
		}
		if !reflect.DeepEqual(replayed.Projection.Receipts, snapBefore.Projection.Receipts) {
			t.Fatal("Projection receipts differ between Replay and Snapshot")
		}
		if !reflect.DeepEqual(replayed.Projection.Edges, snapBefore.Projection.Edges) {
			t.Fatal("Projection edges differ between Replay and Snapshot")
		}

		snapAfter, err := s.Snapshot(nil)
		if err != nil {
			t.Fatalf("Snapshot(nil) after: %v", err)
		}
		if !reflect.DeepEqual(snapAfter.Projection.Records, replayed.Projection.Records) {
			t.Fatal("Installed projection records differ after Replay")
		}
	})

	t.Run("FreshEmptyStore", func(t *testing.T) {
		defer guard(t)

		s, _ := newTestStore(t)
		initSnap, err := s.Initialize()
		if err != nil {
			t.Fatalf("Initialize: %v", err)
		}

		replayed, err := s.Replay(nil)
		if err != nil {
			t.Fatalf("Replay(nil) on empty store: %v", err)
		}

		if replayed.Revision != 0 || replayed.HeadHash != codec.ZeroHash {
			t.Fatalf("empty replay got rev=%d, hash=%s", replayed.Revision, replayed.HeadHash)
		}
		if !reflect.DeepEqual(replayed.Projection.Records, initSnap.Projection.Records) {
			t.Fatal("empty replay records differ from initialize")
		}
	})
}
