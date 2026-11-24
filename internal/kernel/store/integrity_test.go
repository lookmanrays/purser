package store

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/lookmanrays/purser/internal/kernel/codec"
)

func requireStoreError(t *testing.T, err error, code string, revision int64) {
	t.Helper()
	var detail *codec.Error
	if !errors.As(err, &detail) || detail.Code != code || detail.Revision != revision {
		t.Fatalf("error = %v, want %s at revision %d", err, code, revision)
	}
}

func TestInitializeRejectsDamagedHistoryAtFirstRevision(t *testing.T) {
	tests := []struct {
		name, code string
		revision   int64
		checkHead  bool
		damage     func(*testing.T, *Store)
	}{
		{"trailing bytes", "integrity_error", 1, false, func(t *testing.T, s *Store) {
			rewriteRawEvent(t, s, 1, func(raw []byte) []byte { return append(raw, '\n') })
		}},
		{"duplicate member", "integrity_error", 1, false, func(t *testing.T, s *Store) {
			rewriteRawEvent(t, s, 1, func(raw []byte) []byte { return append([]byte(`{"v":1,`), raw[1:]...) })
		}},
		{"wrong field type", "integrity_error", 1, false, func(t *testing.T, s *Store) {
			rewriteRawEvent(t, s, 1, func(raw []byte) []byte { return bytes.Replace(raw, []byte(`"v":1`), []byte(`"v":true`), 1) })
		}},
		{"invalid time", "integrity_error", 1, false, func(t *testing.T, s *Store) {
			rewriteEvent(t, s, 1, func(e *codec.Event) { e.At = "2026-02-30T12:00:00.000000Z" }, false)
		}},
		{"unrepresentable batch end", "integrity_error", 1, false, func(t *testing.T, s *Store) {
			rewriteEvent(t, s, 1, func(e *codec.Event) { e.BatchStart, e.BatchSize = 2, 1<<53-1 }, false)
		}},
		{"bad hash precedes later malformed event", "integrity_error", 1, false, func(t *testing.T, s *Store) {
			rewriteRawEvent(t, s, 1, func(raw []byte) []byte {
				return bytes.Replace(raw, []byte("Add notification retries."), []byte("Change notification retries."), 1)
			})
			rewriteRawEvent(t, s, 2, func([]byte) []byte { return []byte("{}") })
		}},
		{"inconsistent batch actor", "integrity_error", 2, false, func(t *testing.T, s *Store) {
			rewriteEvent(t, s, 2, func(e *codec.Event) { e.ActorID = "principal:other" }, false)
		}},
		{"broken previous link", "integrity_error", 2, false, func(t *testing.T, s *Store) {
			rewriteEvent(t, s, 2, func(e *codec.Event) { e.PrevHash = codec.ZeroHash }, false)
		}},
		{"missing first event", "integrity_error", 1, false, func(t *testing.T, s *Store) {
			execStoreSQL(t, s, "DELETE FROM events WHERE revision=1")
		}},
		{"incomplete batch", "integrity_error", 2, true, func(t *testing.T, s *Store) {
			execStoreSQL(t, s, "DELETE FROM events WHERE revision=2")
		}},
		{"missing final batch", "integrity_error", 1, true, func(t *testing.T, s *Store) {
			execStoreSQL(t, s, "DELETE FROM events")
		}},
		{"head inside a batch", "integrity_error", 1, true, func(t *testing.T, s *Store) {
			execStoreSQL(t, s, "UPDATE meta SET head_revision=1")
		}},
		{"wrong checkpoint hash", "integrity_error", 2, true, func(t *testing.T, s *Store) {
			execStoreSQL(t, s, "UPDATE meta SET head_hash=?", codec.ZeroHash)
		}},
		{"malformed checkpoint hash", "integrity_error", 2, true, func(t *testing.T, s *Store) {
			execStoreSQL(t, s, "UPDATE meta SET head_hash=?", strings.Repeat("F", 64))
		}},
		{"missing checkpoint row", "integrity_error", 0, true, func(t *testing.T, s *Store) {
			execStoreSQL(t, s, "DELETE FROM meta")
		}},
		{"unsupported canonical wire version", "unsupported_version", 2, false, func(t *testing.T, s *Store) {
			rewriteEvent(t, s, 2, func(e *codec.Event) { e.V = 2 }, true)
		}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			s, w := newTestStore(t)
			if _, err := s.Initialize(); err != nil {
				t.Fatal(err)
			}
			playBatch(t, s, 0, w)
			tt.damage(t, s)
			if tt.checkHead {
				_, err := s.Snapshot(nil)
				requireStoreError(t, err, tt.code, tt.revision)
			}
			_, err := reopenStore(t, s).Initialize()
			requireStoreError(t, err, tt.code, tt.revision)
		})
	}
}

