package testvectors

import (
	"bytes"
	"fmt"
	"path/filepath"
	"sort"
	"testing"
)

// AC-4: Golden logs exist for at least 20 scenarios and together contain
// all sixteen v1 record kinds.
// Test plan: Check that golden events form a valid chain by comparing each
// prev_hash with the previous line's hash, without re-hashing. Check that
// every v1 record kind listed in records.draft.yaml appears in at least one
// golden snapshot.

func TestAC4_GoldenScenarioCount(t *testing.T) {
	defer guard(t)

	names := GoldenNames()
	if len(names) < 20 {
		t.Fatalf("len(GoldenNames()) = %d, want at least 20", len(names))
	}
}

func TestAC4_GoldenSixteenV1RecordKinds(t *testing.T) {
	defer guard(t)

	names := GoldenNames()
	if len(names) == 0 {
		t.Fatal("no golden scenario names returned")
	}

	foundKinds := make(map[string]bool)
	for _, name := range names {
		sc := Golden(name)
		if sc == nil {
			t.Fatalf("Golden(%q) returned nil", name)
		}
		for _, sn := range sc.Snapshots {
			for _, rec := range sn.Records() {
				if kind, ok := rec["kind"].(string); ok && kind != "" {
					foundKinds[kind] = true
				}
			}
		}
	}

	var missing []string
	for _, want := range sixteenV1RecordKinds {
		if !foundKinds[want] {
			missing = append(missing, want)
		}
	}

	if len(missing) > 0 {
		var found []string
		for k := range foundKinds {
			found = append(found, k)
		}
		sort.Strings(found)
		sort.Strings(missing)
		t.Fatalf("missing %d of 16 v1 record kinds: %v (found: %v)", len(missing), missing, found)
	}
}

func TestAC4_GoldenEventChains(t *testing.T) {
	defer guard(t)

	names := GoldenNames()
	if len(names) == 0 {
		t.Fatal("no golden scenarios")
	}

	for _, name := range names {
		t.Run(name, func(t *testing.T) {
			defer guard(t)

			sc := Golden(name)
			if sc == nil {
				t.Fatalf("Golden(%q) returned nil", name)
			}
			if len(sc.Events) == 0 {
				t.Fatalf("scenario %s has no events", name)
			}

			for i := 0; i < len(sc.Events); i++ {
				ev := sc.Events[i]
				if i == 0 {
					if ev.Revision != 1 {
						t.Errorf("scenario %s: first event revision = %d, want 1", name, ev.Revision)
					}
					if ev.PrevHash != zeroHash {
						t.Errorf("scenario %s: first event prev_hash = %q, want %q", name, ev.PrevHash, zeroHash)
					}
				} else {
					prev := sc.Events[i-1]
					if ev.Revision != prev.Revision+1 {
						t.Errorf("scenario %s: event %d revision %d not contiguous with previous %d",
							name, i, ev.Revision, prev.Revision)
					}
					if ev.PrevHash != prev.Hash {
						t.Errorf("scenario %s: event %d prev_hash link %q != previous hash %q",
							name, i, ev.PrevHash, prev.Hash)
					}
				}
				if len(ev.Hash) != 64 {
					t.Errorf("scenario %s: event %d hash len = %d, want 64", name, i, len(ev.Hash))
				}
				if ev.Type == "" {
					t.Errorf("scenario %s: event %d missing type", name, i)
				}
			}
		})
	}
}

func TestAC4_GoldenExactLines(t *testing.T) {
	defer guard(t)

	names := GoldenNames()
	if len(names) == 0 {
		t.Fatal("no golden scenarios")
	}

	splitLines := func(raw []byte) [][]byte {
		trimmed := bytes.TrimSuffix(raw, []byte("\n"))
		if len(trimmed) == 0 {
			return nil
		}
		return bytes.Split(trimmed, []byte("\n"))
	}

	for _, name := range names {
		t.Run(name, func(t *testing.T) {
			defer guard(t)

			sc := Golden(name)
			if sc == nil {
				t.Fatalf("Golden(%q) returned nil", name)
			}

			// 1. EventLines must equal Raw("golden/<name>/events.jsonl") split on newlines
			eventsRaw := Raw(filepath.Join("golden", name, "events.jsonl"))
			wantEventLines := splitLines(eventsRaw)
			if len(sc.EventLines) != len(wantEventLines) {
				t.Fatalf("scenario %s: len(EventLines) = %d, want %d",
					name, len(sc.EventLines), len(wantEventLines))
			}
			for i := range wantEventLines {
				if !bytes.Equal(sc.EventLines[i], wantEventLines[i]) {
					t.Fatalf("scenario %s: EventLines[%d] does not match raw events.jsonl line", name, i)
				}
			}
			if len(sc.Events) != len(sc.EventLines) {
				t.Errorf("scenario %s: len(Events) = %d != len(EventLines) = %d",
					name, len(sc.Events), len(sc.EventLines))
			}

			// 2. SnapshotLines must equal Raw("golden/<name>/snapshots.jsonl") split on newlines
			snapshotsRaw := Raw(filepath.Join("golden", name, "snapshots.jsonl"))
			wantSnapshotLines := splitLines(snapshotsRaw)
			if len(sc.SnapshotLines) != len(wantSnapshotLines) {
				t.Fatalf("scenario %s: len(SnapshotLines) = %d, want %d",
					name, len(sc.SnapshotLines), len(wantSnapshotLines))
			}
			for i := range wantSnapshotLines {
				if !bytes.Equal(sc.SnapshotLines[i], wantSnapshotLines[i]) {
					t.Fatalf("scenario %s: SnapshotLines[%d] does not match raw snapshots.jsonl line", name, i)
				}
			}
			if len(sc.Snapshots) != len(sc.SnapshotLines) {
				t.Errorf("scenario %s: len(Snapshots) = %d != len(SnapshotLines) = %d",
					name, len(sc.Snapshots), len(sc.SnapshotLines))
			}
		})
	}
}

