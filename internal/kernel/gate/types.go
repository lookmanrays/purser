package gate

import (
	"time"

	"github.com/lookmanrays/purser/internal/kernel/codec"
	"github.com/lookmanrays/purser/internal/kernel/projection"
	"github.com/lookmanrays/purser/internal/kernel/store"
)

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

// Options supplies dependencies for Gate construction.
type Options struct {
	Store     *store.Store
	Artifacts ArtifactStore
	Clock     store.Clock
}
