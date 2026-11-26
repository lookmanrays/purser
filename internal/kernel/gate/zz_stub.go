package gate

import (
	"time"

	"github.com/lookmanrays/purser/internal/kernel/codec"
	"github.com/lookmanrays/purser/internal/kernel/projection"
	"github.com/lookmanrays/purser/internal/kernel/store"
)

var unimplemented = func() { panic("unimplemented: P1-03") }

// Binding comes only from trusted ingress, never from proposal JSON.
type Binding struct {
	ActorID     string
	Role        string // principal, planner, executor, observer, verifier, launcher
	PrincipalID string
	Commitment  *string
	Assignment  *string
	Witness     map[string][]byte // observer and verifier witness bytes, by name
	Attachments map[string]any    // launcher-only trusted attachments
}

// Proposal is the envelope for operations to be admitted.
type Proposal struct {
	V             int64
	ProposalID    string
	Commitment    string
	BaseRevision  int64
	Ops           []any // each entry exactly as decoded; a non-object entry is rejected with invalid_op
	Justification []string
}

// ParseProposal parses raw proposal bytes.
func ParseProposal(_ []byte) (Proposal, error) { unimplemented(); return Proposal{}, nil }

// RequestDigest computes the tagged proposal digest for a binding and proposal.
func RequestDigest(_ Binding, _ Proposal) (string, error) { unimplemented(); return "", nil }

// ReadSet records what an operator read, so the gate can detect stale bases.
// Collection reads (a set's insertions and deletions) arrive with P1-07.
type ReadSet struct{}

// Record records a dependency on an existing record.
func (r *ReadSet) Record(_ string) { unimplemented() }

// Absent records a dependency on the non-existence of a record.
func (r *ReadSet) Absent(_ string) { unimplemented() }

// ArtifactStore is the content-addressed byte store operators use to stage witness bytes.
type ArtifactStore interface {
	Put(data []byte) (digest string, err error)
	Get(digest string) ([]byte, error)
	Has(digest string) (bool, error)
}

// Tentative is the state an operator sees: the committed snapshot plus
// the events earlier ops in the same proposal produced.
type Tentative struct {
	Projection projection.Projection
	Revision   int64
	At         time.Time
	Binding    Binding
	Commitment string
	Artifacts  ArtifactStore
}

// Rejection describes a semantic refusal with code and details.
type Rejection struct {
	Code    string
	Details map[string]any
}

// Pending describes a held approval outcome.
type Pending struct {
	ApprovalID string
	Events     []codec.EventSpec
}

// Outcome holds the result of an operator application.
type Outcome struct {
	Events    []codec.EventSpec
	Rejection *Rejection
	Pending   *Pending
}

// Operator is implemented by kernel admission operators.
type Operator interface {
	Name() string
	Apply(t Tentative, op map[string]any, reads *ReadSet) (Outcome, error)
}

// Gate coordinates admission into the kernel event log.
type Gate struct{}

// Options supplies dependencies for Gate construction.
type Options struct {
	Store     *store.Store
	Artifacts ArtifactStore
	Clock     store.Clock
}

// New constructs a Gate with the given options.
func New(_ Options) *Gate { unimplemented(); return nil }

// Register adds an operator to the Gate's registry.
func (g *Gate) Register(_ Operator) error { unimplemented(); return nil }

// Admit evaluates a proposal envelope and operations against tentative state and appends results.
func (g *Gate) Admit(_ Binding, _ []byte) (projection.Receipt, error) {
	unimplemented()
	return projection.Receipt{}, nil
}

// RegisterM1 registers the M1 operator set onto Gate.
func RegisterM1(_ *Gate) error { unimplemented(); return nil }
