package gate

import (
	"errors"
	"fmt"
	"sync"
	"time"

	"github.com/lookmanrays/purser/internal/kernel/codec"
	"github.com/lookmanrays/purser/internal/kernel/projection"
	"github.com/lookmanrays/purser/internal/kernel/store"
)

// Gate coordinates admission within a Store write transaction.
type Gate struct {
	opts Options
	mu   sync.RWMutex
	ops  map[string]Operator
}

// New constructs a Gate over the configured Store.
func New(opts Options) *Gate {
	if opts.Clock == nil {
		opts.Clock = time.Now
	}
	return &Gate{opts: opts, ops: make(map[string]Operator)}
}

// Register installs an operator under its unique name.
func (g *Gate) Register(op Operator) error {
	if op == nil {
		return errors.New("nil operator")
	}
	name := op.Name()
	g.mu.Lock()
	defer g.mu.Unlock()
	if name == "" || g.ops[name] != nil {
		return fmt.Errorf("empty or already registered operator name %q", name)
	}
	g.ops[name] = op
	return nil
}

type admission struct {
	proposal Proposal
	digest   string
	details  map[string]any
}

const principal = "principal:local"

func authenticate(b Binding) error {
	if b.ActorID == "" || b.Role == "" || b.PrincipalID != principal ||
		(b.Role == "principal") != (b.ActorID == principal) ||
		b.Commitment != nil && !codec.ValidID(*b.Commitment) ||
		b.Assignment != nil && !codec.ValidID(*b.Assignment) ||
		len(b.Attachments) != 0 && b.Role != "launcher" ||
		len(b.Witness) != 0 && b.Role != "observer" && b.Role != "verifier" {
		return &codec.Error{Code: "unauthenticated", Detail: "invalid trusted binding"}
	}
	for _, proof := range b.Witness {
		if proof == nil || len(proof) > 8*1024*1024 {
			return &codec.Error{Code: "unauthenticated", Detail: "invalid witness bytes"}
		}
	}
	return nil
}

func inScope(b Binding, commitment string) bool {
	if b.Commitment == nil {
		return b.Role == "principal"
	}
	return *b.Commitment == commitment
}

// Admit returns a receipt only after the Store has committed its batch.
func (g *Gate) Admit(b Binding, raw []byte) (projection.Receipt, error) {
	if err := authenticate(b); err != nil {
		return projection.Receipt{}, err
	}
	p, err := ParseProposal(raw)
	if err != nil {
		return projection.Receipt{}, err
	}
	digest, err := RequestDigest(b, p)
	if err != nil {
		return projection.Receipt{}, err
	}
	if g.opts.Store == nil {
		return projection.Receipt{}, &codec.Error{Code: "unavailable", Detail: "no admission store"}
	}
	a := admission{proposal: p, digest: digest, details: map[string]any{}}
	snap, _, err := g.opts.Store.Transact(b.ActorID, p.ProposalID, g.opts.Clock, func(s store.Snapshot, at time.Time) ([]codec.EventSpec, error) {
		if old, found := s.Projection.Receipt(b.ActorID, p.ProposalID); found {
			switch {
			case old.Digest != digest:
				return nil, &codec.Error{Code: "id_conflict", Revision: old.Revision, Detail: "proposal ID already bound to a different digest"}
			case !inScope(b, p.Commitment) && old.Code != "forbidden":
				return nil, &codec.Error{Code: "unauthenticated", Detail: "binding cannot redeliver receipt outside its scope"}
			default:
				return nil, nil
			}
		}
		t := Tentative{s.Projection, s.Revision, at, b, p.Commitment, g.opts.Artifacts}
		out, err := g.evaluate(t, &a)
		if err != nil {
			return nil, err
		}
		events, disposition, code, details := out.Events, "admitted", "ok", a.details
		switch {
		case out.Rejection != nil:
			events, disposition, code, details = nil, "rejected", out.Rejection.Code, out.Rejection.Details
		case out.Pending != nil:
			events = append(events, out.Pending.Events...)
			disposition, code = "pending", "needs_principal"
			details = map[string]any{"approval_id": out.Pending.ApprovalID}
		}
		return append(events, receiptEvent(b.ActorID, p.ProposalID, digest, disposition, code, details)), nil
	})
	if err != nil {
		if e, ok := errors.AsType[*codec.Error](err); ok {
			switch e.Code {
			case "unavailable", "integrity_error", "id_conflict", "unauthenticated":
				return projection.Receipt{}, err
			}
		}
		return projection.Receipt{}, fmt.Errorf("%w: %w", &codec.Error{Code: "integrity_error", Detail: "admission transaction"}, err)
	}
	r, _ := snap.Projection.Receipt(b.ActorID, p.ProposalID)
	return r, nil
}

