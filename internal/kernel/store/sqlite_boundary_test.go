package store

import (
	"bytes"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/lookmanrays/purser/internal/kernel/codec"
	"github.com/lookmanrays/purser/internal/kernel/projection"
	"github.com/lookmanrays/purser/internal/testvectors"
)

func fixedClock() time.Time { return time.Date(2026, 11, 18, 12, 0, 0, 0, time.UTC) }

func openStoreForTest(t *testing.T, path string, opts Options) *Store {
	t.Helper()
	s, err := Open(path, opts)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := s.Close(); err != nil {
			t.Error(err)
		}
	})
	return s
}

func execStoreSQL(t *testing.T, s *Store, query string, args ...any) {
	t.Helper()
	if _, err := s.db.Exec(query, args...); err != nil {
		t.Fatalf("SQL %q: %v", query, err)
	}
}

func createSpec() codec.EventSpec {
	return codec.EventSpec{Type: "CommitmentCreated", EventVersion: 1, Data: map[string]any{
		"id": "C-one", "root": "W-one", "principal": "principal:local", "statement": "First statement",
	}}
}

func TestSeparateSQLiteWritersAndReaderVisibility(t *testing.T) {
	path := filepath.Join(t.TempDir(), "log ?#.db")
	writer := openStoreForTest(t, path, Options{})
	if _, err := writer.Initialize(); err != nil {
		t.Fatal(err)
	}
	other := openStoreForTest(t, path, Options{BusyTimeout: 30 * time.Millisecond})
	var mode string
	var synchronous int
	if err := other.db.QueryRow("PRAGMA journal_mode").Scan(&mode); err != nil || mode != "wal" {
		t.Fatalf("journal mode = %q, %v", mode, err)
	}
	if err := other.db.QueryRow("PRAGMA synchronous").Scan(&synchronous); err != nil || synchronous != 2 {
		t.Fatalf("synchronous = %d, %v", synchronous, err)
	}

	entered, release := make(chan struct{}), make(chan struct{})
	var once sync.Once
	unblock := func() { once.Do(func() { close(release) }) }
	defer unblock()
	finished := make(chan error, 1)
	go func() {
		_, _, err := writer.Transact("principal:local", "p-first", fixedClock, func(Snapshot, time.Time) ([]codec.EventSpec, error) {
			close(entered)
			<-release
			return []codec.EventSpec{createSpec()}, nil
		})
		finished <- err
	}()
	select {
	case <-entered:
	case err := <-finished:
		t.Fatalf("writer exited before taking ownership: %v", err)
	case <-time.After(5 * time.Second):
		t.Fatal("writer did not enter decide")
	}

	snap, err := other.Snapshot(nil)
	if err != nil || snap.Revision != 0 || snap.HeadHash != codec.ZeroHash {
		t.Fatalf("concurrent reader got %v, %v", snap, err)
	}
	_, _, err = other.Transact("principal:local", "p-second", func() time.Time {
		t.Error("sampled clock before write ownership")
		return fixedClock()
	}, func(Snapshot, time.Time) ([]codec.EventSpec, error) {
		t.Error("second writer entered decide while first writer held ownership")
		return nil, nil
	})
	requireStoreError(t, err, "unavailable", 0)

	unblock()
	if err := <-finished; err != nil {
		t.Fatal(err)
	}
	second, _, err := other.Transact("principal:local", "p-second", fixedClock, func(before Snapshot, _ time.Time) ([]codec.EventSpec, error) {
		if before.Revision != 1 {
			t.Errorf("second writer read revision %d, want 1", before.Revision)
		}
		record, _ := before.Projection.Record("C-one")
		record.Fields["statement"] = "local callback mutation"
		return []codec.EventSpec{{Type: "CommitmentActivated", EventVersion: 1, Data: map[string]any{"id": "C-one"}}}, nil
	})
	if err != nil || second.Revision != 2 {
		t.Fatalf("writer retry got %+v, %v", second, err)
	}
	record, _ := second.Projection.Record("C-one")
	if record.Fields["statement"] != "First statement" || record.Fields["state"] != "active" {
		t.Fatalf("unexpected record after retry: %+v", record)
	}
}

func TestProjectionAndEventsRollBackWhenHeadUpdateFails(t *testing.T) {
	s, w := newTestStore(t)
	if _, err := s.Initialize(); err != nil {
		t.Fatal(err)
	}
	before, _ := playBatch(t, s, 0, w)
	execStoreSQL(t, s, `CREATE TRIGGER reject_head BEFORE UPDATE ON meta
		WHEN (SELECT count(*) FROM events)=3 AND EXISTS(SELECT 1 FROM records WHERE id='C-one')
		BEGIN SELECT RAISE(ABORT, 'injected head update failure'); END`)
	_, _, err := s.Transact("principal:local", "p-fault", fixedClock, func(Snapshot, time.Time) ([]codec.EventSpec, error) {
		return []codec.EventSpec{createSpec()}, nil
	})
	requireStoreError(t, err, "integrity_error", 0)
	after, err := s.Snapshot(nil)
	if err != nil || after.Revision != before.Revision || after.HeadHash != before.HeadHash {
		t.Fatalf("head after rollback: %+v, %v", after, err)
	}
	events, err := s.RawEvents(1, 10)
	if err != nil || len(events) != 2 {
		t.Fatalf("events after rollback: %d, %v", len(events), err)
	}
	if _, ok := after.Projection.Record("C-one"); ok {
		t.Fatal("uncommitted record survived rollback")
	}
}

