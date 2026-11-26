package store

import "github.com/lookmanrays/purser/internal/kernel/projection"

// Registry returns the reducer registry configured by Open. Callers only read it.
func (s *Store) Registry() *projection.Registry { return s.registry }
