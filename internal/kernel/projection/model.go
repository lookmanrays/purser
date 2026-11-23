package projection

import (
	"cmp"
	"reflect"
	"slices"

	"github.com/lookmanrays/purser/internal/kernel/codec"
)

// Record is a projected entity.
type Record struct {
	ID         string
	Kind       string
	Commitment string
	Changed    int64
	Fields     map[string]any
}

// Receipt is the projected result of a proposal.
type Receipt struct {
	ActorID     string
	ProposalID  string
	Digest      string
	Disposition string // admitted, pending or rejected
	Code        string
	Details     map[string]any
	Revision    int64
}

// Edge shape is {"kind","from_id","to_id","changed"} (kernel-v1.md).
// Changed is the last revision that changed the edge.
type Edge struct {
	Kind    string
	FromID  string
	ToID    string
	Changed int64
}

// Projection is immutable from the caller's view: Apply returns a new value.
type Projection struct {
	Records  []Record  // sorted by ID
	Receipts []Receipt // sorted by (ActorID, ProposalID)
	Edges    []Edge    // sorted by (Kind, FromID, ToID)
}

// Empty returns the initial projection.
func Empty() Projection { return Projection{[]Record{}, []Receipt{}, []Edge{}} }

// Map returns the projection's canonical wire shape.
func (p Projection) Map() map[string]any {
	records, receipts, edges := make([]any, len(p.Records)), make([]any, len(p.Receipts)), make([]any, len(p.Edges))
	for i, r := range p.Records {
		records[i] = map[string]any{"id": r.ID, "kind": r.Kind, "commitment": r.Commitment, "changed": r.Changed, "fields": r.Fields}
	}
	for i, r := range p.Receipts {
		receipts[i] = map[string]any{"actor_id": r.ActorID, "proposal_id": r.ProposalID, "digest": r.Digest,
			"disposition": r.Disposition, "code": r.Code, "details": r.Details, "revision": r.Revision}
	}
	for i, e := range p.Edges {
		edges[i] = map[string]any{"kind": e.Kind, "from_id": e.FromID, "to_id": e.ToID, "changed": e.Changed}
	}
	return map[string]any{"records": records, "receipts": receipts, "edges": edges}
}

// Checksum hashes the canonical projection.
func (p Projection) Checksum() (string, error) { return codec.Hash(codec.TagProjection, p.Map()) }

// Record finds a record by ID.
func (p Projection) Record(id string) (Record, bool) {
	i, ok := slices.BinarySearchFunc(p.Records, Record{ID: id}, compareRecord)
	return at(p.Records, i, ok)
}

// ByKind returns records of the given kind in ID order.
func (p Projection) ByKind(kind string) []Record {
	return slices.DeleteFunc(slices.Clone(p.Records), func(r Record) bool { return r.Kind != kind })
}

// Receipt finds a receipt by actor and proposal ID.
func (p Projection) Receipt(actorID, proposalID string) (Receipt, bool) {
	i, ok := slices.BinarySearchFunc(p.Receipts, Receipt{ActorID: actorID, ProposalID: proposalID}, compareReceipt)
	return at(p.Receipts, i, ok)
}

// EdgesTo returns edges of the given kind ending at toID.
func (p Projection) EdgesTo(kind, toID string) []Edge {
	return slices.DeleteFunc(slices.Clone(p.Edges), func(e Edge) bool { return e.Kind != kind || e.ToID != toID })
}

func at[T any](items []T, i int, ok bool) (T, bool) {
	if ok {
		return items[i], true
	}
	var zero T
	return zero, false
}

func compareRecord(a, b Record) int { return cmp.Compare(a.ID, b.ID) }
func compareReceipt(a, b Receipt) int {
	return cmp.Or(cmp.Compare(a.ActorID, b.ActorID), cmp.Compare(a.ProposalID, b.ProposalID))
}
func compareEdge(a, b Edge) int {
	return cmp.Or(cmp.Compare(a.Kind, b.Kind), cmp.Compare(a.FromID, b.FromID), cmp.Compare(a.ToID, b.ToID))
}

// FromMap parses the canonical wire shape and sorts each collection.
func FromMap(m map[string]any) (Projection, error) {
	if !matches(m, map[string]any{"records": []any(nil), "receipts": []any(nil), "edges": []any(nil)}) {
		return Projection{}, invalid(0, "invalid projection collections")
	}
	records, err := readRows(m["records"].([]any), compareRecord, map[string]any{
		"id": "", "kind": "", "commitment": "", "changed": int64(0), "fields": map[string]any(nil),
	}, func(r map[string]any) Record {
		return Record{r["id"].(string), r["kind"].(string), r["commitment"].(string), r["changed"].(int64), r["fields"].(map[string]any)}
	})
	if err != nil {
		return Projection{}, err
	}
	receipts, err := readRows(m["receipts"].([]any), compareReceipt, receiptFields(true), func(r map[string]any) Receipt {
		return readReceipt(r, r["revision"].(int64))
	})
	if err != nil {
		return Projection{}, err
	}
	edges, err := readRows(m["edges"].([]any), compareEdge, map[string]any{"kind": "", "from_id": "", "to_id": "", "changed": int64(0)}, func(e map[string]any) Edge {
		return Edge{e["kind"].(string), e["from_id"].(string), e["to_id"].(string), e["changed"].(int64)}
	})
	return Projection{records, receipts, edges}, err
}

func readRows[T any](items []any, compare func(T, T) int, shape map[string]any, parse func(map[string]any) T) ([]T, error) {
	rows := make([]T, len(items))
	for i, item := range items {
		row, _ := item.(map[string]any)
		if !matches(row, shape) {
			return nil, invalid(0, "invalid projection row")
		}
		rows[i] = parse(row)
	}
	slices.SortFunc(rows, compare)
	for i := 1; i < len(rows); i++ {
		if compare(rows[i-1], rows[i]) == 0 {
			return nil, invalid(0, "duplicate projection identity")
		}
	}
	return rows, nil
}

func matches(data, shape map[string]any) bool {
	if len(data) != len(shape) {
		return false
	}
	for key, example := range shape {
		if reflect.TypeOf(data[key]) != reflect.TypeOf(example) {
			return false
		}
	}
	return true
}
