package gate

import (
	"github.com/lookmanrays/purser/internal/kernel/codec"
	"github.com/lookmanrays/purser/internal/kernel/projection"
)

func (a *admission) hold(t Tentative, row projection.Record, expiry string) Outcome {
	if codec.FormatTime(t.At) >= expiry {
		return reject("approval_expired")
	}
	id := "a:" + a.digest
	versions := []any{map[string]any{"key": row.ID, "changed": row.Changed}}
	return Outcome{Pending: &Pending{ApprovalID: id, Events: []codec.EventSpec{event("ApprovalRequested", map[string]any{
		"id": id, "requester": t.Binding.ActorID, "request": proposalMap(a.proposal),
		"request_digest": a.digest, "read_versions": versions, "expires_at": expiry,
	})}}}
}

func (a *admission) resolve(t Tentative, row projection.Record, op map[string]any) (Outcome, error) {
	fields := row.Fields
	if fields["request_digest"] != op["request_digest"] {
		return reject("digest_mismatch"), nil
	}
	if fields["state"] != "pending" {
		return reject("already_decided"), nil
	}
	integrity := &codec.Error{Code: "integrity_error", Revision: row.Changed, Detail: "invalid saved approval data"}
	request, _ := fields["request"].(map[string]any)
	id, idOK := request["proposal_id"].(string)
	actor, actorOK := fields["requester"].(string)
	expires, expiresOK := fields["expires_at"].(string)
	saved, versionsOK := fields["read_versions"].([]any)
	if !idOK || !actorOK || !expiresOK || !versionsOK {
		return Outcome{}, integrity
	}
	// These are the origin's declared versions. They govern its resolution
	// independently of the answer's base and its read of the approval record.
	versions := make(map[string]int64, len(saved))
	for _, v := range saved {
		read, _ := v.(map[string]any)
		key, keyOK := read["key"].(string)
		revision, revisionOK := read["changed"].(int64)
		if !keyOK || !revisionOK {
			return Outcome{}, integrity
		}
		versions[key] = revision
	}
	changed := conflicts(t.Projection, versions)
	state, code, disposition := "approved", "ok", "admitted"
	details := map[string]any{"approval_id": row.ID}
	switch {
	case codec.FormatTime(t.At) >= expires:
		state, code, disposition = "expired", "approval_expired", "rejected"
	case len(changed) != 0:
		state, code, disposition = "stale", "stale_base", "rejected"
		details = map[string]any{"conflicts": changed}
	case op["decision"] == "deny":
		state, code, disposition = "denied", "approval_denied", "rejected"
	}
	events := []codec.EventSpec{event("ApprovalResolved", map[string]any{"id": row.ID, "state": state})}
	if state == "approved" {
		events = append(events, event("CommitmentActivated", map[string]any{"id": row.Commitment}))
	}
	events = append(events, receiptEvent(actor, id, op["request_digest"].(string), disposition, code, details))
	a.details = map[string]any{"approval_id": row.ID, "state": state}
	return Outcome{Events: events}, nil
}
