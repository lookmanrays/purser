package store

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"time"

	"github.com/lookmanrays/purser/internal/kernel/codec"
	"github.com/lookmanrays/purser/internal/kernel/projection"
	"modernc.org/sqlite"
)

// Clock samples the current instant.
type Clock func() time.Time

// Snapshot is the state at a complete batch boundary.
type Snapshot struct {
	Revision   int64
	HeadHash   string
	Projection projection.Projection
}

// Decide returns the events to append while the store owns the write transaction.
type Decide func(s Snapshot, at time.Time) ([]codec.EventSpec, error)

// Options configures the reducer registry and SQLite lock wait.
type Options struct {
	Registry    *projection.Registry
	BusyTimeout time.Duration
}

// Store owns the SQLite event log and materialized projection.
type Store struct {
	db       *sql.DB
	path     string
	registry *projection.Registry
	history  historicalCache
}

// Open prepares a store handle. Initialize creates or checks its database.
func Open(path string, opts Options) (*Store, error) {
	absolute, err := filepath.Abs(path)
	if err != nil {
		return nil, databaseError(err)
	}
	if opts.Registry == nil {
		opts.Registry = projection.Base()
	}
	if opts.BusyTimeout == 0 {
		opts.BusyTimeout = 10 * time.Second
	}
	dsn := url.URL{Scheme: "file", Path: absolute,
		RawQuery: fmt.Sprintf("mode=rw&_txlock=immediate&_pragma=busy_timeout(%d)&_pragma=synchronous(FULL)", opts.BusyTimeout.Milliseconds())}
	db, err := sql.Open("sqlite", dsn.String())
	if err != nil {
		return nil, databaseError(err)
	}
	return &Store{db: db, path: absolute, registry: opts.Registry}, nil
}

// Close releases the database handle.
func (s *Store) Close() error { return databaseError(s.db.Close()) }

const maxWireInteger = 1<<53 - 1

const schema = `
CREATE TABLE IF NOT EXISTS events (revision INTEGER PRIMARY KEY, body BLOB NOT NULL) STRICT;
CREATE TABLE IF NOT EXISTS meta (id INTEGER PRIMARY KEY CHECK(id=1), head_revision INTEGER NOT NULL, head_hash TEXT NOT NULL) STRICT;
CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, body BLOB NOT NULL) STRICT, WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS receipts (actor_id TEXT, proposal_id TEXT, body BLOB NOT NULL, PRIMARY KEY(actor_id, proposal_id)) STRICT, WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS edges (kind TEXT, from_id TEXT, to_id TEXT, body BLOB NOT NULL, PRIMARY KEY(kind, from_id, to_id)) STRICT, WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS edges_by_support ON edges(kind, to_id);
INSERT OR IGNORE INTO meta VALUES (1, 0, '` + codec.ZeroHash + `');
PRAGMA application_id=1347768915;
PRAGMA user_version=1;`

// Initialize creates an empty database or verifies an existing store.
func (s *Store) Initialize() (Snapshot, error) {
	file, err := os.OpenFile(s.path, os.O_CREATE|os.O_RDONLY, 0o600)
	if err != nil {
		return Snapshot{}, databaseError(err)
	}
	if err := file.Close(); err != nil {
		return Snapshot{}, databaseError(err)
	}
	if _, err := identify(s.db); err != nil {
		return Snapshot{}, err
	}
	var mode string
	if err := s.db.QueryRow("PRAGMA journal_mode=WAL").Scan(&mode); err != nil {
		return Snapshot{}, databaseError(err)
	}
	if mode != "wal" {
		return Snapshot{}, storeError("unavailable", 0, "SQLite did not enable WAL")
	}
	tx, err := s.begin(false)
	if err != nil {
		return Snapshot{}, err
	}
	defer func() { _ = tx.Rollback() }()
	existing, err := identify(tx)
	if err != nil {
		return Snapshot{}, err
	}
	if !existing {
		if _, err := tx.Exec(schema); err != nil {
			return Snapshot{}, databaseError(err)
		}
	}
	snap, err := s.fold(tx, nil)
	if err != nil {
		return Snapshot{}, err
	}
	return snap, databaseError(tx.Commit())
}

type rowReader interface{ QueryRow(string, ...any) *sql.Row }

func identify(db rowReader) (bool, error) {
	var id, version, objects int
	err := db.QueryRow(`SELECT application_id, user_version, EXISTS(SELECT 1 FROM sqlite_schema)
		FROM pragma_application_id(), pragma_user_version()`).Scan(&id, &version, &objects)
	switch {
	case err != nil:
		return false, databaseError(err)
	case id == 0 && objects == 0:
		return false, nil
	case id != 0x50555253:
		return false, storeError("integrity_error", 0, "unrecognized SQLite database")
	case version != 1:
		return false, storeError("unsupported_version", 0, fmt.Sprintf("SQLite schema v%d", version))
	default:
		return true, nil
	}
}

func (s *Store) begin(readOnly bool) (*sql.Tx, error) {
	tx, err := s.db.BeginTx(context.Background(), &sql.TxOptions{ReadOnly: readOnly})
	if err != nil {
		return nil, databaseError(err)
	}
	var mode string
	err = tx.QueryRow("PRAGMA journal_mode").Scan(&mode)
	if err != nil || mode != "wal" {
		_ = tx.Rollback()
		if err != nil {
			return nil, databaseError(err)
		}
		return nil, storeError("unavailable", 0, "SQLite database is outside WAL mode")
	}
	return tx, nil
}

func storeError(code string, revision int64, detail string) error {
	return &codec.Error{Code: code, Revision: revision, Detail: detail}
}

func databaseError(err error) error {
	if err == nil {
		return nil
	}
	code := "unavailable"
	if e, ok := errors.AsType[*sqlite.Error](err); ok {
		switch e.Code() & 255 {
		case 1, 11, 17, 19, 26: // SQLite ERROR, CORRUPT, SCHEMA, CONSTRAINT, NOTADB.
			code = "integrity_error"
		}
	}
	return fmt.Errorf("%w: %w", storeError(code, 0, "SQLite"), err)
}
