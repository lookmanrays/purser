package schema_test

import (
	"encoding/json"
	"errors"
	"maps"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/lookmanrays/purser/internal/schema"
	"github.com/lookmanrays/purser/internal/testvectors"
	"gopkg.in/yaml.v3"
)

func workSpec() map[string]any {
	return map[string]any{
		"kind": "feature", "implements": "graph.store@v1", "tier": "implementer",
		"scope": map[string]any{"write": []any{"internal/graph/**"}, "read": []any{"contracts/graph/**"}},
		"reuse": []any{}, "tests": []any{},
		"budget": map[string]any{"diff_lines": int64(50), "files": int64(2), "attempts": int64(1)},
	}
}

func TestAC3_V2Fields(t *testing.T) {
	defer guard(t)
	wire := testvectors.Wire()
	commitment := wire.Proposals[0]["commitment"]
	work := wire.Events[0]["data"].(map[string]any)["root"]
	digest := wire.RequestDigests[0]
	commit := testvectors.Lifecycle().Subjects["B"].(map[string]any)
	tests := []struct {
		kind, required string
		fields         map[string]any
	}{
		{"Contract", "path", map[string]any{
			"contract": "graph.store", "version": int64(1), "contract_kind": "module",
			"path": "contracts/graph/store.yaml", "digest": digest, "producer": work,
			"consumers": []any{}, "state": "proposed",
		}},
		{"GraphSnapshot", "indexers", map[string]any{
			"git_commit": commit, "digest": digest, "indexers": []any{map[string]any{"name": "scip-go", "version": "0.1.x"}},
			"files": int64(42), "symbols": int64(900),
		}},
		{"Contradiction", "reason", map[string]any{
			"target": "graph.store", "raised_by": "AS-1", "reason": "Example contradiction",
			"evidence": []any{digest}, "state": "open", "resolution": nil,
		}},
		{"Routing", "level", map[string]any{
			"work": work, "level": int64(2), "tier": "implementer", "model": "test-model",
			"attempt": int64(1), "reason": "Example route",
		}},
		{"Cost", "input_tokens", map[string]any{
			"work": work, "role": "executor", "tier": "implementer", "provider": "test-provider", "model": "test-model",
			"input_tokens": int64(90), "output_tokens": int64(10), "cached_tokens": int64(5), "usd_micros": int64(1),
		}},
		{"WorkSpec", "scope", workSpec()},
	}
	for _, test := range tests {
		name := "fields." + test.kind
		if test.kind == "WorkSpec" {
			name = test.kind
		}
		t.Run(name, func(t *testing.T) {
			defer guard(t)
			t.Run("valid", func(t *testing.T) {
				defer guard(t)
				requireValid(t, name, test.fields, "v2 example")
				if test.kind == "WorkSpec" {
					item := recordOfKind(wire.Expected.SnapshotAt8, "WorkItem")
					fields := maps.Clone(item["fields"].(map[string]any))
					fields["spec"] = test.fields
					requireValid(t, "fields.WorkItem", fields, "WorkItem with a spec")
					return
				}
				record := map[string]any{
					"id": test.kind + "-example", "kind": test.kind, "commitment": commitment,
					"changed": int64(1), "fields": test.fields,
				}
				requireValid(t, "record", record, "v2 record envelope")
			})
			t.Run("missing_"+test.required, func(t *testing.T) {
				defer guard(t)
				value := maps.Clone(test.fields)
				delete(value, test.required)
				_ = requireViolation(t, name, value)
			})
			t.Run("extra_field", func(t *testing.T) {
				defer guard(t)
				value := maps.Clone(test.fields)
				value["extra"] = true
				_ = requireViolation(t, name, value)
			})
			if test.kind == "GraphSnapshot" {
				t.Run("bare_git_commit_digest", func(t *testing.T) {
					defer guard(t)
					value := maps.Clone(test.fields)
					value["git_commit"] = commit["digest"]
					_ = requireViolation(t, name, value)
				})
			}
		})
	}
}

