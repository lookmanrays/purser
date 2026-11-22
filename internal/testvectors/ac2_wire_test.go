package testvectors

import (
	"os"
	"testing"
)

// AC-2: The loader exposes every top-level section of wire-v1.json:
// contract, actor, clock_by_batch, encoding_vector, proposals,
// request_digests, events, expected, legacy, independent_case_branches and binding.

func TestAC2_WireTopLevelSections(t *testing.T) {
	defer guard(t)

	w := Wire()
	if w == nil {
		t.Fatal("Wire() returned nil")
	}

	// 1. contract
	if w.Contract != "purser.kernel/v1" {
		t.Errorf("w.Contract = %q, want %q", w.Contract, "purser.kernel/v1")
	}

	// 2. actor
	if w.Actor != "principal:local" {
		t.Errorf("w.Actor = %q, want %q", w.Actor, "principal:local")
	}

	// 3. clock_by_batch
	if len(w.ClockByBatch) != 3 {
		t.Errorf("len(w.ClockByBatch) = %d, want 3", len(w.ClockByBatch))
	} else {
		wantClocks := []string{
			"2026-11-18T12:00:00.000000Z",
			"2026-11-18T12:00:01.000000Z",
			"2026-11-18T12:00:02.000000Z",
		}
		for i, want := range wantClocks {
			if w.ClockByBatch[i] != want {
				t.Errorf("w.ClockByBatch[%d] = %q, want %q", i, w.ClockByBatch[i], want)
			}
		}
	}

	// 4. encoding_vector
	if w.EncodingVector.Value == nil {
		t.Error("w.EncodingVector.Value is nil")
	}
	if w.EncodingVector.CanonicalUTF8 == "" {
		t.Error("w.EncodingVector.CanonicalUTF8 is empty")
	}
	if len(w.EncodingVector.SHA256CanonicalUTF8) != 64 {
		t.Errorf("w.EncodingVector.SHA256CanonicalUTF8 len = %d, want 64", len(w.EncodingVector.SHA256CanonicalUTF8))
	}

	// 5. proposals
	if len(w.Proposals) != 3 {
		t.Errorf("len(w.Proposals) = %d, want 3", len(w.Proposals))
	}

	// 6. request_digests
	if len(w.RequestDigests) != 3 {
		t.Errorf("len(w.RequestDigests) = %d, want 3", len(w.RequestDigests))
	} else {
		for i, d := range w.RequestDigests {
			if len(d) != 64 {
				t.Errorf("request_digests[%d] len = %d, want 64", i, len(d))
			}
		}
	}

	// 7. events
	if len(w.Events) != 8 {
		t.Errorf("len(w.Events) = %d, want 8", len(w.Events))
	}

	// 8. expected
	if w.Expected.Init == nil {
		t.Error("w.Expected.Init is nil")
	}
	if w.Expected.InitialStatus == nil {
		t.Error("w.Expected.InitialStatus is nil")
	}
	if len(w.Expected.AdmissionReplies) != 3 {
		t.Errorf("len(w.Expected.AdmissionReplies) = %d, want 3", len(w.Expected.AdmissionReplies))
	}
	if len(w.Expected.StatusByRevision) != 3 {
		t.Errorf("len(w.Expected.StatusByRevision) = %d, want 3", len(w.Expected.StatusByRevision))
	}
	if w.Expected.SnapshotAt4 == nil {
		t.Error("w.Expected.SnapshotAt4 is nil")
	}
	if w.Expected.SnapshotAt8 == nil {
		t.Error("w.Expected.SnapshotAt8 is nil")
	}
	if w.Expected.WhyActiveCommitment == nil {
		t.Error("w.Expected.WhyActiveCommitment is nil")
	}
	if w.Expected.ReplayAsOf != "2026-11-18T12:00:04.000000Z" {
		t.Errorf("w.Expected.ReplayAsOf = %q, want %q", w.Expected.ReplayAsOf, "2026-11-18T12:00:04.000000Z")
	}
	if w.Expected.ReplayEqualsStatusAtRevision != 8 {
		t.Errorf("w.Expected.ReplayEqualsStatusAtRevision = %d, want 8", w.Expected.ReplayEqualsStatusAtRevision)
	}

	// 9. legacy
	if w.Legacy.Source != "synthetic first batch, CommitmentCreated event_version 0" {
		t.Errorf("w.Legacy.Source = %q, want %q", w.Legacy.Source, "synthetic first batch, CommitmentCreated event_version 0")
	}
	if len(w.Legacy.Events) != 2 {
		t.Errorf("len(w.Legacy.Events) = %d, want 2", len(w.Legacy.Events))
	}
	if len(w.Legacy.ExpectedProjectionHash) != 64 {
		t.Errorf("w.Legacy.ExpectedProjectionHash len = %d, want 64", len(w.Legacy.ExpectedProjectionHash))
	}
	if len(w.Legacy.ExpectedHeadHash) != 64 {
		t.Errorf("w.Legacy.ExpectedHeadHash len = %d, want 64", len(w.Legacy.ExpectedHeadHash))
	}

	// 10. independent_case_branches
	if len(w.IndependentCaseBranches) != 7 {
		t.Errorf("len(w.IndependentCaseBranches) = %d, want 7", len(w.IndependentCaseBranches))
	}

	// 11. binding
	if len(w.Binding) == 0 {
		t.Error("w.Binding is empty")
	}
	if actorID, _ := w.Binding["actor_id"].(string); actorID != "principal:local" {
		t.Errorf("w.Binding.actor_id = %q, want %q", actorID, "principal:local")
	}
}

func TestAC2_WireLegacyStatus(t *testing.T) {
	defer guard(t)

	checkpoints := LegacyStatus()
	if len(checkpoints) != 4 {
		t.Fatalf("len(LegacyStatus()) = %d, want 4", len(checkpoints))
	}

	for i, cp := range checkpoints {
		if cp.V != 1 {
			t.Errorf("checkpoint %d: V = %d, want 1", i, cp.V)
		}
		if len(cp.HeadHash) != 64 {
			t.Errorf("checkpoint %d: HeadHash len = %d, want 64", i, len(cp.HeadHash))
		}
		if len(cp.ProjectionHash) != 64 {
			t.Errorf("checkpoint %d: ProjectionHash len = %d, want 64", i, len(cp.ProjectionHash))
		}
		if cp.AsOf == "" {
			t.Errorf("checkpoint %d: AsOf is empty", i)
		}
		if cp.Answers == nil {
			t.Errorf("checkpoint %d: Answers is nil", i)
		}
	}
}

func TestAC2_LoaderResolvesFromSourcePath(t *testing.T) {
	defer guard(t)

	origWd, err := os.Getwd()
	if err != nil {
		t.Fatalf("getwd: %v", err)
	}
	tmp := t.TempDir()
	if err := os.Chdir(tmp); err != nil {
		t.Fatalf("chdir %s: %v", tmp, err)
	}
	defer func() {
		_ = os.Chdir(origWd)
	}()

	w := Wire()
	if w == nil {
		t.Fatal("Wire() returned nil from temp directory")
	}
	if w.Contract != "purser.kernel/v1" {
		t.Errorf("w.Contract = %q, want %q", w.Contract, "purser.kernel/v1")
	}

	raw := Raw("purser-v1/wire-v1.json")
	if len(raw) == 0 {
		t.Fatal("Raw(purser-v1/wire-v1.json) returned empty bytes from temp directory")
	}
}