func TestAC4_GoldenEventTwelveFieldsAndInt64(t *testing.T) {
	defer guard(t)

	names := GoldenNames()
	if len(names) == 0 {
		t.Fatal("no golden scenarios")
	}

	var foundIntInEventData bool
	var foundIntInProjection bool

	var checkNoFloats func(val any, path string)
	checkNoFloats = func(val any, path string) {
		switch v := val.(type) {
		case float64:
			t.Fatalf("found float64 at %s (%v); numbers must decode as int64", path, v)
		case float32:
			t.Fatalf("found float32 at %s (%v); numbers must decode as int64", path, v)
		case map[string]any:
			for k, child := range v {
				checkNoFloats(child, path+"."+k)
			}
		case []any:
			for i, child := range v {
				checkNoFloats(child, fmt.Sprintf("%s[%d]", path, i))
			}
		}
	}

	var findInt func(val any) bool
	findInt = func(val any) bool {
		switch v := val.(type) {
		case int64:
			return true
		case map[string]any:
			for _, child := range v {
				if findInt(child) {
					return true
				}
			}
		case []any:
			for _, child := range v {
				if findInt(child) {
					return true
				}
			}
		}
		return false
	}

	for _, name := range names {
		sc := Golden(name)
		if sc == nil {
			t.Fatalf("Golden(%q) returned nil", name)
		}
		for i, ev := range sc.Events {
			// Check all 12 fields are accessible and valid
			if ev.V != 1 {
				t.Errorf("scenario %s event %d: V = %d, want 1", name, i, ev.V)
			}
			if ev.EventVersion <= 0 {
				t.Errorf("scenario %s event %d: EventVersion = %d, want > 0", name, i, ev.EventVersion)
			}
			if ev.Revision <= 0 {
				t.Errorf("scenario %s event %d: Revision = %d, want > 0", name, i, ev.Revision)
			}
			if ev.BatchStart <= 0 {
				t.Errorf("scenario %s event %d: BatchStart = %d, want > 0", name, i, ev.BatchStart)
			}
			if ev.BatchSize <= 0 {
				t.Errorf("scenario %s event %d: BatchSize = %d, want > 0", name, i, ev.BatchSize)
			}
			if ev.ActorID == "" {
				t.Errorf("scenario %s event %d: ActorID is empty", name, i)
			}
			if ev.ProposalID == "" {
				t.Errorf("scenario %s event %d: ProposalID is empty", name, i)
			}
			if ev.At == "" {
				t.Errorf("scenario %s event %d: At is empty", name, i)
			}
			if ev.Type == "" {
				t.Errorf("scenario %s event %d: Type is empty", name, i)
			}
			if ev.Data == nil {
				t.Errorf("scenario %s event %d: Data is nil", name, i)
			}
			if len(ev.PrevHash) != 64 {
				t.Errorf("scenario %s event %d: PrevHash len = %d, want 64", name, i, len(ev.PrevHash))
			}
			if len(ev.Hash) != 64 {
				t.Errorf("scenario %s event %d: Hash len = %d, want 64", name, i, len(ev.Hash))
			}

			// Check that no numbers in Data are decoded as float64
			checkNoFloats(ev.Data, fmt.Sprintf("%s.events[%d].data", name, i))
			if findInt(ev.Data) {
				foundIntInEventData = true
			}
		}

		for j, sn := range sc.Snapshots {
			checkNoFloats(sn.Projection, fmt.Sprintf("%s.snapshots[%d].projection", name, j))
			if findInt(sn.Projection) {
				foundIntInProjection = true
			}
		}
	}

	if !foundIntInEventData {
		t.Error("expected at least one int64 number in golden event Data payloads across all scenarios")
	}
	if !foundIntInProjection {
		t.Error("expected at least one int64 number in golden snapshot Projections across all scenarios")
	}
}
