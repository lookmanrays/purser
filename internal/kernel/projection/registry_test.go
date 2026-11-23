package projection

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/lookmanrays/purser/internal/kernel/codec"
	"github.com/lookmanrays/purser/internal/testvectors"
)

func hasCode(err error, want string) bool {
	if err == nil {
		return false
	}
	var ce *codec.Error
	if errors.As(err, &ce) && ce.Code == want {
		return true
	}
	type coder interface{ Code() string }
	var c coder
	if errors.As(err, &c) && c.Code() == want {
		return true
	}
	return strings.Contains(err.Error(), want)
}

// AC-2: An unregistered event type or version fails with unsupported_version,
// and the projection stays as it was.
func TestAC2_UnregisteredEventTypeOrVersionFails(t *testing.T) {
	defer guard(t)

	reg := Base()
	initial := Empty()

	t.Run("UnregisteredEventType", func(t *testing.T) {
		defer guard(t)

		ev := codec.Event{
			V:            1,
			EventVersion: 1,
			Revision:     1,
			BatchStart:   1,
			BatchSize:    1,
			ActorID:      "principal:local",
			ProposalID:   "p-unknown",
			At:           "2026-11-18T12:00:00.000000Z",
			Type:         "TotallyUnknownEventType",
			Data:         map[string]any{"foo": "bar"},
		}

		pOut, changes, err := reg.Apply(initial, ev)
		if err == nil {
			t.Fatal("Apply with unregistered event type succeeded, want unsupported_version")
		}
		if !hasCode(err, "unsupported_version") {
			t.Fatalf("expected unsupported_version, got: %v", err)
		}
		if len(pOut.Records) != len(initial.Records) {
			t.Errorf("projection was mutated on failure: got %d records", len(pOut.Records))
		}
		if len(changes.Records) != 0 || len(changes.Receipts) != 0 {
			t.Errorf("changes were returned on failure: %+v", changes)
		}
	})

	t.Run("UnsupportedVersion", func(t *testing.T) {
		defer guard(t)

		ev := codec.Event{
			V:            1,
			EventVersion: 42,
			Revision:     1,
			BatchStart:   1,
			BatchSize:    1,
			ActorID:      "principal:local",
			ProposalID:   "p-create",
			At:           "2026-11-18T12:00:00.000000Z",
			Type:         "CommitmentCreated",
			Data: map[string]any{
				"id":        "C-1",
				"root":      "W-1",
				"principal": "principal:local",
				"statement": "hello",
			},
		}

		pOut, _, err := reg.Apply(initial, ev)
		if err == nil {
			t.Fatal("Apply with unsupported event version succeeded, want unsupported_version")
		}
		if !hasCode(err, "unsupported_version") {
			t.Fatalf("expected unsupported_version, got: %v", err)
		}
		if len(pOut.Records) != len(initial.Records) {
			t.Errorf("projection was mutated on failure")
		}
	})

	t.Run("DuplicateReducerRegistrationFails", func(t *testing.T) {
		defer guard(t)

		customReg := NewRegistry()
		dummy := func(p Projection, _ map[string]any, _, _ int64) (Projection, Changes, error) {
			return p, Changes{}, nil
		}

		err1 := customReg.Register("MyEvent", 1, dummy)
		if err1 != nil {
			t.Fatalf("first registration failed: %v", err1)
		}

		err2 := customReg.Register("MyEvent", 1, dummy)
		if err2 == nil {
			t.Fatal("duplicate registration succeeded, want duplicate_reducer")
		}
		if !hasCode(err2, "duplicate_reducer") {
			t.Errorf("expected duplicate_reducer, got: %v", err2)
		}
	})

	t.Run("KnownMethod", func(t *testing.T) {
		defer guard(t)

		if !reg.Known("CommitmentCreated", 1) {
			t.Error("Base() should know CommitmentCreated v1")
		}
		if !reg.Known("CommitmentCreated", 0) {
			t.Error("Base() should know CommitmentCreated v0 (legacy upcast)")
		}
		if !reg.Known("ProposalReceipted", 1) {
			t.Error("Base() should know ProposalReceipted v1")
		}
		if reg.Known("CommitmentCreated", 2) {
			t.Error("Base() should not know CommitmentCreated v2")
		}
		if reg.Known("UnknownEvent", 1) {
			t.Error("Base() should not know UnknownEvent")
		}
	})
}

