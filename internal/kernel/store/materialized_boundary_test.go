package store

import (
	"testing"
	"time"

	"github.com/lookmanrays/purser/internal/kernel/codec"
)

func TestBatchWritesRepeatedRecordChangeOnce(t *testing.T) {
	s, w := newTestStore(t)
	if _, err := s.Initialize(); err != nil {
		t.Fatal(err)
	}
	playBatch(t, s, 0, w)
	execStoreSQL(t, s, `CREATE TABLE record_writes (id TEXT NOT NULL);
		CREATE TRIGGER track_record_update AFTER UPDATE ON records
		BEGIN INSERT INTO record_writes VALUES (NEW.id); END`)
	_, _, err := s.Transact("principal:local", "p-edits", fixedClock, func(Snapshot, time.Time) ([]codec.EventSpec, error) {
		return []codec.EventSpec{
			{Type: "CommitmentEdited", EventVersion: 1, Data: map[string]any{"id": "C-notify", "statement": "first"}},
			{Type: "CommitmentEdited", EventVersion: 1, Data: map[string]any{"id": "C-notify", "statement": "final"}},
		}, nil
	})
	if err != nil {
		t.Fatal(err)
	}
	var writes, matching int
	err = s.db.QueryRow("SELECT count(*), count(CASE WHEN id='C-notify' THEN 1 END) FROM record_writes").Scan(&writes, &matching)
	if err != nil || writes != 1 || matching != 1 {
		t.Fatalf("writes = %d total, %d for C-notify, error = %v", writes, matching, err)
	}
	snap, err := s.Snapshot(nil)
	if err != nil {
		t.Fatal(err)
	}
	record, ok := snap.Projection.Record("C-notify")
	if !ok || record.Fields["statement"] != "final" || record.Changed != snap.Revision {
		t.Fatalf("unexpected final record: %+v", record)
	}
}

func TestHistoricalSnapshotsIsolateCallersAndRecheckCachedBoundary(t *testing.T) {
	s, w := newTestStore(t)
	if _, err := s.Initialize(); err != nil {
		t.Fatal(err)
	}
	batch, _ := playBatch(t, s, 0, w)
	playBatch(t, s, 1, w)
	revision := batch.Revision
	want := mustChecksum(t, batch.Projection)
	for range 3 {
		snap, err := s.Snapshot(&revision)
		if err != nil {
			t.Fatal(err)
		}
		if snap.HeadHash != batch.HeadHash || mustChecksum(t, snap.Projection) != want {
			t.Fatalf("unexpected historical snapshot: %+v", snap)
		}
		snap.Projection.Records[0].Fields["statement"] = "caller edit"
		snap.Projection.Receipts[0].Details["caller edit"] = true
	}
	rewriteRawEvent(t, s, revision, func(raw []byte) []byte { return append(raw, '\n') })
	_, err := s.Snapshot(&revision)
	requireStoreError(t, err, "integrity_error", revision)
}
