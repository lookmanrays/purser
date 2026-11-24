package store

import (
	"bytes"
	"database/sql"
	"errors"
	"fmt"
	"strings"

	"github.com/lookmanrays/purser/internal/kernel/codec"
	"github.com/lookmanrays/purser/internal/kernel/projection"
)

// Snapshot reads a complete historical batch, or the current head when revision is nil.
func (s *Store) Snapshot(revision *int64) (Snapshot, error) {
	tx, err := s.begin(true)
	if err != nil {
		return Snapshot{}, err
	}
	defer func() { _ = tx.Rollback() }()
	if revision == nil {
		return headSnapshot(tx)
	}
	return s.fold(tx, revision)
}

func headSnapshot(tx *sql.Tx) (Snapshot, error) {
	exists, err := identify(tx)
	if err != nil {
		return Snapshot{}, err
	}
	if !exists {
		return Snapshot{}, storeError("unavailable", 0, "uninitialized store")
	}
	revision, hash, err := checkpoint(tx)
	if err != nil {
		return Snapshot{}, err
	}
	// The INTEGER PRIMARY KEY lets SQLite find the tail without scanning the log.
	last, err := scanBoundary(tx.QueryRow("SELECT revision, body FROM events ORDER BY revision DESC LIMIT 1"))
	switch {
	case errors.Is(err, sql.ErrNoRows):
		if revision != 0 {
			return Snapshot{}, storeError("integrity_error", 1, "head checkpoint has no events")
		}
	case err != nil:
		return Snapshot{}, err
	case last.Revision != revision:
		return Snapshot{}, storeError("integrity_error", min(revision, last.Revision+1), "head revision differs from the log")
	case last.BatchStart+last.BatchSize-1 != revision:
		return Snapshot{}, storeError("integrity_error", revision, "last event does not end its batch")
	case last.Hash != hash:
		return Snapshot{}, storeError("integrity_error", revision, "head hash differs from the last event")
	}
	p, err := readProjection(tx, revision)
	if err != nil {
		return Snapshot{}, err
	}
	return Snapshot{revision, hash, p}, nil
}

func scanBoundary(row *sql.Row) (codec.Event, error) {
	var revision int64
	var body any
	if err := row.Scan(&revision, &body); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return codec.Event{}, err
		}
		return codec.Event{}, databaseError(err)
	}
	raw, ok := body.([]byte)
	if !ok {
		return codec.Event{}, storeError("integrity_error", revision, "invalid boundary event body")
	}
	event, err := decodeEvent(raw, revision)
	if err != nil {
		return codec.Event{}, err
	}
	hash, err := event.ComputeHash()
	if err != nil {
		return codec.Event{}, fmt.Errorf("%w: %w", storeError("integrity_error", revision, "hash boundary event"), err)
	}
	if hash != event.Hash {
		return codec.Event{}, storeError("integrity_error", revision, "invalid boundary event hash")
	}
	return event, nil
}

func (s *Store) fold(tx *sql.Tx, revision *int64) (Snapshot, error) {
	state := Snapshot{0, codec.ZeroHash, projection.Empty()}
	if revision != nil && (*revision < 0 || *revision > maxWireInteger) {
		return Snapshot{}, storeError("bad_revision", *revision, "outside the wire range")
	}
	exists, err := identify(tx)
	if err != nil {
		return Snapshot{}, err
	}
	if !exists {
		return Snapshot{}, storeError("unavailable", 0, "uninitialized store")
	}
	head, hash, err := checkpoint(tx)
	if err != nil {
		return Snapshot{}, err
	}
	if revision != nil && *revision == 0 {
		return state, nil
	}
	rows, err := tx.Query("SELECT revision, body FROM events ORDER BY revision")
	if err != nil {
		return Snapshot{}, databaseError(err)
	}
	defer func() { _ = rows.Close() }()
	var batch []codec.Event
	for rows.Next() {
		next := state.Revision + int64(len(batch)) + 1
		event, err := scanEvent(rows, next)
		if err != nil {
			// A bad event in the buffered prefix takes precedence over this row.
			if at, issue := chainError(batch, state); issue != nil && at < next {
				return Snapshot{}, issue
			}
			return Snapshot{}, err
		}
		batch = append(batch, event)
		if int64(len(batch)) < batch[0].BatchSize {
			continue
		}
		if _, issue := chainError(batch, state); issue != nil {
			return Snapshot{}, issue
		}
		if head > state.Revision && head <= event.Revision && (head != event.Revision || hash != event.Hash) {
			return Snapshot{}, storeError("integrity_error", head, "head checkpoint disagrees with its batch")
		}
		state.Revision, state.HeadHash = event.Revision, event.Hash
		for _, e := range batch {
			state.Projection, err = s.apply(state.Projection, e)
			if err != nil {
				return Snapshot{}, err
			}
		}
		if revision != nil && *revision <= state.Revision {
			if *revision != state.Revision {
				return Snapshot{}, storeError("bad_revision", *revision, "inside a batch")
			}
			return state, nil
		}
		batch = batch[:0]
	}
	if err := rows.Err(); err != nil {
		return Snapshot{}, databaseError(err)
	}
	if _, issue := chainError(batch, state); issue != nil {
		return Snapshot{}, issue
	}
	if state.Revision != head || state.HeadHash != hash {
		return Snapshot{}, storeError("integrity_error", min(head, state.Revision)+1, "head differs from the log")
	}
	if revision != nil {
		return Snapshot{}, storeError("bad_revision", *revision, "beyond the head")
	}
	return state, verifyProjection(tx, state)
}

