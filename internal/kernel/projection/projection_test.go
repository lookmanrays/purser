package projection

import (
	"reflect"
	"testing"

	"github.com/lookmanrays/purser/internal/kernel/codec"
	"github.com/lookmanrays/purser/internal/testvectors"
)

func guard(t *testing.T) {
	if r := recover(); r != nil {
		t.Fatalf("panic: %v", r)
	}
}

// AC-3: Empty() has no records, receipts or edges, its map form holds empty
// lists, and its checksum is the hash of that map.
func TestAC3_EmptyProjectionShape(t *testing.T) {
	defer guard(t)

	p := Empty()
	if len(p.Records) != 0 {
		t.Errorf("len(Empty().Records) = %d, want 0", len(p.Records))
	}
	if len(p.Receipts) != 0 {
		t.Errorf("len(Empty().Receipts) = %d, want 0", len(p.Receipts))
	}
	if len(p.Edges) != 0 {
		t.Errorf("len(Empty().Edges) = %d, want 0", len(p.Edges))
	}

	m := p.Map()
	if m == nil {
		t.Fatal("Empty().Map() returned nil")
	}
	for _, key := range []string{"records", "receipts", "edges"} {
		v, ok := m[key]
		if !ok {
			t.Errorf("Empty().Map() missing key %q", key)
			continue
		}
		rv := reflect.ValueOf(v)
		if rv.Kind() != reflect.Slice || rv.Len() != 0 {
			t.Errorf("Empty().Map()[%q] is not an empty slice", key)
		}
	}

	expectedChecksum, err := codec.Hash(codec.TagProjection, m)
	if err != nil {
		t.Fatalf("codec.Hash failed on empty map: %v", err)
	}
	gotChecksum, err := p.Checksum()
	if err != nil {
		t.Fatalf("p.Checksum() failed: %v", err)
	}
	if gotChecksum != expectedChecksum {
		t.Errorf("p.Checksum() = %q, want %q", gotChecksum, expectedChecksum)
	}

	// Query methods on empty projection
	if _, ok := p.Record("any"); ok {
		t.Error("Empty().Record() found record in empty projection")
	}
	if records := p.ByKind("Commitment"); len(records) != 0 {
		t.Errorf("Empty().ByKind() returned %d records", len(records))
	}
	if _, ok := p.Receipt("actor", "prop"); ok {
		t.Error("Empty().Receipt() found receipt in empty projection")
	}
	if edges := p.EdgesTo("depends_on", "target"); len(edges) != 0 {
		t.Errorf("Empty().EdgesTo() returned %d edges", len(edges))
	}
}

