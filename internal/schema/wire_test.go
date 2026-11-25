package schema_test

import (
	"bytes"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"github.com/lookmanrays/purser/internal/kernel/codec"
	"github.com/lookmanrays/purser/internal/schema"
	"github.com/lookmanrays/purser/internal/testvectors"
)

func guard(t *testing.T) {
	if r := recover(); r != nil {
		t.Fatalf("panic: %v", r)
	}
}

func requireValid(t *testing.T, name string, value any, where string) {
	t.Helper()
	if err := schema.Validate(name, value); err != nil {
		t.Errorf("%s should validate against %s: %v", where, name, err)
	}
}

func requireViolation(t *testing.T, name string, value any) *schema.ValidationError {
	t.Helper()
	var violation *schema.ValidationError
	err := schema.Validate(name, value)
	if !errors.As(err, &violation) || violation == nil {
		t.Fatalf("Validate(%q) error = %v (%T), want *schema.ValidationError", name, err, err)
	}
	return violation
}

func decodeObject(t *testing.T, raw []byte) map[string]any {
	t.Helper()
	object, err := codec.DecodeObject(raw)
	if err != nil {
		t.Fatal(err)
	}
	return object
}

func referenceParity(t *testing.T) map[string]any {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("testdata", "reference-parity.json"))
	if err != nil {
		t.Fatal(err)
	}
	return decodeObject(t, raw)
}

func TestAC1_WireExamples(t *testing.T) {
	defer guard(t)
	wire := testvectors.Wire()

	t.Run("proposals", func(t *testing.T) {
		defer guard(t)
		for _, proposal := range wire.Proposals {
			requireValid(t, "proposal", proposal, proposal["proposal_id"].(string))
		}
		for _, branch := range wire.IndependentCaseBranches {
			proposals, _ := branch["ordered_proposals"].([]any)
			for _, item := range proposals {
				proposal := item.(map[string]any)
				requireValid(t, "proposal", proposal, proposal["proposal_id"].(string))
			}
		}
	})
	t.Run("events", func(t *testing.T) {
		defer guard(t)
		for i, event := range wire.Events {
			requireValid(t, "event", event, fmt.Sprintf("wire event %d (%s)", i+1, event["type"]))
		}
	})
	t.Run("legacy_events", func(t *testing.T) {
		defer guard(t)
		for i, event := range wire.Legacy.Events {
			requireValid(t, "event", event, fmt.Sprintf("legacy event %d (%s)", i+1, event["type"]))
		}
	})
	t.Run("receipts", func(t *testing.T) {
		defer guard(t)
		for i, reply := range wire.Expected.AdmissionReplies {
			receipt := reply["stdout"].(map[string]any)["receipt"]
			requireValid(t, "receipt", receipt, fmt.Sprintf("admission reply %d", i+1))
		}
		for _, snapshot := range []map[string]any{wire.Expected.SnapshotAt4, wire.Expected.SnapshotAt8} {
			for _, receipt := range snapshot["projection"].(map[string]any)["receipts"].([]any) {
				row := receipt.(map[string]any)
				requireValid(t, "receipt", row, fmt.Sprintf("snapshot %v receipt %s", snapshot["revision"], row["proposal_id"]))
			}
		}
	})
	t.Run("snapshots", func(t *testing.T) {
		defer guard(t)
		requireValid(t, "snapshot", wire.Expected.SnapshotAt4, "snapshot_at_4")
		requireValid(t, "snapshot", wire.Expected.SnapshotAt8, "snapshot_at_8")
	})
	t.Run("binding", func(t *testing.T) {
		defer guard(t)
		requireValid(t, "binding", wire.Binding, "wire binding")
	})
	t.Run("answers", func(t *testing.T) {
		defer guard(t)
		requireValid(t, "answers", wire.Expected.InitialStatus["answers"], "initial wire status")
		for _, status := range wire.Expected.StatusByRevision {
			requireValid(t, "answers", status["answers"], fmt.Sprintf("wire status at revision %v", status["revision"]))
		}
		for _, status := range testvectors.LegacyStatus() {
			requireValid(t, "answers", status.Answers, fmt.Sprintf("M2 status at revision %d", status.Revision))
		}
		requireValid(t, "answers", referenceParity(t)["answers"], "reference rows for q01 to q10")
	})
}

func TestAC1_GoldenEvents(t *testing.T) {
	defer guard(t)
	for _, name := range testvectors.GoldenNames() {
		t.Run(name, func(t *testing.T) {
			defer guard(t)
			for i, raw := range testvectors.Golden(name).EventLines {
				event := decodeObject(t, raw)
				requireValid(t, "event", event, fmt.Sprintf("event line %d (%s)", i+1, event["type"]))
			}
		})
	}
}

func TestAC1_ReferenceParity(t *testing.T) {
	defer guard(t)
	parity := referenceParity(t)
	for _, item := range parity["validation"].([]any) {
		probe := item.(map[string]any)
		t.Run(probe["name"].(string), func(t *testing.T) {
			defer guard(t)
			name, value := probe["schema"].(string), probe["value"]
			if !probe["valid"].(bool) {
				_ = requireViolation(t, name, value)
				return
			}
			requireValid(t, name, value, "reference accepted input")
			if receipt, ok := probe["reference_receipt"]; ok {
				requireValid(t, "receipt", receipt, "reference durable rejection")
			}
		})
	}
	for _, item := range parity["decode"].([]any) {
		probe := item.(map[string]any)
		t.Run(probe["name"].(string), func(t *testing.T) {
			defer guard(t)
			value, err := codec.Decode([]byte(probe["raw"].(string)))
			want, decoded := probe["canonical"].(string)
			if !decoded {
				if err == nil {
					t.Fatal("codec accepted bytes rejected by the reference")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			canonical, err := codec.Encode(value)
			if err != nil {
				t.Fatal(err)
			}
			if !bytes.Equal(canonical, []byte(want)) {
				t.Fatalf("canonical proposal = %q, want %q", canonical, want)
			}
			requireValid(t, "proposal", value, "decoded proposal")
		})
	}
}

func checkRecord(t *testing.T, value any, where string) {
	t.Helper()
	record := value.(map[string]any)
	context := fmt.Sprintf("%s, record %s (%s)", where, record["id"], record["kind"])
	requireValid(t, "record", record, context)
	requireValid(t, "fields."+record["kind"].(string), record["fields"], context)
}

func TestAC2_WireAndGoldenRecords(t *testing.T) {
	defer guard(t)
	wire := testvectors.Wire()
	for _, snapshot := range []map[string]any{wire.Expected.SnapshotAt4, wire.Expected.SnapshotAt8} {
		name := fmt.Sprintf("snapshot_at_%v", snapshot["revision"])
		t.Run(name, func(t *testing.T) {
			defer guard(t)
			for _, record := range snapshot["projection"].(map[string]any)["records"].([]any) {
				checkRecord(t, record, name)
			}
		})
	}
	for _, name := range testvectors.GoldenNames() {
		t.Run(name, func(t *testing.T) {
			defer guard(t)
			for _, row := range testvectors.Golden(name).Snapshots {
				for _, record := range row.Projection["records"].([]any) {
					checkRecord(t, record, fmt.Sprintf("snapshot at revision %d", row.Revision))
				}
			}
		})
	}
}
