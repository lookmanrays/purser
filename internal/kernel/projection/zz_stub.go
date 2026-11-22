package projection

// Stubs for P1-02a, from contracts/kernel/projection.yaml (parameter names
// blanked for the linter). The builder replaces this file with the implementation.

import "github.com/lookmanrays/purser/internal/kernel/codec"

var unimplemented = func() { panic("unimplemented: P1-02a") }

// Record is a projected entity record.
type Record struct {
	ID         string
	Kind       string
	Commitment string
	Changed    int64
	Fields     map[string]any
}

// Receipt is a projected proposal receipt.
type Receipt struct {
	ActorID     string
	ProposalID  string
	Digest      string
	Disposition string // admitted, pending or rejected
	Code        string
	Details     map[string]any
	Revision    int64
}

// Edge represents a directed relation between records.
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

// Empty returns an empty projection.
func Empty() Projection { unimplemented(); return Projection{} }

// Map converts the projection into its map representation for canonical serialization.
func (Projection) Map() map[string]any { unimplemented(); return nil }

// Checksum returns codec.Hash(TagProjection, p.Map()).
func (Projection) Checksum() (string, error) { unimplemented(); return "", nil }

// Record looks up a record by its ID.
func (Projection) Record(_ string) (Record, bool) { unimplemented(); return Record{}, false }

// ByKind returns all records of the given kind.
func (Projection) ByKind(_ string) []Record { unimplemented(); return nil }

// Receipt looks up a receipt by its (actorID, proposalID) key.
func (Projection) Receipt(_, _ string) (Receipt, bool) { unimplemented(); return Receipt{}, false }

// EdgesTo returns all edges of the given kind pointing to toID.
func (Projection) EdgesTo(_, _ string) []Edge { unimplemented(); return nil }

// FromMap reconstructs a Projection from its map representation.
func FromMap(_ map[string]any) (Projection, error) { unimplemented(); return Projection{}, nil }

// Changes reports what one event changed, so the store can update only those rows.
type Changes struct {
	Records  []string
	Receipts [][2]string
	Edges    []Edge
	Removed  []Edge
}

// Reducer is a pure transition function applying event data to a projection.
type Reducer func(p Projection, data map[string]any, revision, batchEnd int64) (Projection, Changes, error)

// Registry maps (event type, version) to pure reducers.
type Registry struct{}

// NewRegistry creates an empty registry.
func NewRegistry() *Registry { unimplemented(); return nil }

// Register registers a reducer for an event type and version.
func (*Registry) Register(_ string, _ int64, _ Reducer) error { unimplemented(); return nil }

// Apply executes the registered reducer for an event's type and version.
func (*Registry) Apply(_ Projection, _ codec.Event) (Projection, Changes, error) {
	unimplemented()
	return Projection{}, Changes{}, nil
}

// Known reports whether a reducer is registered for the event type and version.
func (*Registry) Known(_ string, _ int64) bool { unimplemented(); return false }

// Base returns a Registry pre-populated with M1 kernel event reducers.
func Base() *Registry { unimplemented(); return nil }