// AC-1: FromMap of snapshot_at_4 and snapshot_at_8 yields projections whose
// checksums equal the projection_hash recorded at those revisions, whose
// lookups return the snapshot's records and receipts, and whose lists keep
// the specification's sort order.
func TestAC1_ProjectionChecksumAndSnapshotParity(t *testing.T) {
	defer guard(t)

	w := testvectors.Wire()
	if w == nil {
		t.Fatal("Wire vector is nil")
	}

	t.Run("SnapshotAt4", func(t *testing.T) {
		defer guard(t)

		snapMap, ok := w.Expected.SnapshotAt4["projection"].(map[string]any)
		if !ok {
			t.Fatal("expected.snapshot_at_4.projection is not map[string]any")
		}
		p4, err := FromMap(snapMap)
		if err != nil {
			t.Fatalf("FromMap(snapshot_at_4) error: %v", err)
		}

		gotChecksum, err := p4.Checksum()
		if err != nil {
			t.Fatalf("p4.Checksum() error: %v", err)
		}

		wantChecksum, ok := w.Expected.StatusByRevision[1]["projection_hash"].(string)
		if !ok {
			t.Fatal("missing status_by_revision[1].projection_hash")
		}
		if gotChecksum != wantChecksum {
			t.Errorf("snapshot_at_4 projection checksum = %q, want %q", gotChecksum, wantChecksum)
		}

		// Lookups on snapshot_at_4
		cRecord, ok := p4.Record("C-notify")
		if !ok {
			t.Error("p4.Record('C-notify') not found")
		} else {
			if cRecord.Kind != "Commitment" {
				t.Errorf("C-notify kind = %q, want Commitment", cRecord.Kind)
			}
			if cRecord.Commitment != "C-notify" {
				t.Errorf("C-notify commitment = %q, want C-notify", cRecord.Commitment)
			}
			if cRecord.Changed != 1 {
				t.Errorf("C-notify changed = %d, want 1", cRecord.Changed)
			}
			if cRecord.Fields["state"] != "proposed" {
				t.Errorf("C-notify state = %v, want proposed", cRecord.Fields["state"])
			}
		}

		wRecord, ok := p4.Record("W-root")
		if !ok {
			t.Error("p4.Record('W-root') not found")
		} else {
			if wRecord.Kind != "WorkItem" {
				t.Errorf("W-root kind = %q, want WorkItem", wRecord.Kind)
			}
			if wRecord.Commitment != "C-notify" {
				t.Errorf("W-root commitment = %q, want C-notify", wRecord.Commitment)
			}
		}

		aRecord, ok := p4.Record("a:19181b8d8107854127291b7431d56405d165271426f0ca411d19e61e7c542884") // ApprovalRequested.data.id in wire-v1.json
		if !ok {
			t.Error("p4.Record(approvalID) not found")
		} else if aRecord.Kind != "Approval" {
			t.Errorf("approval kind = %q, want Approval", aRecord.Kind)
		}

		r1, ok := p4.Receipt("principal:local", "p-create")
		if !ok {
			t.Error("p4.Receipt(p-create) not found")
		} else {
			if r1.Disposition != "admitted" {
				t.Errorf("p-create disposition = %q, want admitted", r1.Disposition)
			}
			if r1.Revision != 2 {
				t.Errorf("p-create revision = %d, want 2", r1.Revision)
			}
		}

		r2, ok := p4.Receipt("principal:local", "p-hold")
		if !ok {
			t.Error("p4.Receipt(p-hold) not found")
		} else {
			if r2.Disposition != "pending" {
				t.Errorf("p-hold disposition = %q, want pending", r2.Disposition)
			}
			if r2.Revision != 4 {
				t.Errorf("p-hold revision = %d, want 4", r2.Revision)
			}
		}

		commitments := p4.ByKind("Commitment")
		if len(commitments) != 1 {
			t.Errorf("len(ByKind('Commitment')) = %d, want 1", len(commitments))
		}
		workItems := p4.ByKind("WorkItem")
		if len(workItems) != 1 {
			t.Errorf("len(ByKind('WorkItem')) = %d, want 1", len(workItems))
		}
	})

	t.Run("SnapshotAt8", func(t *testing.T) {
		defer guard(t)

		snapMap, ok := w.Expected.SnapshotAt8["projection"].(map[string]any)
		if !ok {
			t.Fatal("expected.snapshot_at_8.projection is not map[string]any")
		}
		p8, err := FromMap(snapMap)
		if err != nil {
			t.Fatalf("FromMap(snapshot_at_8) error: %v", err)
		}

		gotChecksum, err := p8.Checksum()
		if err != nil {
			t.Fatalf("p8.Checksum() error: %v", err)
		}

		wantChecksum, ok := w.Expected.StatusByRevision[2]["projection_hash"].(string)
		if !ok {
			t.Fatal("missing status_by_revision[2].projection_hash")
		}
		if gotChecksum != wantChecksum {
			t.Errorf("snapshot_at_8 projection checksum = %q, want %q", gotChecksum, wantChecksum)
		}

		// In snapshot_at_8, C-notify is active
		cRecord, ok := p8.Record("C-notify")
		if !ok {
			t.Error("p8.Record('C-notify') not found")
		} else if cRecord.Fields["state"] != "active" {
			t.Errorf("C-notify state = %v, want active", cRecord.Fields["state"])
		}

		// p-hold receipt advanced to admitted at revision 8
		rHold, ok := p8.Receipt("principal:local", "p-hold")
		if !ok {
			t.Error("p8.Receipt(p-hold) not found")
		} else {
			if rHold.Disposition != "admitted" {
				t.Errorf("p-hold disposition = %q, want admitted", rHold.Disposition)
			}
			if rHold.Revision != 8 {
				t.Errorf("p-hold revision = %d, want 8", rHold.Revision)
			}
		}

		// p-answer receipt is admitted at revision 8
		rAns, ok := p8.Receipt("principal:local", "p-answer")
		if !ok {
			t.Error("p8.Receipt(p-answer) not found")
		} else {
			if rAns.Disposition != "admitted" {
				t.Errorf("p-answer disposition = %q, want admitted", rAns.Disposition)
			}
			if rAns.Revision != 8 {
				t.Errorf("p-answer revision = %d, want 8", rAns.Revision)
			}
		}
	})

	t.Run("SortOrderInvariants", func(t *testing.T) {
		defer guard(t)

		// Unsorted maps given to FromMap must produce sorted slices in Projection
		raw := map[string]any{
			"records": []any{
				map[string]any{"id": "Z", "kind": "K", "commitment": "C", "changed": int64(1), "fields": map[string]any{}},
				map[string]any{"id": "A", "kind": "K", "commitment": "C", "changed": int64(1), "fields": map[string]any{}},
				map[string]any{"id": "M", "kind": "K", "commitment": "C", "changed": int64(1), "fields": map[string]any{}},
			},
			"receipts": []any{
				map[string]any{"actor_id": "b", "proposal_id": "2", "digest": "d", "disposition": "admitted", "code": "ok", "details": map[string]any{}, "revision": int64(1)},
				map[string]any{"actor_id": "a", "proposal_id": "9", "digest": "d", "disposition": "admitted", "code": "ok", "details": map[string]any{}, "revision": int64(1)},
				map[string]any{"actor_id": "a", "proposal_id": "1", "digest": "d", "disposition": "admitted", "code": "ok", "details": map[string]any{}, "revision": int64(1)},
			},
			"edges": []any{
				map[string]any{"kind": "y", "from_id": "1", "to_id": "1", "changed": int64(1)},
				map[string]any{"kind": "x", "from_id": "2", "to_id": "1", "changed": int64(1)},
				map[string]any{"kind": "x", "from_id": "1", "to_id": "2", "changed": int64(1)},
			},
		}

		p, err := FromMap(raw)
		if err != nil {
			t.Fatalf("FromMap failed on unsorted data: %v", err)
		}

		// Check records sorted by ID
		if p.Records[0].ID != "A" || p.Records[1].ID != "M" || p.Records[2].ID != "Z" {
			t.Errorf("Records not sorted by ID: %v, %v, %v", p.Records[0].ID, p.Records[1].ID, p.Records[2].ID)
		}

		// Check receipts sorted by (actor_id, proposal_id)
		if p.Receipts[0].ActorID != "a" || p.Receipts[0].ProposalID != "1" ||
			p.Receipts[1].ActorID != "a" || p.Receipts[1].ProposalID != "9" ||
			p.Receipts[2].ActorID != "b" || p.Receipts[2].ProposalID != "2" {
			t.Errorf("Receipts not sorted by (actor_id, proposal_id)")
		}

		// Check edges sorted by (kind, from_id, to_id)
		if p.Edges[0].Kind != "x" || p.Edges[0].FromID != "1" ||
			p.Edges[1].Kind != "x" || p.Edges[1].FromID != "2" ||
			p.Edges[2].Kind != "y" || p.Edges[2].FromID != "1" {
			t.Errorf("Edges not sorted by (kind, from_id, to_id)")
		}
	})
}

