package store

import (
	"database/sql"
	"sync"

	"github.com/lookmanrays/purser/internal/kernel/codec"
	"github.com/lookmanrays/purser/internal/kernel/projection"
)

const historicalCacheLimit = 16

type historicalCache struct {
	mu      sync.Mutex
	entries map[int64]historicalEntry
}

type historicalEntry struct {
	hash       string
	projection []byte
}

func (c *historicalCache) load(tx *sql.Tx, revision, head int64, headHash string) (Snapshot, bool) {
	c.mu.Lock()
	entry, ok := c.entries[revision]
	c.mu.Unlock()
	if !ok || (revision == head && entry.hash != headHash) {
		return Snapshot{}, false
	}
	// A committed prefix is immutable. Check its boundary in the same SQLite
	// read transaction before reusing a result from a previous fold.
	event, err := scanBoundary(tx.QueryRow("SELECT revision, body FROM events WHERE revision=?", revision))
	if err != nil || event.Hash != entry.hash {
		return Snapshot{}, false
	}
	object, err := codec.DecodeObject(entry.projection)
	if err != nil {
		return Snapshot{}, false
	}
	p, err := projection.FromMap(object)
	if err != nil {
		return Snapshot{}, false
	}
	return Snapshot{revision, entry.hash, p}, true
}

func (c *historicalCache) save(snap Snapshot) {
	// Store bytes so snapshots never share caller-mutable slices or maps.
	raw, err := codec.Encode(snap.Projection.Map())
	if err != nil {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.entries == nil {
		c.entries = make(map[int64]historicalEntry)
	}
	if _, exists := c.entries[snap.Revision]; !exists && len(c.entries) == historicalCacheLimit {
		clear(c.entries)
	}
	c.entries[snap.Revision] = historicalEntry{snap.HeadHash, raw}
}
