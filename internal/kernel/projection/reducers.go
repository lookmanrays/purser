package projection

import (
	"fmt"
	"maps"
	"slices"

	"github.com/lookmanrays/purser/internal/kernel/codec"
)

func createCommitment(p Projection, data map[string]any, revision, _ int64) (Projection, Changes, error) {
	if !matches(data, map[string]any{"id": "", "root": "", "principal": "", "statement": ""}) {
		return p, Changes{}, invalid(revision, "invalid commitment fields")
	}
	id, root := data["id"].(string), data["root"].(string)
	_, existingID := p.Record(id)
	_, existingRoot := p.Record(root)
	if id == root || existingID || existingRoot {
		return p, Changes{}, invalid(revision, "duplicate commitment or root ID")
	}
	p.Records = append(slices.Clone(p.Records),
		Record{id, "Commitment", id, revision, map[string]any{"state": "proposed", "root": root, "principal": data["principal"], "statement": data["statement"]}},
		Record{root, "WorkItem", id, revision, map[string]any{"state": "open", "generation": int64(1), "parent": nil, "candidate": nil}})
	slices.SortFunc(p.Records, compareRecord)
	return p, Changes{Records: []string{id, root}}, nil
}

func updateRecord(kind, field, constant string) Reducer {
	shape := map[string]any{"id": ""}
	if constant == "" {
		shape[field] = ""
	}
	return func(p Projection, data map[string]any, revision, _ int64) (Projection, Changes, error) {
		if !matches(data, shape) {
			return p, Changes{}, invalid(revision, "invalid "+kind+" update fields")
		}
		value := constant
		if constant == "" {
			value = data[field].(string)
		}
		if kind == "Approval" && !slices.Contains([]string{"approved", "denied", "expired", "stale"}, value) {
			return p, Changes{}, invalid(revision, "invalid approval state")
		}
		id := data["id"].(string)
		i, found := slices.BinarySearchFunc(p.Records, Record{ID: id}, compareRecord)
		if !found || p.Records[i].Kind != kind {
			return p, Changes{}, invalid(revision, "missing "+kind+" "+id)
		}
		p.Records = slices.Clone(p.Records)
		p.Records[i].Fields = maps.Clone(p.Records[i].Fields)
		p.Records[i].Fields[field], p.Records[i].Changed = value, revision
		return p, Changes{Records: []string{id}}, nil
	}
}

func requestApproval(p Projection, data map[string]any, revision, _ int64) (Projection, Changes, error) {
	if !matches(data, map[string]any{"id": "", "requester": "", "request": map[string]any(nil), "request_digest": "", "read_versions": []any(nil), "expires_at": ""}) {
		return p, Changes{}, invalid(revision, "invalid approval fields")
	}
	id := data["id"].(string)
	commitment, ok := data["request"].(map[string]any)["commitment"].(string)
	parent, found := p.Record(commitment)
	_, exists := p.Record(id)
	if !ok || !found || parent.Kind != "Commitment" || exists {
		return p, Changes{}, invalid(revision, "invalid approval ID or commitment")
	}
	for _, read := range data["read_versions"].([]any) {
		fields, _ := read.(map[string]any)
		if !matches(fields, map[string]any{"key": "", "changed": int64(0)}) {
			return p, Changes{}, invalid(revision, "invalid read version")
		}
	}
	if _, err := codec.ParseTime(data["expires_at"].(string)); err != nil {
		return p, Changes{}, fmt.Errorf("%w: %w", invalid(revision, "invalid expiry"), err)
	}
	fields := maps.Clone(data)
	delete(fields, "id")
	fields["state"] = "pending"
	p.Records = append(slices.Clone(p.Records), Record{id, "Approval", commitment, revision, fields})
	slices.SortFunc(p.Records, compareRecord)
	return p, Changes{Records: []string{id}}, nil
}

func receiptFields(projected bool) map[string]any {
	shape := map[string]any{"actor_id": "", "proposal_id": "", "digest": "", "disposition": "", "code": "", "details": map[string]any(nil)}
	if projected {
		shape["revision"] = int64(0)
	}
	return shape
}

func readReceipt(r map[string]any, revision int64) Receipt {
	return Receipt{r["actor_id"].(string), r["proposal_id"].(string), r["digest"].(string), r["disposition"].(string),
		r["code"].(string), r["details"].(map[string]any), revision}
}

func recordReceipt(p Projection, data map[string]any, revision, batchEnd int64) (Projection, Changes, error) {
	if !matches(data, receiptFields(false)) {
		return p, Changes{}, invalid(revision, "invalid receipt fields")
	}
	r := readReceipt(data, batchEnd)
	i, exists := slices.BinarySearchFunc(p.Receipts, r, compareReceipt)
	if !slices.Contains([]string{"pending", "admitted", "rejected"}, r.Disposition) ||
		exists && (p.Receipts[i].Disposition != "pending" || p.Receipts[i].Digest != r.Digest || r.Disposition == "pending") {
		return p, Changes{}, invalid(revision, "invalid receipt disposition or transition")
	}
	p.Receipts = slices.Clone(p.Receipts)
	if exists {
		p.Receipts[i] = r
	} else {
		p.Receipts = slices.Insert(p.Receipts, i, r)
	}
	return p, Changes{Receipts: [][2]string{{r.ActorID, r.ProposalID}}}, nil
}
