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

func (table projectionTable) identity(row map[string]any) (rowIdentity, bool) {
	var key rowIdentity
	for i, column := range table.keys {
		value, ok := row[column].(string)
		if !ok {
			return rowIdentity{}, false
		}
		key[i] = value
	}
	return key, true
}

func (table projectionTable) encode(view map[string]any, revision int64) (map[rowIdentity][]byte, error) {
	rows := view[table.name].([]any)
	encoded := make(map[rowIdentity][]byte, len(rows))
	for _, item := range rows {
		row := item.(map[string]any)
		key, ok := table.identity(row)
		if !ok {
			return nil, storeError("integrity_error", revision, "invalid identity in "+table.name)
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

func readProjection(tx *sql.Tx, revision int64) (projection.Projection, error) {
	view := make(map[string]any, len(projectionTables))
	for _, table := range projectionTables {
		items := []any{}
		err := table.read(tx, revision, func(key rowIdentity, raw []byte) error {
			row, err := codec.DecodeObject(raw)
			if err != nil {
				return fmt.Errorf("%w: %w", storeError("integrity_error", revision, "decode "+table.name), err)
			}
			if identity, ok := table.identity(row); !ok || identity != key {
				return storeError("integrity_error", revision, "body and row key differ in "+table.name)
			}
			canonical, err := codec.Encode(row)
			if err != nil {
				return fmt.Errorf("%w: %w", storeError("integrity_error", revision, "encode "+table.name), err)
			}
			if !bytes.Equal(raw, canonical) {
				return storeError("integrity_error", revision, "noncanonical body in "+table.name)
			}
			items = append(items, row)
			return nil
		})
		if err != nil {
			return projection.Projection{}, err
		}
		view[table.name] = items
	}
	p, err := projection.FromMap(view)
	if err != nil {
		return projection.Projection{}, fmt.Errorf("%w: %w", storeError("integrity_error", revision, "parse materialized rows"), err)
	}
	return p, nil
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
	err := table.read(tx, revision, func(key rowIdentity, raw []byte) error {
		if !bytes.Equal(raw, expected[key]) {
			return storeError("integrity_error", revision, "unexpected materialized row in "+table.name)
		}
		delete(expected, key)
		return nil
	})
	if err != nil {
		return err
	}
	if len(expected) != 0 {
		return storeError("integrity_error", revision, "missing materialized row in "+table.name)
	}
	return nil
}

func (table projectionTable) read(tx *sql.Tx, revision int64, visit func(rowIdentity, []byte) error) error {
	rows, err := tx.Query("SELECT " + strings.Join(table.keys, ", ") + ", body FROM " + table.name)
	if err != nil {
		return databaseError(err)
	}
	defer func() { _ = rows.Close() }()
	var fields [3]any
	var body any
	columns := []any{&fields[0], &fields[1], &fields[2]}
	columns = append(columns[:len(table.keys)], &body)
	for rows.Next() {
		if err := rows.Scan(columns...); err != nil {
			return databaseError(err)
		}
		var key rowIdentity
		for i := range table.keys {
			value, ok := fields[i].(string)
			if !ok {
				return storeError("integrity_error", revision, "invalid row key in "+table.name)
			}
			key[i] = value
		}
		raw, ok := body.([]byte)
		if !ok || len(raw) == 0 {
			return storeError("integrity_error", revision, "invalid row body in "+table.name)
		}
		if err := visit(key, raw); err != nil {
			return err
		}
	}
	return databaseError(rows.Err())
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
