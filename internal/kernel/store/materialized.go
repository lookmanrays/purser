package store

import (
	"bytes"
	"database/sql"
	"fmt"
	"strings"

	"github.com/lookmanrays/purser/internal/kernel/codec"
	"github.com/lookmanrays/purser/internal/kernel/projection"
)

type projectionTable struct {
	name string
	keys []string
}

var projectionTables = [...]projectionTable{
	{"records", []string{"id"}},
	{"receipts", []string{"actor_id", "proposal_id"}},
	{"edges", []string{"kind", "from_id", "to_id"}},
}

type rowIdentity [3]string

func (table projectionTable) encode(view map[string]any, revision int64) (map[rowIdentity][]byte, error) {
	rows := view[table.name].([]any)
	encoded := make(map[rowIdentity][]byte, len(rows))
	for _, item := range rows {
		row := item.(map[string]any)
		var key rowIdentity
		for i, column := range table.keys {
			key[i] = row[column].(string)
		}
		body, err := codec.Encode(row)
		if err != nil {
			return nil, fmt.Errorf("%w: %w", storeError("integrity_error", revision, "encode "+table.name), err)
		}
		if _, exists := encoded[key]; exists {
			return nil, storeError("integrity_error", revision, "duplicate identity in "+table.name)
		}
		encoded[key] = body
	}
	return encoded, nil
}

func verifyProjection(tx *sql.Tx, snap Snapshot) error {
	view := snap.Projection.Map()
	for _, table := range projectionTables {
		expected, err := table.encode(view, snap.Revision)
		if err != nil {
			return err
		}
		if err := table.verify(tx, expected, snap.Revision); err != nil {
			return err
		}
	}
	return nil
}

func (table projectionTable) verify(tx *sql.Tx, expected map[rowIdentity][]byte, revision int64) error {
	rows, err := tx.Query("SELECT " + strings.Join(table.keys, ", ") + ", body FROM " + table.name)
	if err != nil {
		return databaseError(err)
	}
	defer func() { _ = rows.Close() }()
	var key rowIdentity
	var body any
	columns := []any{&key[0], &key[1], &key[2]}
	columns = append(columns[:len(table.keys)], &body)
	for rows.Next() {
		if err := rows.Scan(columns...); err != nil {
			return databaseError(err)
		}
		raw, ok := body.([]byte)
		if !ok || len(raw) == 0 || !bytes.Equal(raw, expected[key]) {
			return storeError("integrity_error", revision, "unexpected materialized row in "+table.name)
		}
		delete(expected, key)
	}
	if err := rows.Err(); err != nil {
		return databaseError(err)
	}
	if len(expected) != 0 {
		return storeError("integrity_error", revision, "missing materialized row in "+table.name)
	}
	return nil
}

func publish(tx *sql.Tx, previous projection.Projection, snap Snapshot) error {
	before, after := previous.Map(), snap.Projection.Map()
	for _, table := range projectionTables {
		old, err := table.encode(before, snap.Revision)
		if err != nil {
			return err
		}
		next, err := table.encode(after, snap.Revision)
		if err != nil {
			return err
		}
		if err := table.write(tx, old, next); err != nil {
			return err
		}
	}
	_, err := tx.Exec("UPDATE meta SET head_revision=?, head_hash=? WHERE id=1", snap.Revision, snap.HeadHash)
	return databaseError(err)
}

func (table projectionTable) write(tx *sql.Tx, old, next map[rowIdentity][]byte) error {
	args := make([]any, len(table.keys)+1)
	remove := "DELETE FROM " + table.name + " WHERE " + strings.Join(table.keys, "=? AND ") + "=?"
	upsert := "INSERT INTO " + table.name + " VALUES (" + strings.Repeat("?,", len(table.keys)) +
		"?) ON CONFLICT DO UPDATE SET body=excluded.body"
	for key := range old {
		if _, remains := next[key]; !remains {
			for i := range table.keys {
				args[i] = key[i]
			}
			if _, err := tx.Exec(remove, args[:len(table.keys)]...); err != nil {
				return databaseError(err)
			}
		}
	}
	for key, body := range next {
		if bytes.Equal(body, old[key]) {
			continue
		}
		for i := range table.keys {
			args[i] = key[i]
		}
		args[len(table.keys)] = body
		if _, err := tx.Exec(upsert, args...); err != nil {
			return databaseError(err)
		}
	}
	return nil
}