func TestProjection_EdgesTo(t *testing.T) {
	defer guard(t)

	raw := map[string]any{
		"records":  []any{},
		"receipts": []any{},
		"edges": []any{
			map[string]any{"kind": "depends_on", "from_id": "W-1", "to_id": "W-parent", "changed": int64(2)},
			map[string]any{"kind": "depends_on", "from_id": "W-2", "to_id": "W-parent", "changed": int64(2)},
			map[string]any{"kind": "witness", "from_id": "W-1", "to_id": "W-other", "changed": int64(3)},
		},
	}
	p, err := FromMap(raw)
	if err != nil {
		t.Fatalf("FromMap: %v", err)
	}

	matches := p.EdgesTo("depends_on", "W-parent")
	if len(matches) != 2 {
		t.Fatalf("len(EdgesTo) = %d, want 2", len(matches))
	}
	if matches[0].FromID != "W-1" || matches[1].FromID != "W-2" {
		t.Errorf("EdgesTo returned unexpected from_ids: %v, %v", matches[0].FromID, matches[1].FromID)
	}

	noMatches := p.EdgesTo("depends_on", "nonexistent")
	if len(noMatches) != 0 {
		t.Errorf("len(EdgesTo nonexistent) = %d, want 0", len(noMatches))
	}
}
