package gate

import (
	"strings"
	"unicode"

	"github.com/lookmanrays/purser/internal/kernel/codec"
)

const (
	createCommitment  = "commitment.create"
	editCommitment    = "commitment.edit"
	requestAcceptance = "commitment.request_acceptance"
	answerApproval    = "approval.answer"
)

type m1Operator struct {
	name    string
	request *admission
}

func (m m1Operator) Name() string { return m.name }

// RegisterM1 installs the commitment and approval operators.
func RegisterM1(g *Gate) error {
	for _, name := range []string{createCommitment, editCommitment, requestAcceptance, answerApproval} {
		if err := g.Register(m1Operator{name: name}); err != nil {
			return err
		}
	}
	return nil
}

// validate checks M1 syntax and authority before any tentative transition.
func (m m1Operator) validate(t Tentative, op map[string]any, count int, reads *ReadSet) string {
	id, _ := op["id"].(string)
	statement, _ := op["statement"].(string)
	root, _ := op["root"].(string)
	expires, _ := op["expires_at"].(string)
	digest, _ := op["request_digest"].(string)
	decision, _ := op["decision"].(string)
	answer := m.name == answerApproval
	switch {
	case answer:
		if len(op) != 4 || !codec.ValidApprovalID(id) || !codec.ValidApprovalID("a:"+digest) ||
			(decision != "approve" && decision != "deny") || count != 1 {
			return "invalid_op"
		}
	case !codec.ValidID(id):
		return "invalid_op"
	case m.name == requestAcceptance:
		if _, err := codec.ParseTime(expires); err != nil || len(op) != 3 || count != 1 {
			return "invalid_op"
		}
	default:
		if strings.TrimFunc(statement, referenceSpace) == "" {
			return "invalid_op"
		}
		if m.name == createCommitment {
			if len(op) != 4 || root == id || !codec.ValidID(root) {
				return "invalid_op"
			}
		} else if len(op) != 3 {
			return "invalid_op"
		}
	}
	b := t.Binding
	if answer {
		if b.Role != "principal" || b.Assignment != nil {
			return "forbidden"
		}
	} else if id != t.Commitment || (b.Role != "principal" && b.Role != "intake") {
		return "forbidden"
	}
	if m.name == createCommitment {
		reads.Absent(id)
		reads.Absent(root)
	} else {
		reads.Record(id)
	}
	return ""
}

// Python's str.strip also removes the four ASCII information separators.
func referenceSpace(r rune) bool { return unicode.IsSpace(r) || r >= '\x1c' && r <= '\x1f' }

func (m m1Operator) Apply(t Tentative, op map[string]any, _ *ReadSet) (Outcome, error) {
	id := op["id"].(string)
	row, found := t.Projection.Record(id)
	if m.name == createCommitment {
		root := op["root"].(string)
		_, rootExists := t.Projection.Record(root)
		if found || rootExists {
			return reject("id_exists"), nil
		}
		return Outcome{Events: []codec.EventSpec{event("CommitmentCreated", map[string]any{
			"id": id, "root": root, "statement": op["statement"], "principal": t.Binding.PrincipalID,
		})}}, nil
	}
	if !found || row.Commitment != t.Commitment {
		return reject("not_found"), nil
	}
	if m.name == answerApproval {
		if row.Kind != "Approval" {
			return reject("not_found"), nil
		}
		return m.request.resolve(t, row, op)
	}
	if row.Kind != "Commitment" || row.Fields["state"] != "proposed" {
		return reject("invalid_transition"), nil
	}
	if m.name == requestAcceptance {
		return m.request.hold(t, row, op["expires_at"].(string)), nil
	}
	return Outcome{Events: []codec.EventSpec{event("CommitmentEdited", map[string]any{
		"id": id, "statement": op["statement"],
	})}}, nil
}