func TestStoreMatchesExactWireSnapshotsAndLegacyHead(t *testing.T) {
	t.Run("wire", func(t *testing.T) {
		s, w := newTestStore(t)
		if _, err := s.Initialize(); err != nil {
			t.Fatal(err)
		}
		for i := range w.ClockByBatch {
			playBatch(t, s, i, w)
		}
		for _, want := range []map[string]any{w.Expected.SnapshotAt4, w.Expected.SnapshotAt8} {
			revision := want["revision"].(int64)
			got, err := s.Snapshot(&revision)
			if err != nil {
				t.Fatal(err)
			}
			gotBytes, err := codec.Encode(map[string]any{"revision": got.Revision, "head_hash": got.HeadHash, "projection": got.Projection.Map()})
			if err != nil {
				t.Fatal(err)
			}
			wantBytes, err := codec.Encode(want)
			if err != nil || !bytes.Equal(gotBytes, wantBytes) {
				t.Fatalf("snapshot at %d differs from the wire bytes: %v", revision, err)
			}
		}
		execStoreSQL(t, s, "UPDATE events SET body=x'7b7d' WHERE revision=8")
		revision := int64(4)
		if _, err := s.Snapshot(&revision); err != nil {
			t.Fatalf("valid historical prefix: %v", err)
		}
		_, err := s.Events(8, 8)
		requireStoreError(t, err, "integrity_error", 8)
	})

	t.Run("legacy", func(t *testing.T) {
		path := filepath.Join(t.TempDir(), "legacy.db")
		s := openStoreForTest(t, path, Options{})
		if _, err := s.Initialize(); err != nil {
			t.Fatal(err)
		}
		w := testvectors.Wire()
		first := w.Legacy.Events[0]
		at, err := codec.ParseTime(first["at"].(string))
		if err != nil {
			t.Fatal(err)
		}
		specs := make([]codec.EventSpec, len(w.Legacy.Events))
		for i, event := range w.Legacy.Events {
			specs[i] = codec.EventSpec{Type: event["type"].(string), EventVersion: event["event_version"].(int64), Data: event["data"].(map[string]any)}
		}
		_, _, err = s.Transact(first["actor_id"].(string), first["proposal_id"].(string), func() time.Time { return at }, func(Snapshot, time.Time) ([]codec.EventSpec, error) {
			return specs, nil
		})
		if err != nil {
			t.Fatal(err)
		}
		reopened := openStoreForTest(t, path, Options{})
		got, err := reopened.Initialize()
		if err != nil {
			t.Fatal(err)
		}
		checksum, err := got.Projection.Checksum()
		if err != nil || got.HeadHash != w.Legacy.ExpectedHeadHash || checksum != w.Legacy.ExpectedProjectionHash {
			t.Fatalf("legacy checkpoint differs: head=%s projection=%s error=%v", got.HeadHash, checksum, err)
		}
		raw, err := reopened.RawEvents(1, 1)
		want, encodeErr := codec.Encode(first)
		if err != nil || encodeErr != nil || len(raw) != 1 || !bytes.Equal(raw[0], want) {
			t.Fatalf("legacy event bytes changed: read=%v encode=%v rows=%d", err, encodeErr, len(raw))
		}
	})
}

func TestMaterializedEdgeRemovalAndUnchangedRecords(t *testing.T) {
	reg := projection.Base()
	edge := projection.Edge{Kind: "depends_on", FromID: "W-root", ToID: "C-notify"}
	if err := reg.Register("TestEdgeAdded", 1, func(p projection.Projection, _ map[string]any, revision, _ int64) (projection.Projection, projection.Changes, error) {
		p.Edges = []projection.Edge{{Kind: edge.Kind, FromID: edge.FromID, ToID: edge.ToID, Changed: revision}}
		return p, projection.Changes{Edges: p.Edges}, nil
	}); err != nil {
		t.Fatal(err)
	}
	if err := reg.Register("TestEdgeRemoved", 1, func(p projection.Projection, _ map[string]any, _, _ int64) (projection.Projection, projection.Changes, error) {
		p.Edges = nil
		return p, projection.Changes{Removed: []projection.Edge{edge}}, nil
	}); err != nil {
		t.Fatal(err)
	}
	s := openStoreForTest(t, filepath.Join(t.TempDir(), "edges.db"), Options{Registry: reg})
	if _, err := s.Initialize(); err != nil {
		t.Fatal(err)
	}
	playBatch(t, s, 0, testvectors.Wire())
	execStoreSQL(t, s, `CREATE TRIGGER protect_records BEFORE DELETE ON records
		BEGIN SELECT RAISE(ABORT, 'record rewritten'); END`)
	execStoreSQL(t, s, `CREATE TRIGGER protect_record_updates BEFORE UPDATE ON records
		BEGIN SELECT RAISE(ABORT, 'record updated'); END`)
	for i, eventType := range []string{"TestEdgeAdded", "TestEdgeRemoved"} {
		_, _, err := s.Transact("principal:local", "p-edge", fixedClock, func(Snapshot, time.Time) ([]codec.EventSpec, error) {
			return []codec.EventSpec{{Type: eventType, EventVersion: 1, Data: map[string]any{}}}, nil
		})
		if err != nil {
			t.Fatal(err)
		}
		var rows int
		if err := s.db.QueryRow("SELECT count(*) FROM edges").Scan(&rows); err != nil || rows != 1-i {
			t.Fatalf("after %s: %d edge rows, %v", eventType, rows, err)
		}
	}
}
