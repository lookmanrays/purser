package gate

import (
	"maps"
	"slices"

	"github.com/lookmanrays/purser/internal/kernel/projection"
)

// ReadSet tracks the committed versions an operator's reads must agree with.
type ReadSet struct {
	base     int64
	versions map[string]int64
}

// Record declares a dependency on a record.
func (r *ReadSet) Record(id string) {
	if r.versions == nil {
		r.versions = make(map[string]int64)
	}
	r.versions[id] = r.base
}

// Absent declares a dependency on an ID still being unreserved.
func (r *ReadSet) Absent(id string) { r.Record(id) }

func conflicts(p projection.Projection, versions map[string]int64) []any {
	var changed []any
	for _, id := range slices.Sorted(maps.Keys(versions)) {
		if row, ok := p.Record(id); ok && row.Changed > versions[id] {
			changed = append(changed, map[string]any{"key": id, "changed": row.Changed})
		}
	}
	return changed
}

func (r *ReadSet) rejection(p projection.Projection, commitment string, maskForeign bool) *Rejection {
	if maskForeign {
		for id := range r.versions {
			if row, ok := p.Record(id); ok && row.Commitment != commitment {
				return &Rejection{Code: "not_found"}
			}
		}
	}
	if changed := conflicts(p, r.versions); len(changed) != 0 {
		return &Rejection{Code: "stale_base", Details: map[string]any{"conflicts": changed}}
	}
	return nil
}
