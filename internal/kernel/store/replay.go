package store

import (
	"github.com/lookmanrays/purser/internal/kernel/codec"
	"github.com/lookmanrays/purser/internal/kernel/projection"
)

// Replay verifies the log and atomically installs its rebuilt projection.
func (s *Store) Replay(trusted *TrustedHead) (Snapshot, error) {
	tx, err := s.begin(false)
	if err != nil {
		return Snapshot{}, err
	}
	defer func() { _ = tx.Rollback() }()
	p := projection.Empty()
	head, err := verifyHistory(tx, trusted, func(event codec.Event) error {
		next, _, err := s.apply(p, event)
		p = next
		return err
	})
	if err != nil {
		return Snapshot{}, err
	}
	// Recreate missing projection tables on a log-only restore. Replace all
	// rows together so WAL readers see either complete installation.
	if _, err := tx.Exec(schema); err != nil {
		return Snapshot{}, databaseError(err)
	}
	view := p.Map()
	for _, table := range projectionTables {
		rows, err := table.encode(view, head.Revision)
		if err != nil {
			return Snapshot{}, err
		}
		if _, err := tx.Exec("DELETE FROM " + table.name); err != nil {
			return Snapshot{}, databaseError(err)
		}
		if err := table.write(tx, nil, rows); err != nil {
			return Snapshot{}, err
		}
	}
	if _, err := tx.Exec("UPDATE meta SET head_revision=?, head_hash=? WHERE id=1", head.Revision, head.Hash); err != nil {
		return Snapshot{}, databaseError(err)
	}
	if err := tx.Commit(); err != nil {
		return Snapshot{}, databaseError(err)
	}
	return Snapshot{head.Revision, head.Hash, p}, nil
}