func recordOfKind(snapshot map[string]any, kind string) map[string]any {
	for _, value := range snapshot["projection"].(map[string]any)["records"].([]any) {
		record := value.(map[string]any)
		if record["kind"] == kind {
			return record
		}
	}
	panic("wire fixture missing record kind " + kind)
}

func TestAC4_ClosedObjectsAtDepth(t *testing.T) {
	defer guard(t)
	wire := testvectors.Wire()
	snapshot := wire.Expected.SnapshotAt4
	approval := recordOfKind(snapshot, "Approval")
	approvalFields := approval["fields"].(map[string]any)
	approvalRead := approvalFields["read_versions"].([]any)[0].(map[string]any)
	receipt := wire.Expected.AdmissionReplies[1]["stdout"].(map[string]any)["receipt"].(map[string]any)
	referenceAnswers := referenceParity(t)["answers"].(map[string]any)
	answerRow := referenceAnswers["q08"].([]any)[0].(map[string]any)
	spec := workSpec()
	tests := []struct {
		label, name string
		value       map[string]any
		target      map[string]any
	}{
		{"record_envelope", "record", approval, approval},
		{"approval_read_version", "fields.Approval", approvalFields, approvalRead},
		{"receipt_details", "receipt", receipt, receipt["details"].(map[string]any)},
		{"snapshot_projection", "snapshot", snapshot, snapshot["projection"].(map[string]any)},
		{"work_spec_scope", "WorkSpec", spec, spec["scope"].(map[string]any)},
		{"work_spec_budget", "WorkSpec", spec, spec["budget"].(map[string]any)},
		{"answer_row", "answers", referenceAnswers, answerRow},
	}
	for _, test := range tests {
		t.Run(test.label, func(t *testing.T) {
			defer guard(t)
			requireValid(t, test.name, test.value, "original object")
			test.target["extra"] = true
			defer delete(test.target, "extra")
			_ = requireViolation(t, test.name, test.value)
		})
	}
	t.Run("golden_dump_metadata", func(t *testing.T) {
		defer guard(t)
		name := testvectors.GoldenNames()[0]
		dumpRow := decodeObject(t, testvectors.Golden(name).SnapshotLines[0])
		_ = requireViolation(t, "snapshot", dumpRow)
		delete(dumpRow, "projection_checksum")
		requireValid(t, "snapshot", dumpRow, "wire shape of the same projection")
	})
}

func TestAC5_GeneratedApprovalIDsAreReserved(t *testing.T) {
	defer guard(t)
	wire := testvectors.Wire()
	approval := recordOfKind(wire.Expected.SnapshotAt4, "Approval")
	work := recordOfKind(wire.Expected.SnapshotAt4, "WorkItem")
	id := approval["id"].(string)
	requestDigest := approval["fields"].(map[string]any)["request_digest"].(string)
	if want := "a:" + requestDigest; id != want {
		t.Fatalf("vector approval ID = %q, want %q", id, want)
	}

	request := wire.Events[2]
	pending := wire.Expected.AdmissionReplies[1]["stdout"].(map[string]any)["receipt"].(map[string]any)
	proposal := wire.Proposals[0]
	for _, test := range []struct {
		label, name      string
		value            map[string]any
		idObject         map[string]any
		key              string
		generatedAllowed bool
	}{
		{"approval_record", "record", approval, approval, "id", true},
		{"approval_requested", "event", request, request["data"].(map[string]any), "id", true},
		{"pending_receipt", "receipt", pending, pending["details"].(map[string]any), "approval_id", true},
		{"work_item", "record", work, work, "id", false},
		{"proposal_id", "proposal", proposal, proposal, "proposal_id", false},
		{"proposal_commitment", "proposal", proposal, proposal, "commitment", false},
	} {
		t.Run(test.label, func(t *testing.T) {
			defer guard(t)
			oldID := test.idObject[test.key]
			defer func() { test.idObject[test.key] = oldID }()
			test.idObject[test.key] = id
			if test.generatedAllowed {
				requireValid(t, test.name, test.value, "generated approval ID")
			} else {
				_ = requireViolation(t, test.name, test.value)
			}
			for _, probe := range []struct{ label, id string }{
				{"invalid_separator", "_" + id},
				{"short_digest", id[:len(id)-1]},
				{"uppercase_digest", "a:" + strings.ToUpper(requestDigest)},
				{"trailing_newline", id + "\n"},
			} {
				t.Run(probe.label, func(t *testing.T) {
					defer guard(t)
					test.idObject[test.key] = probe.id
					_ = requireViolation(t, test.name, test.value)
				})
			}
		})
	}
}

