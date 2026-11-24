package store

import (
	"database/sql"
	"maps"
	"slices"
	"sync"

	"github.com/lookmanrays/purser/internal/kernel/codec"
)

const historicalCacheLimit = 16

type historicalCache struct {
	mu      sync.Mutex
	entries map[int64]Snapshot
}

func (c *historicalCache) load(tx *sql.Tx, revision, head int64, headHash string) (Snapshot, bool) {
	c.mu.Lock()
	entry, ok := c.entries[revision]
	c.mu.Unlock()
	if !ok || (revision == head && entry.HeadHash != headHash) {
		return Snapshot{}, false
	}
	// A committed prefix is immutable. Check its boundary in the same SQLite
	// read transaction before reusing a result from a previous fold.
	event, err := scanBoundary(tx.QueryRow("SELECT revision, body FROM events WHERE revision=?", revision))
	if err != nil || event.Hash != entry.HeadHash {
		return Snapshot{}, false
	}
	return copyHistoricalSnapshot(entry), true
}

func (c *historicalCache) save(snap Snapshot) {
	// Only copy valid wire values. Encoding bounds excessive nesting and rejects
	// non-wire values returned by a custom reducer.
	if _, err := codec.Encode(snap.Projection.Map()); err != nil {
		return
	}
	entry := copyHistoricalSnapshot(snap)
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.entries == nil {
		c.entries = make(map[int64]Snapshot)
	}
	if _, exists := c.entries[snap.Revision]; !exists && len(c.entries) == historicalCacheLimit {
		clear(c.entries)
	}
	c.entries[snap.Revision] = entry
}

func copyHistoricalSnapshot(snap Snapshot) Snapshot {
	p := &snap.Projection
	p.Records = slices.Clone(p.Records)
	for i := range p.Records {
		p.Records[i].Fields = copyHistoricalObject(p.Records[i].Fields)
	}
	p.Receipts = slices.Clone(p.Receipts)
	for i := range p.Receipts {
		p.Receipts[i].Details = copyHistoricalObject(p.Receipts[i].Details)
	}
	p.Edges = slices.Clone(p.Edges)
	return snap
}

func copyHistoricalObject(object map[string]any) map[string]any {
	copied := maps.Clone(object)
	for key, value := range copied {
		copied[key] = copyHistoricalValue(value)
	}
	return copied
}

func copyHistoricalValue(value any) any {
	switch value := value.(type) {
	case map[string]any:
		return copyHistoricalObject(value)
	case []any:
		copied := slices.Clone(value)
		for i, item := range copied {
			copied[i] = copyHistoricalValue(item)
		}
		return copied
	default:
		return value
	}
}
