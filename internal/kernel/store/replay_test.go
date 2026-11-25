package store

import (
	"fmt"
	"path/filepath"
	"reflect"
	"sync"
	"testing"
	"time"

	"github.com/lookmanrays/purser/internal/kernel/codec"
	"github.com/lookmanrays/purser/internal/kernel/projection"
	"github.com/lookmanrays/purser/internal/testvectors"
)

func execSQL(t *testing.T, s *Store, query string, args ...any) {
	t.Helper()
	if _, err := s.db.Exec(query, args...); err != nil {
		t.Fatalf("execSQL %q: %v", query, err)
	}
}

func openStoreForReplayTest(t *testing.T, path string) *Store {
	t.Helper()
	s, err := Open(path, Options{
		Registry:    projection.Base(),
		BusyTimeout: 5 * time.Second,
	})
	if err != nil {
		t.Fatalf("Open %s: %v", path, err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s
}

// populateLegacyStore initializes a store and populates it with the legacy wire vector events.
func populateLegacyStore(t *testing.T, w *testvectors.WireVector) *Store {
	t.Helper()
	path := filepath.Join(t.TempDir(), "legacy.db")
	s := openStoreForReplayTest(t, path)
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

		// Delete materialized projection rows to prove Replay rebuilds from the event log.
		execSQL(t, s, "DELETE FROM records; DELETE FROM receipts; DELETE FROM edges")

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

		// A later Snapshot(nil) must also see the rebuilt projection
		afterSnap, err := s.Snapshot(nil)
		if err != nil {
			t.Fatalf("Snapshot(nil) after Replay: %v", err)
		}
		afterSum, err := afterSnap.Projection.Checksum()
		if err != nil {
			t.Fatalf("Checksum afterSnap: %v", err)
		}
		if afterSum != w.Legacy.ExpectedProjectionHash {
			t.Fatalf("Snapshot(nil) checksum = %q, want %q", afterSum, w.Legacy.ExpectedProjectionHash)
		}
	})

	t.Run("ReplayWithMatchingTrustedHead", func(t *testing.T) {
		defer guard(t)

		s := populateLegacyStore(t, w)
		trusted := &TrustedHead{
			Revision: int64(len(w.Legacy.Events)),
			Hash:     w.Legacy.ExpectedHeadHash,
		}

		// Delete materialized projection rows to prove Replay rebuilds from the event log.
		execSQL(t, s, "DELETE FROM records; DELETE FROM receipts; DELETE FROM edges")

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

		afterSnap, err := s.Snapshot(nil)
		if err != nil {
			t.Fatalf("Snapshot(nil) after Replay(trusted): %v", err)
		}
		afterSum, err := afterSnap.Projection.Checksum()
		if err != nil {
			t.Fatalf("Checksum afterSnap: %v", err)
		}
		if afterSum != w.Legacy.ExpectedProjectionHash {
			t.Fatalf("Snapshot(nil) checksum = %q, want %q", afterSum, w.Legacy.ExpectedProjectionHash)
		}
	})
}