func event(kind string, data map[string]any) codec.EventSpec {
	return codec.EventSpec{Type: kind, EventVersion: 1, Data: data}
}

func receiptEvent(actor, id, digest, disposition, code string, details map[string]any) codec.EventSpec {
	if details == nil {
		details = map[string]any{}
	}
	return event("ProposalReceipted", map[string]any{
		"actor_id": actor, "proposal_id": id, "digest": digest,
		"disposition": disposition, "code": code, "details": details,
	})
}

func reject(code string) Outcome { return Outcome{Rejection: &Rejection{Code: code}} }

func (g *Gate) evaluate(t Tentative, a *admission) (Outcome, error) {
	p := a.proposal
	switch {
	case p.BaseRevision < 0 || p.BaseRevision > t.Revision:
		return reject("bad_revision"), nil
	case p.BaseRevision != 0 && p.BaseRevision != t.Revision:
		if _, err := g.opts.Store.Snapshot(&p.BaseRevision); err != nil {
			if e, ok := errors.AsType[*codec.Error](err); ok && e.Code == "bad_revision" {
				return reject("bad_revision"), nil
			}
			return Outcome{}, err
		}
	}
	if !inScope(t.Binding, t.Commitment) {
		return reject("forbidden"), nil
	}
	reads := ReadSet{base: p.BaseRevision}
	ops := make([]Operator, len(p.Ops))
	hasCustom := false
	for i, entry := range p.Ops {
		op, _ := entry.(map[string]any)
		name, ok := op["op"].(string)
		if !ok {
			return reject("invalid_op"), nil
		}
		g.mu.RLock()
		impl := g.ops[name]
		g.mu.RUnlock()
		if impl == nil {
			return reject("unknown_op"), nil
		}
		if m, ok := impl.(m1Operator); ok {
			if code := m.validate(t, op, len(p.Ops), &reads); code != "" {
				return reject(code), nil
			}
			m.request = a
			impl = m
		} else {
			hasCustom = true
		}
		ops[i] = impl
	}

	start := t.Projection
	maskForeign := t.Binding.Role != "principal" || ops[0].Name() == answerApproval
	if !hasCustom {
		if rejection := reads.rejection(start, t.Commitment, maskForeign); rejection != nil {
			return Outcome{Rejection: rejection}, nil
		}
	}
	var result Outcome
	var failure error
	for i, impl := range ops {
		// Custom Apply declares its reads and returns its syntax and authorization
		// decision together. Inspect those before making any lifecycle result durable.
		out, err := impl.Apply(t, p.Ops[i].(map[string]any), &reads)
		if out.Rejection != nil {
			switch out.Rejection.Code {
			case "invalid_op", "forbidden", "unknown_op":
				return out, nil
			}
		}
		if result.Rejection != nil || failure != nil {
			continue
		}
		if err != nil || out.Rejection != nil {
			result, failure = out, err
			continue
		}
		if out.Pending != nil {
			if len(ops) != 1 {
				return reject("invalid_op"), nil
			}
			result.Pending = out.Pending
		}
		result.Events = append(result.Events, out.Events...)
		if i+1 < len(ops) {
			next := t
			failure = g.preview(&next, out.Events)
			if failure == nil {
				t = next
			}
		}
	}
	if hasCustom {
		if rejection := reads.rejection(start, t.Commitment, maskForeign); rejection != nil {
			return Outcome{Rejection: rejection}, nil
		}
	}
	return result, failure
}

func (g *Gate) preview(t *Tentative, events []codec.EventSpec) error {
	for _, spec := range events {
		t.Revision++
		raw, err := codec.Encode(spec.Data)
		if err != nil {
			return fmt.Errorf("%w: %w", &codec.Error{Code: "integrity_error", Revision: t.Revision, Detail: "encode tentative event data"}, err)
		}
		data, err := codec.DecodeObject(raw)
		if err != nil {
			return err
		}
		e := codec.Event{Type: spec.Type, EventVersion: spec.EventVersion, Data: data,
			Revision: t.Revision, BatchStart: t.Revision, BatchSize: 1}
		t.Projection, _, err = g.opts.Store.Registry().Apply(t.Projection, e)
		if err != nil {
			return err
		}
	}
	return nil
}
