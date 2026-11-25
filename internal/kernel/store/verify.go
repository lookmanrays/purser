package store

import (
	"database/sql"
	"errors"
	"fmt"

	"github.com/lookmanrays/purser/internal/kernel/codec"
)

// TrustedHead identifies an expected revision and hash at the head of the log.
type TrustedHead struct {
	Revision int64
	Hash     string
}

// Verify checks stored bytes, the contiguous chain, batch boundaries and known versions without writing.
func (s *Store) Verify(trusted *TrustedHead) error {
	tx, err := s.begin(true)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback() }()
	_, err = verifyHistory(tx, trusted, func(e codec.Event) error {
		switch {
		case e.V != 1:
			return storeError("unsupported_version", e.Revision, fmt.Sprintf("wire v%d", e.V))
		case !s.registry.Known(e.Type, e.EventVersion):
			return storeError("unsupported_version", e.Revision, fmt.Sprintf("%s v%d", e.Type, e.EventVersion))
		default:
			return nil
		}
	})
	return err
}

// verifyHistory checks each original batch before it can reach a reducer.
func verifyHistory(tx *sql.Tx, trusted *TrustedHead, interpret func(codec.Event) error) (TrustedHead, error) {
	anchor, err := retainedHead(tx)
	if err != nil {
		return TrustedHead{}, err
	}
	rows, err := tx.Query("SELECT revision, body FROM events ORDER BY revision")
	if err != nil {
		return TrustedHead{}, databaseError(err)
	}
	defer func() { _ = rows.Close() }()
	state := Snapshot{HeadHash: codec.ZeroHash}
	var batch []codec.Event
	for rows.Next() {
		next := state.Revision + int64(len(batch)) + 1
		event, err := scanEvent(rows, next)
		if err != nil {
			if at, issue := chainError(batch, state); issue != nil && at < next {
				return TrustedHead{}, issue
			}
			return TrustedHead{}, err
		}
		batch = append(batch, event)
		if int64(len(batch)) < batch[0].BatchSize {
			continue
		}
		if _, err := chainError(batch, state); err != nil {
			return TrustedHead{}, err
		}
		for _, e := range batch {
			if err := interpret(e); err != nil {
				return TrustedHead{}, err
			}
			if e.Revision == anchor.Revision && (e.Revision != event.Revision || e.Hash != anchor.Hash) {
				return TrustedHead{}, storeError("integrity_error", e.Revision, "retained checkpoint disagrees with its batch")
			}
		}
		state.Revision, state.HeadHash = event.Revision, event.Hash
		clear(batch)
		batch = batch[:0]
	}
	if err := rows.Err(); err != nil {
		return TrustedHead{}, databaseError(err)
	}
	if _, err := chainError(batch, state); err != nil {
		return TrustedHead{}, err
	}
	if anchor.Revision > state.Revision {
		return TrustedHead{}, storeError("integrity_error", state.Revision+1, "log ends before the retained checkpoint")
	}
	head := TrustedHead{state.Revision, state.HeadHash}
	if trusted != nil && *trusted != head {
		revision := head.Revision
		if trusted.Revision != revision {
			revision = min(trusted.Revision, revision) + 1
		}
		return TrustedHead{}, storeError("integrity_error", max(0, revision), "log head differs from the trusted head")
	}
	return head, nil
}

// A log-only restore can lack a checkpoint. Validate any surviving checkpoint
// before treating it as a retained anchor.
func retainedHead(tx *sql.Tx) (TrustedHead, error) {
	exists, err := identify(tx)
	if err != nil {
		return TrustedHead{}, err
	}
	if !exists {
		return TrustedHead{}, storeError("unavailable", 0, "uninitialized store")
	}
	var valid, empty bool
	switch err := tx.QueryRow(`SELECT type='table' AND ncol=3 AND strict=1 AND
		(SELECT count(*) FROM pragma_table_xinfo('meta', 'main') WHERE dflt_value IS NULL AND
		 (cid, name, type, pk, "notnull", hidden) IN (VALUES
			(0, 'id', 'INTEGER', 1, 0, 0), (1, 'head_revision', 'INTEGER', 0, 1, 0),
			(2, 'head_hash', 'TEXT', 0, 1, 0)))=3
		FROM pragma_table_list('meta') WHERE schema='main'`).Scan(&valid); {
	case errors.Is(err, sql.ErrNoRows):
		return TrustedHead{}, nil
	case err != nil:
		return TrustedHead{}, databaseError(err)
	case !valid:
		return TrustedHead{}, storeError("integrity_error", 0, "invalid head checkpoint table")
	}
	if err := tx.QueryRow("SELECT NOT EXISTS(SELECT 1 FROM meta)").Scan(&empty); err != nil {
		return TrustedHead{}, databaseError(err)
	}
	if empty {
		return TrustedHead{}, nil
	}
	revision, hash, err := checkpoint(tx)
	return TrustedHead{revision, hash}, err
}