// AC-4: Concurrent readers during replay always see a complete old or new snapshot.
func TestAC4_ConcurrentReadersDuringReplay(t *testing.T) {
	defer guard(t)

	// Build a consistent store with P1-02b's uniform store helper.
	s := makeUniformStore(t, 2000)
	newSnap, err := s.Snapshot(nil)
	if err != nil {
		t.Fatalf("Snapshot before delete: %v", err)
	}
	newSum, err := newSnap.Projection.Checksum()
	if err != nil {
		t.Fatalf("Checksum newSnap: %v", err)
	}

	// Delete one materialized record row. The log is untouched, so head revision & hash match meta,
	// but the installed projection is missing a row.
	execSQL(t, s, "DELETE FROM records WHERE id = (SELECT id FROM records LIMIT 1)")

	oldSnap, err := s.Snapshot(nil)
	if err != nil {
		t.Fatalf("Snapshot after delete: %v", err)
	}
	oldSum, err := oldSnap.Projection.Checksum()
	if err != nil {
		t.Fatalf("Checksum oldSnap: %v", err)
	}
	if oldSum == newSum {
		t.Fatal("oldSum and newSum must differ after deleting a record")
	}

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
	var readyWg sync.WaitGroup
	var doneWg sync.WaitGroup
	for r := 0; r < readerCount; r++ {
		readyWg.Add(1)
		doneWg.Add(1)
		go func() {
			defer doneWg.Done()
			first := true
			for {
				select {
				case <-done:
					return
				default:
					snap, snapErr := s.Snapshot(nil)
					if first {
						readyWg.Done()
						first = false
					}
					if snapErr != nil {
						setReaderErr(fmt.Errorf("reader Snapshot(nil) error: %w", snapErr))
						return
					}
					sum, sumErr := snap.Projection.Checksum()
					if sumErr != nil {
						setReaderErr(fmt.Errorf("reader Checksum error: %w", sumErr))
						return
					}
					isOld := snap.Revision == oldSnap.Revision && snap.HeadHash == oldSnap.HeadHash && sum == oldSum
					isNew := snap.Revision == newSnap.Revision && snap.HeadHash == newSnap.HeadHash && sum == newSum
					if !isOld && !isNew {
						setReaderErr(fmt.Errorf("torn read: revision=%d, hash=%s, checksum=%s (want old sum=%s or new sum=%s)",
							snap.Revision, snap.HeadHash, sum, oldSum, newSum))
						return
					}
				}
			}
		}()
	}

	// Wait for all readers to complete at least one read before calling Replay
	readyWg.Wait()

	// Replay rebuilds the complete projection and installs it atomically
	replayedSnap, replayErr := s.Replay(nil)
	close(done)
	doneWg.Wait()

	if replayErr != nil {
		t.Fatalf("Replay failed: %v", replayErr)
	}
	if readerErr != nil {
		t.Fatalf("concurrent reader error: %v", readerErr)
	}
	replayedSum, err := replayedSnap.Projection.Checksum()
	if err != nil {
		t.Fatalf("Checksum replayedSnap: %v", err)
	}
	if replayedSnap.Revision != newSnap.Revision || replayedSnap.HeadHash != newSnap.HeadHash || replayedSum != newSum {
		t.Fatalf("Replay snapshot = (rev=%d, hash=%s, sum=%s), want (rev=%d, hash=%s, sum=%s)",
			replayedSnap.Revision, replayedSnap.HeadHash, replayedSum, newSnap.Revision, newSnap.HeadHash, newSum)
	}

	// Afterwards Snapshot(nil) must equal new
	afterSnap, err := s.Snapshot(nil)
	if err != nil {
		t.Fatalf("Snapshot(nil) after Replay: %v", err)
	}
	afterSum, err := afterSnap.Projection.Checksum()
	if err != nil {
		t.Fatalf("Checksum afterSnap: %v", err)
	}
	if afterSnap.Revision != newSnap.Revision || afterSnap.HeadHash != newSnap.HeadHash || afterSum != newSum {
		t.Fatalf("Snapshot after Replay = (rev=%d, hash=%s, sum=%s), want (rev=%d, hash=%s, sum=%s)",
			afterSnap.Revision, afterSnap.HeadHash, afterSum, newSnap.Revision, newSnap.HeadHash, newSum)
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
		sumBefore, err := snapBefore.Projection.Checksum()
		if err != nil {
			t.Fatalf("Checksum snapBefore: %v", err)
		}

		// Delete materialized projection rows to prove Replay rebuilds from the event log.
		execSQL(t, s, "DELETE FROM records; DELETE FROM receipts; DELETE FROM edges")

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

		sumReplay, err := replayed.Projection.Checksum()
		if err != nil {
			t.Fatalf("Checksum replayed: %v", err)
		}
		if sumReplay != sumBefore {
			t.Fatalf("Projection checksum: replayed=%s, snap=%s", sumReplay, sumBefore)
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

		// Snapshot(nil) after Replay must equal replayed and snapBefore
		snapAfter, err := s.Snapshot(nil)
		if err != nil {
			t.Fatalf("Snapshot(nil) after: %v", err)
		}
		if !reflect.DeepEqual(snapAfter.Projection.Records, snapBefore.Projection.Records) {
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
