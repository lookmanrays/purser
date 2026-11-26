package store

import "github.com/lookmanrays/purser/internal/kernel/projection"

var unimplemented = func() { panic("unimplemented: P1-03") }

// Registry returns the reducer registry Open received, or projection.Base() when nil.
func (s *Store) Registry() *projection.Registry { unimplemented(); return nil }
