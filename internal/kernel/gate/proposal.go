package gate

import (
	"fmt"

	"github.com/lookmanrays/purser/internal/kernel/codec"
)

// ParseProposal validates the envelope and preserves each original operation value.
func ParseProposal(raw []byte) (Proposal, error) {
	fields, err := codec.DecodeObject(raw)
	if err != nil {
		return Proposal{}, fmt.Errorf("%w: %w", &codec.Error{Code: "invalid_input", Detail: "decode proposal"}, err)
	}
	version, _ := fields["v"].(int64)
	id, _ := fields["proposal_id"].(string)
	commitment, _ := fields["commitment"].(string)
	base, validBase := fields["base_revision"].(int64)
	ops, _ := fields["ops"].([]any)
	refs, validRefs := fields["justification"].([]any)
	if len(fields) != 6 || version != 1 || !codec.ValidID(id) || !codec.ValidID(commitment) ||
		!validBase || len(ops) == 0 || !validRefs {
		return Proposal{}, &codec.Error{Code: "invalid_input", Detail: "invalid proposal envelope fields"}
	}
	justification := make([]string, len(refs))
	for i, ref := range refs {
		s, ok := ref.(string)
		if !ok || s == "" {
			return Proposal{}, &codec.Error{Code: "invalid_input", Detail: fmt.Sprintf("invalid justification entry %d", i)}
		}
		justification[i] = s
	}
	return Proposal{version, id, commitment, base, ops, justification}, nil
}

// RequestDigest hashes the canonical proposal and trusted actor identity.
func RequestDigest(b Binding, p Proposal) (string, error) {
	digest, err := codec.Hash(codec.TagProposal, map[string]any{"actor_id": b.ActorID, "proposal": proposalMap(p)})
	if err != nil {
		return "", fmt.Errorf("%w: %w", &codec.Error{Code: "invalid_input", Detail: "hash proposal"}, err)
	}
	return digest, nil
}

func proposalMap(p Proposal) map[string]any {
	refs := make([]any, len(p.Justification))
	for i, ref := range p.Justification {
		refs[i] = ref
	}
	return map[string]any{
		"v": p.V, "proposal_id": p.ProposalID, "commitment": p.Commitment,
		"base_revision": p.BaseRevision, "ops": p.Ops, "justification": refs,
	}
}
