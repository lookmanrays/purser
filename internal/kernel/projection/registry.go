package projection

import (
	"fmt"
	"maps"

	"github.com/lookmanrays/purser/internal/kernel/codec"
)

// Changes reports what one event changed, so the store can update only those rows.
type Changes struct {
	Records  []string
	Receipts [][2]string
	Edges    []Edge
	Removed  []Edge
}

// Reducer is a pure transition over verified event data.
type Reducer func(p Projection, data map[string]any, revision, batchEnd int64) (Projection, Changes, error)

type eventKey struct {
	name    string
	version int64
}

// Registry owns the reducer for each event type and version.
type Registry struct{ reducers map[eventKey]Reducer }

// NewRegistry creates an empty registry.
func NewRegistry() *Registry { return &Registry{make(map[eventKey]Reducer)} }

// Register installs a reducer before the registry is put into use.
func (r *Registry) Register(eventType string, version int64, fn Reducer) error {
	if r.Known(eventType, version) {
		return &codec.Error{Code: "duplicate_reducer", Detail: eventType}
	}
	if fn == nil {
		return invalid(0, "nil reducer for "+eventType)
	}
	r.reducers[eventKey{eventType, version}] = fn
	return nil
}

// Known reports whether a reducer is registered for the event type and version.
func (r *Registry) Known(eventType string, version int64) bool {
	return r.reducers[eventKey{eventType, version}] != nil
}

// Apply interprets one verified event.
func (r *Registry) Apply(p Projection, e codec.Event) (Projection, Changes, error) {
	fn := r.reducers[eventKey{e.Type, e.EventVersion}]
	if fn == nil {
		return p, Changes{}, &codec.Error{Code: "unsupported_version", Revision: e.Revision, Detail: fmt.Sprintf("%s v%d", e.Type, e.EventVersion)}
	}
	data := e.Data
	if e.Type == "CommitmentCreated" && e.EventVersion == 0 {
		if !matches(data, map[string]any{"id": "", "root": "", "principal": "", "text": ""}) {
			return p, Changes{}, invalid(e.Revision, "invalid legacy commitment fields")
		}
		data = maps.Clone(data)
		data["statement"] = data["text"]
		delete(data, "text")
	}
	next, changes, err := fn(p, data, e.Revision, e.BatchStart+e.BatchSize-1)
	if err != nil {
		return p, Changes{}, err
	}
	return next, changes, nil
}

// Base registers the M1 event reducers.
func Base() *Registry {
	return &Registry{map[eventKey]Reducer{
		{"CommitmentCreated", 0}:   createCommitment,
		{"CommitmentCreated", 1}:   createCommitment,
		{"CommitmentEdited", 1}:    updateRecord("Commitment", "statement", ""),
		{"CommitmentActivated", 1}: updateRecord("Commitment", "state", "active"),
		{"ApprovalRequested", 1}:   requestApproval,
		{"ApprovalResolved", 1}:    updateRecord("Approval", "state", ""),
		{"ProposalReceipted", 1}:   recordReceipt,
	}}
}

func invalid(revision int64, detail string) error {
	return &codec.Error{Code: "integrity_error", Revision: revision, Detail: detail}
}