func TestAC6_ExactViolationPointers(t *testing.T) {
	defer guard(t)
	wire := testvectors.Wire()
	eventMissing := maps.Clone(wire.Events[0])
	data := maps.Clone(eventMissing["data"].(map[string]any))
	delete(data, "statement")
	eventMissing["data"] = data

	eventExtra := maps.Clone(wire.Events[0])
	data = maps.Clone(eventExtra["data"].(map[string]any))
	data["extra"] = true
	eventExtra["data"] = data

	eventEscaped := maps.Clone(wire.Events[0])
	data = maps.Clone(eventEscaped["data"].(map[string]any))
	data["extra/~"] = true
	eventEscaped["data"] = data

	proposal := maps.Clone(wire.Proposals[0])
	proposal["base_revision"] = false

	snapshot := wire.Expected.SnapshotAt4
	snapshot["projection"].(map[string]any)["records"].([]any)[0].(map[string]any)["changed"] = false

	for _, test := range []struct {
		label, name, pointer string
		value                any
	}{
		{"missing_property", "event", "/data/statement", eventMissing},
		{"extra_property", "event", "/data/extra", eventExtra},
		{"escaped_property", "event", "/data/extra~1~0", eventEscaped},
		{"wrong_type", "proposal", "/base_revision", proposal},
		{"array_item_property", "snapshot", "/projection/records/0/changed", snapshot},
		{"whole_value", "proposal", "", nil},
	} {
		t.Run(test.label, func(t *testing.T) {
			defer guard(t)
			violation := requireViolation(t, test.name, test.value)
			if violation.Schema != test.name || violation.Pointer != test.pointer {
				t.Errorf("violation schema and pointer = (%q, %q), want (%q, %q)",
					violation.Schema, violation.Pointer, test.name, test.pointer)
			}
		})
	}
}

func TestAC6_UnknownSchema(t *testing.T) {
	defer guard(t)
	if err := schema.Validate("nonexistent-schema", nil); !errors.Is(err, schema.ErrUnknownSchema) {
		t.Errorf("Validate unknown schema error = %v, want ErrUnknownSchema", err)
	}
}

func TestAC7_GraphContractExamples(t *testing.T) {
	defer guard(t)
	raw, err := os.ReadFile(filepath.Join("..", "..", "contracts", "graph", "store.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	var contract struct {
		Data struct {
			Examples map[string]map[string]any
		}
	}
	if err := yaml.Unmarshal(raw, &contract); err != nil {
		t.Fatal(err)
	}
	for _, kind := range []string{"node", "edge", "meta"} {
		t.Run(kind, func(t *testing.T) {
			defer guard(t)
			value, err := json.Marshal(contract.Data.Examples[kind])
			if err != nil {
				t.Fatal(err)
			}
			requireValid(t, "graph."+kind, decodeObject(t, value), "graph contract example")
		})
	}
	t.Run("unknown_kind", func(t *testing.T) {
		defer guard(t)
		node := maps.Clone(contract.Data.Examples["node"])
		node["kind"] = "unknown"
		value, err := json.Marshal(node)
		if err != nil {
			t.Fatal(err)
		}
		_ = requireViolation(t, "graph.node", decodeObject(t, value))
	})
}
