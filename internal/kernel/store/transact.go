package store

import (
	"database/sql"
	"fmt"

	"github.com/lookmanrays/purser/internal/kernel/codec"
	"github.com/lookmanrays/purser/internal/kernel/projection"
)

// Transact decides and commits a single atomic batch under SQLite write ownership.
func (s *Store) Transact(actorID, proposalID string, clock Clock, decide Decide) (Snapshot, []codec.Event, error) {
	if actorID == "" || !codec.ValidID(proposalID) || clock == nil || decide == nil {
		return Snapshot{}, nil, storeError("integrity_error", 0, "invalid transaction arguments")
	}
	tx, err := s.begin(false)
	if err != nil {
		return Snapshot{}, nil, err
	}
	defer func() { _ = tx.Rollback() }()
	snap, err := headSnapshot(tx)
	if err != nil {
		return Snapshot{}, nil, err
	}
	// Detach the callback's copy from the state used to apply and persist events.
	raw, err := codec.Encode(snap.Projection.Map())
	if err != nil {
		return Snapshot{}, nil, err
	}
	object, err := codec.DecodeObject(raw)
	if err != nil {
		return Snapshot{}, nil, err
	}
	copyProjection, err := projection.FromMap(object)
	if err != nil {
		return Snapshot{}, nil, err
	}
	// decide sees the instant the events will record, so a decision at the edge
	// of an expiry agrees with what replay shows.
	at, err := codec.ParseTime(codec.FormatTime(clock()))
	if err != nil {
		return Snapshot{}, nil, err
	}
	specs, err := decide(Snapshot{snap.Revision, snap.HeadHash, copyProjection}, at)
	if err != nil {
		return Snapshot{}, nil, err
	}
	if len(specs) == 0 {
		return snap, nil, nil
	}
	events, err := s.append(tx, &snap, actorID, proposalID, codec.FormatTime(at), specs)
	if err != nil {
		return Snapshot{}, nil, err
	}
	if err := tx.Commit(); err != nil {
		return Snapshot{}, nil, databaseError(err)
	}
	return snap, events, nil
}

func (s *Store) append(tx *sql.Tx, snap *Snapshot, actorID, proposalID, at string, specs []codec.EventSpec) ([]codec.Event, error) {
	start, size := snap.Revision+1, int64(len(specs))
	if size > maxWireInteger-start+1 {
		return nil, storeError("integrity_error", start, "batch exceeds the wire revision range")
	}
	events := make([]codec.Event, size)
	changes := batchChanges{
		records:  make(map[string]struct{}),
		receipts: make(map[[2]string]struct{}),
		edges:    make(map[rowIdentity]struct{}),
	}
	for i, spec := range specs {
		e := codec.Event{V: 1, EventVersion: spec.EventVersion, Revision: start + int64(i), BatchStart: start,
			BatchSize: size, ActorID: actorID, ProposalID: proposalID, At: at, Type: spec.Type, Data: spec.Data, PrevHash: snap.HeadHash}
		var err error
		if e.Hash, err = e.ComputeHash(); err != nil {
			return nil, fmt.Errorf("%w: %w", storeError("integrity_error", e.Revision, "hash event"), err)
		}
		raw, err := codec.Encode(e.Map())
		if err != nil {
			return nil, fmt.Errorf("%w: %w", storeError("integrity_error", e.Revision, "encode event"), err)
		}
		e, err = decodeEvent(raw, e.Revision)
		if err != nil {
			return nil, err
		}
		if _, err := tx.Exec("INSERT INTO events VALUES (?, ?)", e.Revision, raw); err != nil {
			return nil, databaseError(err)
		}
		next, delta, err := s.apply(snap.Projection, e)
		if err != nil {
			return nil, err
		}
		changes.add(delta)
		snap.Projection = next
		events[i], snap.Revision, snap.HeadHash = e, e.Revision, e.Hash
	}
	if err := changes.publish(tx, *snap); err != nil {
		return nil, err
	}
	return events, nil
}
