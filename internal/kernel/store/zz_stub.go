package store

// Stubs for P1-02a, from contracts/kernel/store.yaml (parameter names blanked
// for the linter). The builder replaces this file with the implementation.

import (
	"time"

	"github.com/lookmanrays/purser/internal/kernel/codec"
	"github.com/lookmanrays/purser/internal/kernel/projection"
)

var unimplemented = func() { panic("unimplemented: P1-02a") }

// Clock samples the current instant in UTC.
type Clock func() time.Time

// Snapshot holds the log state at a batch boundary.
type Snapshot struct {
	Revision   int64
	HeadHash   string
	Projection projection.Projection
}

// Decide is the gate callback invoked inside a transaction with write ownership.
type Decide func(s Snapshot, at time.Time) ([]codec.EventSpec, error)

// Options configures Store opening.
type Options struct {
	Registry    *projection.Registry
	BusyTimeout time.Duration
}

// Store owns the SQLite event log and projections.
type Store struct{}

// Open opens or creates a Store at the given SQLite database path.
func Open(_ string, _ Options) (*Store, error) { unimplemented(); return nil, nil }

// Close closes the underlying database.
func (*Store) Close() error { unimplemented(); return nil }

// Initialize sets up a new database or verifies an existing database.
func (*Store) Initialize() (Snapshot, error) { unimplemented(); return Snapshot{}, nil }

// Snapshot reads the log snapshot as of the given revision, or head if nil.
func (*Store) Snapshot(_ *int64) (Snapshot, error) { unimplemented(); return Snapshot{}, nil }

// Transact runs a mutation inside a write transaction.
func (*Store) Transact(_, _ string, _ Clock, _ Decide) (Snapshot, []codec.Event, error) {
	unimplemented()
	return Snapshot{}, nil, nil
}

// Events reads decoded events in the revision range [from, to].
func (*Store) Events(_, _ int64) ([]codec.Event, error) { unimplemented(); return nil, nil }

// RawEvents reads canonical JSON event bytes in the revision range [from, to].
func (*Store) RawEvents(_, _ int64) ([][]byte, error) { unimplemented(); return nil, nil }

// TrustedHead specifies an anchored revision and hash for verification.
type TrustedHead struct {
	Revision int64
	Hash     string
}

// Verify verifies stored event bytes and chain hashes against an optional trusted head.
func (*Store) Verify(_ *TrustedHead) error { unimplemented(); return nil }

// Replay rebuilds projections by folding the event log from revision 0.
func (*Store) Replay(_ *TrustedHead) (Snapshot, error) { unimplemented(); return Snapshot{}, nil }