// AC-4: Folding each golden log from tests/test_gate.py scenarios through
// projection.Base() reproduces every recorded snapshot.
func TestAC4_FoldGoldenLogsReproducesSnapshots(t *testing.T) {
	defer guard(t)

	reg := Base()
	if reg == nil {
		t.Fatal("Base() returned nil")
	}

	gateScenarios := 0
	for _, name := range testvectors.GoldenNames() {
		if !strings.HasPrefix(name, "test_gate.") {
			continue
		}
		gateScenarios++

		t.Run(name, func(t *testing.T) {
			defer guard(t)

			sc := testvectors.Golden(name)
			if sc == nil {
				t.Fatalf("testvectors.Golden(%q) returned nil", name)
			}

			p := Empty()
			snapIdx := 0

			for _, ge := range sc.Events {
				ev := codec.Event{
					V:            ge.V,
					EventVersion: ge.EventVersion,
					Revision:     ge.Revision,
					BatchStart:   ge.BatchStart,
					BatchSize:    ge.BatchSize,
					ActorID:      ge.ActorID,
					ProposalID:   ge.ProposalID,
					At:           ge.At,
					Type:         ge.Type,
					Data:         ge.Data,
					PrevHash:     ge.PrevHash,
					Hash:         ge.Hash,
				}

				var err error
				p, _, err = reg.Apply(p, ev)
				if err != nil {
					t.Fatalf("event rev %d (%s) Apply failed: %v", ev.Revision, ev.Type, err)
				}

				if snapIdx < len(sc.Snapshots) && ev.Revision == sc.Snapshots[snapIdx].Revision {
					wantSnap := sc.Snapshots[snapIdx]
					gotChecksum, err := p.Checksum()
					if err != nil {
						t.Fatalf("Checksum at rev %d failed: %v", ev.Revision, err)
					}
					if gotChecksum != wantSnap.ProjectionChecksum {
						t.Errorf("rev %d: got checksum %q, want %q", ev.Revision, gotChecksum, wantSnap.ProjectionChecksum)
					}
					snapIdx++
				}
			}

			if snapIdx != len(sc.Snapshots) {
				t.Errorf("verified %d of %d snapshots in scenario %s", snapIdx, len(sc.Snapshots), name)
			}
		})
	}

	if gateScenarios == 0 {
		t.Fatal("no test_gate.* scenarios found in golden vectors")
	}

	t.Run("WireLegacyUpcast", func(t *testing.T) {
		defer guard(t)

		w := testvectors.Wire()
		if w == nil {
			t.Fatal("Wire vector is nil")
		}

		p := Empty()
		for _, rawEv := range w.Legacy.Events {
			rawBytes, err := json.Marshal(rawEv)
			if err != nil {
				t.Fatalf("marshal legacy event: %v", err)
			}
			ev, err := codec.ParseEvent(rawBytes)
			if err != nil {
				t.Fatalf("ParseEvent legacy event rev %v: %v", rawEv["revision"], err)
			}

			var applyErr error
			p, _, applyErr = reg.Apply(p, ev)
			if applyErr != nil {
				t.Fatalf("Apply legacy event rev %d failed: %v", ev.Revision, applyErr)
			}
		}

		gotChecksum, err := p.Checksum()
		if err != nil {
			t.Fatalf("Checksum of legacy projection failed: %v", err)
		}
		if gotChecksum != w.Legacy.ExpectedProjectionHash {
			t.Errorf("legacy projection checksum = %q, want %q", gotChecksum, w.Legacy.ExpectedProjectionHash)
		}

		// Verify upcast: C-notify statement must match the original text
		rec, ok := p.Record("C-notify")
		if !ok {
			t.Fatal("C-notify record not found in legacy projection")
		}
		stmt, ok := rec.Fields["statement"].(string)
		if !ok || stmt != "Add notification retries." {
			t.Errorf("upcast statement = %q, want %q", stmt, "Add notification retries.")
		}
		if _, hasText := rec.Fields["text"]; hasText {
			t.Error("upcast record still has 'text' field, should be renamed to 'statement'")
		}
	})
}

// TestRegistry_ParityNegativeCases tests all rejection branches derived from
// reference/python/purser/kernel/projector.py.
func TestRegistry_ParityNegativeCases(t *testing.T) {
	defer guard(t)

	_, currentFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("runtime.Caller failed")
	}
	projDir := filepath.Dir(currentFile)
	parityPath := filepath.Join(projDir, "testdata", "parity.json")

	raw, err := os.ReadFile(parityPath)
	if err != nil {
		t.Fatalf("read parity.json: %v", err)
	}

	var fixture struct {
		Cases []struct {
			Name          string         `json:"name"`
			EventType     string         `json:"event_type"`
			EventVersion  int64          `json:"event_version"`
			Data          map[string]any `json:"data"`
			InitialEvents []struct {
				Type     string         `json:"type"`
				Data     map[string]any `json:"data"`
				Rev      int64          `json:"rev"`
				BatchEnd int64          `json:"batch_end"`
				Version  int64          `json:"version"`
			} `json:"initial_events"`
			ExpectError *string `json:"expect_error"`
		} `json:"cases"`
	}

	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatalf("parse parity.json: %v", err)
	}

	reg := Base()

	for _, tc := range fixture.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			defer guard(t)

			p := Empty()
			for _, ie := range tc.InitialEvents {
				v := ie.Version
				if v == 0 {
					v = 1
				}
				ev := codec.Event{
					V:            1,
					EventVersion: v,
					Revision:     ie.Rev,
					BatchStart:   ie.Rev,
					BatchSize:    1,
					ActorID:      "principal:local",
					ProposalID:   "p-init",
					At:           "2026-11-18T12:00:00.000000Z",
					Type:         ie.Type,
					Data:         ie.Data,
				}
				var applyErr error
				p, _, applyErr = reg.Apply(p, ev)
				if applyErr != nil {
					t.Fatalf("setup event %s failed: %v", ie.Type, applyErr)
				}
			}

			targetEv := codec.Event{
				V:            1,
				EventVersion: tc.EventVersion,
				Revision:     10,
				BatchStart:   10,
				BatchSize:    1,
				ActorID:      "principal:local",
				ProposalID:   "p-target",
				At:           "2026-11-18T12:00:00.000000Z",
				Type:         tc.EventType,
				Data:         tc.Data,
			}

			_, _, applyErr := reg.Apply(p, targetEv)
			if tc.ExpectError != nil {
				if applyErr == nil {
					t.Fatalf("expected error %q, got nil", *tc.ExpectError)
				}
				if !hasCode(applyErr, *tc.ExpectError) {
					t.Errorf("got error %v, want code %q", applyErr, *tc.ExpectError)
				}
			} else if applyErr != nil {
				t.Fatalf("unexpected error: %v", applyErr)
			}
		})
	}
}
