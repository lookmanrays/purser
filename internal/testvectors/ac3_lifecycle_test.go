package testvectors

import (
	"testing"
)

// AC-3: The loader exposes the lifecycle trace's steps, outcome vectors and overlays.

func TestAC3_LifecycleSections(t *testing.T) {
	defer guard(t)

	lc := Lifecycle()
	if lc == nil {
		t.Fatal("Lifecycle() returned nil")
	}

	// 1. steps
	if len(lc.Steps) != 10 {
		t.Fatalf("len(lc.Steps) = %d, want 10", len(lc.Steps))
	}
	for i, step := range lc.Steps {
		if _, ok := step["point"]; !ok {
			t.Errorf("step %d missing point", i)
		}
		if _, ok := step["admitted_or_external_facts"]; !ok {
			t.Errorf("step %d missing admitted_or_external_facts", i)
		}
		if _, ok := step["required_observations"]; !ok {
			t.Errorf("step %d missing required_observations", i)
		}
	}

	// 2. outcome vectors
	if len(lc.OutcomeVectors) != 5 {
		t.Fatalf("len(lc.OutcomeVectors) = %d, want 5", len(lc.OutcomeVectors))
	}
	for i, ov := range lc.OutcomeVectors {
		if _, ok := ov["basis"]; !ok {
			t.Errorf("outcome vector %d missing basis", i)
		}
		if _, ok := ov["digest"]; !ok {
			t.Errorf("outcome vector %d missing digest", i)
		}
	}

	// 3. overlays: continuity_overlay
	if len(lc.ContinuityOverlay) == 0 {
		t.Fatal("lc.ContinuityOverlay is empty")
	}
	requiredOverlayKeys := []string{
		"purpose",
		"other_commitment",
		"independent_branches",
		"invalidated_assumption",
		"decision_under_reconsideration",
		"unaffected_original_siblings",
	}
	for _, k := range requiredOverlayKeys {
		if _, ok := lc.ContinuityOverlay[k]; !ok {
			t.Errorf("ContinuityOverlay missing required key %q", k)
		}
	}

	// 4. alternative cutoff orders and recovery
	if len(lc.AlternativeCutoffOrders) != 3 {
		t.Errorf("len(lc.AlternativeCutoffOrders) = %d, want 3", len(lc.AlternativeCutoffOrders))
	}
	if len(lc.AlternativeRecovery) != 3 {
		t.Errorf("len(lc.AlternativeRecovery) = %d, want 3", len(lc.AlternativeRecovery))
	}

	// 5. top-level contract and fixture fields
	if lc.Contract != "purser.kernel/v1" {
		t.Errorf("lc.Contract = %q, want %q", lc.Contract, "purser.kernel/v1")
	}
	if lc.Fixture != "notification retries" {
		t.Errorf("lc.Fixture = %q, want %q", lc.Fixture, "notification retries")
	}
	if len(lc.Subjects) != 4 {
		t.Errorf("len(lc.Subjects) = %d, want 4", len(lc.Subjects))
	}
	if len(lc.Support) != 2 {
		t.Errorf("len(lc.Support) = %d, want 2", len(lc.Support))
	}
	if len(lc.EvidenceBindings) != 2 {
		t.Errorf("len(lc.EvidenceBindings) = %d, want 2", len(lc.EvidenceBindings))
	}
	if len(lc.ImmutableEffect) == 0 {
		t.Error("lc.ImmutableEffect is empty")
	}
}