func rewriteRawEvent(t *testing.T, s *Store, revision int64, mutate func([]byte) []byte) {
	t.Helper()
	rows, err := s.RawEvents(revision, revision)
	if err != nil || len(rows) != 1 {
		t.Fatalf("read event %d: %d rows, %v", revision, len(rows), err)
	}
	execStoreSQL(t, s, "UPDATE events SET body=? WHERE revision=?", mutate(rows[0]), revision)
}

func rewriteEvent(t *testing.T, s *Store, revision int64, mutate func(*codec.Event), updateHead bool) {
	t.Helper()
	events, err := s.Events(revision, revision)
	if err != nil || len(events) != 1 {
		t.Fatalf("read event %d: %d rows, %v", revision, len(events), err)
	}
	e := events[0]
	mutate(&e)
	e.Hash, err = e.ComputeHash()
	if err != nil {
		t.Fatal(err)
	}
	body, err := codec.Encode(e.Map())
	if err != nil {
		t.Fatal(err)
	}
	execStoreSQL(t, s, "UPDATE events SET body=? WHERE revision=?", body, revision)
	if updateHead {
		execStoreSQL(t, s, "UPDATE meta SET head_hash=?", e.Hash)
	}
}

func reopenStore(t *testing.T, s *Store) *Store {
	t.Helper()
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}
	return openStoreForTest(t, s.path, Options{})
}

func TestStoreChecksMaterializedRowsAtHeadAndReopen(t *testing.T) {
	for _, tt := range []struct {
		name, query string
		checkHead   bool
	}{
		{"missing receipts", "DELETE FROM receipts", false},
		{"malformed record", "UPDATE records SET body=x'7b7d'", true},
		{"wrong record key", "UPDATE records SET id='Other' WHERE id='C-notify'", true},
		{"trailing bytes", "UPDATE records SET body=CAST(body || x'0a' AS BLOB)", true},
		{"wrong field type", `UPDATE records SET body=CAST(json_set(CAST(body AS TEXT), '$.changed', '1') AS BLOB)`, true},
		{"duplicate JSON key", `UPDATE records SET body=CAST('{"kind":"Commitment",' || substr(body, 2) AS BLOB)`, true},
	} {
		t.Run(tt.name, func(t *testing.T) {
			s, w := newTestStore(t)
			if _, err := s.Initialize(); err != nil {
				t.Fatal(err)
			}
			snap, _ := playBatch(t, s, 0, w)
			execStoreSQL(t, s, tt.query)

			head, err := s.Snapshot(nil)
			if tt.checkHead {
				requireStoreError(t, err, "integrity_error", snap.Revision)
			} else if err != nil || head.Revision != snap.Revision || len(head.Projection.Receipts) != 0 {
				t.Fatalf("head did not read the materialized projection: %+v, %v", head, err)
			}
			called := false
			_, _, err = s.Transact("principal:local", "p-next", fixedClock, func(Snapshot, time.Time) ([]codec.EventSpec, error) {
				called = true
				return nil, nil
			})
			if tt.checkHead {
				requireStoreError(t, err, "integrity_error", snap.Revision)
			} else if err != nil {
				t.Fatal(err)
			}
			if called == tt.checkHead {
				t.Fatalf("decide called = %t, want %t", called, !tt.checkHead)
			}

			historical, err := s.Snapshot(&snap.Revision)
			if err != nil {
				t.Fatalf("historical read from intact log: %v", err)
			}
			if historical.HeadHash != snap.HeadHash || historical.Revision != snap.Revision ||
				mustChecksum(t, historical.Projection) != mustChecksum(t, snap.Projection) {
				t.Fatalf("unexpected historical snapshot: %+v", historical)
			}
			_, err = reopenStore(t, s).Initialize()
			requireStoreError(t, err, "integrity_error", snap.Revision)
		})
	}
}

func TestInitializeRejectsForeignAndUnsupportedDatabases(t *testing.T) {
	for _, tt := range []struct {
		name, setup, code string
	}{
		{"foreign database", "CREATE TABLE foreign_data (value TEXT)", "integrity_error"},
		{"unsupported SQLite schema", "PRAGMA application_id=1347768915; PRAGMA user_version=2", "unsupported_version"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "foreign.db")
			if err := os.WriteFile(path, nil, 0o600); err != nil {
				t.Fatal(err)
			}
			s := openStoreForTest(t, path, Options{})
			execStoreSQL(t, s, tt.setup)
			_, err := s.Initialize()
			requireStoreError(t, err, tt.code, 0)
			var mode string
			if err := s.db.QueryRow("PRAGMA journal_mode").Scan(&mode); err != nil || mode != "delete" {
				t.Fatalf("rejected database changed journal mode: %q, %v", mode, err)
			}
		})
	}
	s := openStoreForTest(t, filepath.Join(t.TempDir(), "missing", "ledger.db"), Options{})
	_, err := s.Initialize()
	requireStoreError(t, err, "unavailable", 0)
	_, err = s.Snapshot(nil)
	requireStoreError(t, err, "unavailable", 0)
}