func checkpoint(tx *sql.Tx) (int64, string, error) {
	var revision int64
	var hash string
	err := tx.QueryRow(`SELECT head_revision, head_hash FROM meta WHERE id=1 AND (SELECT count(*) FROM meta)=1
		AND EXISTS (SELECT 1 FROM pragma_table_list('meta') WHERE schema='main' AND type='table' AND ncol=3 AND strict=1)`).Scan(&revision, &hash)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, "", storeError("integrity_error", 0, "missing head checkpoint")
	}
	if err != nil {
		return 0, "", databaseError(err)
	}
	if revision < 0 || revision > maxWireInteger || len(hash) != len(codec.ZeroHash) ||
		strings.Trim(hash, "0123456789abcdef") != "" || (revision == 0 && hash != codec.ZeroHash) {
		return 0, "", storeError("integrity_error", revision, "invalid head checkpoint")
	}
	return revision, hash, nil
}

func scanEvent(rows *sql.Rows, revision int64) (codec.Event, error) {
	var number int64
	var body any
	if err := rows.Scan(&number, &body); err != nil {
		return codec.Event{}, databaseError(err)
	}
	raw, ok := body.([]byte)
	if number != revision || !ok {
		return codec.Event{}, storeError("integrity_error", revision, "invalid event row or sequence")
	}
	return decodeEvent(raw, revision)
}

func chainError(batch []codec.Event, state Snapshot) (int64, error) {
	err := codec.VerifyChain(batch, state.HeadHash, state.Revision+1)
	if err == nil {
		return 0, nil
	}
	revision := err.(*codec.Error).Revision
	return revision, fmt.Errorf("%w: %w", storeError("integrity_error", revision, "verify event chain"), err)
}

func decodeEvent(raw []byte, revision int64) (codec.Event, error) {
	event, err := codec.ParseEvent(raw)
	if err != nil {
		return codec.Event{}, fmt.Errorf("%w: %w", storeError("integrity_error", revision, "decode event"), err)
	}
	canonical, err := codec.Encode(event.Map())
	if err != nil {
		return codec.Event{}, fmt.Errorf("%w: %w", storeError("integrity_error", revision, "encode event"), err)
	}
	if !bytes.Equal(raw, canonical) || event.Revision != revision || event.ActorID == "" || event.ProposalID == "" || event.Type == "" ||
		event.BatchSize < 1 || event.BatchStart < 1 || event.BatchSize > maxWireInteger-event.BatchStart+1 {
		return codec.Event{}, storeError("integrity_error", revision, "invalid or noncanonical event envelope")
	}
	if _, err := codec.ParseTime(event.At); err != nil {
		return codec.Event{}, fmt.Errorf("%w: %w", storeError("integrity_error", revision, "invalid event time"), err)
	}
	return event, nil
}

func (s *Store) apply(p projection.Projection, event codec.Event) (projection.Projection, error) {
	if event.V != 1 {
		return p, storeError("unsupported_version", event.Revision, fmt.Sprintf("wire v%d", event.V))
	}
	next, _, err := s.registry.Apply(p, event)
	return next, err
}

// RawEvents reads original canonical bytes in the inclusive revision range.
func (s *Store) RawEvents(from, to int64) ([][]byte, error) {
	rows, err := s.db.Query("SELECT body FROM events WHERE revision BETWEEN ? AND ? ORDER BY revision", from, to)
	if err != nil {
		return nil, databaseError(err)
	}
	defer func() { _ = rows.Close() }()
	var events [][]byte
	for rows.Next() {
		var raw []byte
		if err := rows.Scan(&raw); err != nil {
			return nil, databaseError(err)
		}
		events = append(events, raw)
	}
	return events, databaseError(rows.Err())
}

// Events decodes events in the inclusive revision range.
func (s *Store) Events(from, to int64) ([]codec.Event, error) {
	raw, err := s.RawEvents(from, to)
	if err != nil {
		return nil, err
	}
	events := make([]codec.Event, len(raw))
	for i, body := range raw {
		events[i], err = decodeEvent(body, max(1, from)+int64(i))
		if err != nil {
			return nil, err
		}
	}
	return events, nil
}
