package store

import (
	"math/rand"
	"path/filepath"
	"reflect"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/lookmanrays/purser/internal/kernel/codec"
	"github.com/lookmanrays/purser/internal/kernel/projection"
)

func mustChecksum(t *testing.T, p projection.Projection) string {
	t.Helper()
	sum, err := p.Checksum()
	if err != nil {
		t.Fatalf("Checksum: %v", err)
	}
	return sum
}

// makeUniformStore populates a store where all events after the first edit the
// same commitment record, keeping projection size constant regardless of event count.
func makeUniformStore(t testing.TB, totalEvents int) *Store {
	t.Helper()
	dbPath := filepath.Join(t.TempDir(), "uniform.db")
	s, err := Open(dbPath, Options{
		Registry:    projection.Base(),
		BusyTimeout: 5 * time.Second,
	})
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	t.Cleanup(func() { _ = s.Close() })
	if _, err := s.Initialize(); err != nil {
		t.Fatalf("Initialize: %v", err)
	}

	clockTime := time.Date(2026, 11, 18, 12, 0, 0, 0, time.UTC)
	clock := func() time.Time { return clockTime }

	firstBatch := true
	remaining := totalEvents
	chunkSize := 5000
	for remaining > 0 {
		batchSize := min(remaining, chunkSize)
		specs := make([]codec.EventSpec, 0, batchSize)
		if firstBatch {
			specs = append(specs, codec.EventSpec{
				Type:         "CommitmentCreated",
				EventVersion: 1,
				Data: map[string]any{
					"id":        "C-1",
					"root":      "W-1",
					"principal": "principal:local",
					"statement": "Initial statement",
				},
			})
			firstBatch = false
			batchSize--
		}
		for i := 0; i < batchSize; i++ {
			specs = append(specs, codec.EventSpec{
				Type:         "CommitmentEdited",
				EventVersion: 1,
				Data: map[string]any{
					"id":        "C-1",
					"statement": "Updated statement",
				},
			})
		}
		_, _, err := s.Transact("principal:local", "p-init", clock, func(Snapshot, time.Time) ([]codec.EventSpec, error) {
			return specs, nil
		})
		if err != nil {
			t.Fatalf("Transact: %v", err)
		}
		remaining -= len(specs)
	}
	return s
}

// AC-1 (baseline): After every wire vector batch, the materialized snapshot equals the from-scratch fold.
func TestAC1_SnapshotEqualsFoldAfterWireBatches(t *testing.T) {
	defer guard(t)

	s, w := newTestStore(t)
	if _, err := s.Initialize(); err != nil {
		t.Fatalf("Initialize: %v", err)
	}

	initSnap, err := s.Snapshot(nil)
	if err != nil {
		t.Fatalf("Snapshot(nil) at revision 0: %v", err)
	}
	emptyProj := projection.Empty()
	if mustChecksum(t, initSnap.Projection) != mustChecksum(t, emptyProj) {
		t.Errorf("init projection checksum = %q, want %q", mustChecksum(t, initSnap.Projection), mustChecksum(t, emptyProj))
	}

	reg := projection.Base()
	var allEvents []codec.Event
	for batchIdx := 0; batchIdx < len(w.ClockByBatch); batchIdx++ {
		snap, events := playBatch(t, s, batchIdx, w)
		allEvents = append(allEvents, events...)

		headSnap, err := s.Snapshot(nil)
		if err != nil {
			t.Fatalf("batch %d Snapshot(nil): %v", batchIdx, err)
		}
		if headSnap.Revision != snap.Revision {
			t.Errorf("batch %d Snapshot revision = %d, want %d", batchIdx, headSnap.Revision, snap.Revision)
		}
		if headSnap.HeadHash != snap.HeadHash {
			t.Errorf("batch %d Snapshot head_hash = %q, want %q", batchIdx, headSnap.HeadHash, snap.HeadHash)
		}

		fold := projection.Empty()
		for _, ev := range allEvents {
			fold, _, err = reg.Apply(fold, ev)
			if err != nil {
				t.Fatalf("Apply event rev %d: %v", ev.Revision, err)
			}
		}

		if mustChecksum(t, headSnap.Projection) != mustChecksum(t, fold) {
			t.Fatalf("batch %d projection checksum mismatch: snap=%q, fold=%q",
				batchIdx, mustChecksum(t, headSnap.Projection), mustChecksum(t, fold))
		}
		if !reflect.DeepEqual(headSnap.Projection.Map(), fold.Map()) {
			t.Fatalf("batch %d projection map mismatch", batchIdx)
		}
	}
}

// AC-2 (baseline): After 1,000 random valid batches, the materialized projection checksum equals the fold checksum.
func TestAC2_RandomBatchesChecksumEqualsFold(t *testing.T) {
	defer guard(t)

	dbPath := filepath.Join(t.TempDir(), "random.db")
	reg := projection.Base()
	s, err := Open(dbPath, Options{
		Registry:    reg,
		BusyTimeout: 5 * time.Second,
	})
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	t.Cleanup(func() { _ = s.Close() })
	if _, err := s.Initialize(); err != nil {
		t.Fatalf("Initialize: %v", err)
	}

	clockTime := time.Date(2026, 11, 18, 12, 0, 0, 0, time.UTC)
	clock := func() time.Time { return clockTime }

	rng := rand.New(rand.NewSource(42))

	var (
		commitments         []string
		proposedCommitments []string
		pendingApprovals    []string
		pendingReceipts     [][2]string
		nextCommitment      int
		nextApproval        int
		nextReceipt         int
	)

	approvalStates := []string{"approved", "denied", "expired", "stale"}
	finalDispositions := []string{"admitted", "rejected"}

	fold := projection.Empty()
	const totalBatches = 1000

	for batchIdx := 0; batchIdx < totalBatches; batchIdx++ {
		batchSize := 1 + rng.Intn(5)
		proposalID := "p-batch-" + strconv.Itoa(batchIdx)

		specs := make([]codec.EventSpec, 0, batchSize)

		// Include batches that create a commitment and edit or activate it in the same batch
		if len(commitments) == 0 || (batchSize >= 2 && rng.Intn(3) == 0) {
			cid := "C-" + strconv.Itoa(nextCommitment)
			wid := "W-" + strconv.Itoa(nextCommitment)
			nextCommitment++

			specs = append(specs, codec.EventSpec{
				Type:         "CommitmentCreated",
				EventVersion: 1,
				Data: map[string]any{
					"id":        cid,
					"root":      wid,
					"principal": "principal:local",
					"statement": "statement-" + strconv.Itoa(batchIdx) + "-0",
				},
			})
			commitments = append(commitments, cid)

			// Same-batch edit
			if len(specs) < batchSize {
				specs = append(specs, codec.EventSpec{
					Type:         "CommitmentEdited",
					EventVersion: 1,
					Data: map[string]any{
						"id":        cid,
						"statement": "statement-" + strconv.Itoa(batchIdx) + "-edit",
					},
				})
			}

			// Same-batch activation or approval
			if len(specs) < batchSize && rng.Intn(2) == 0 {
				specs = append(specs, codec.EventSpec{
					Type:         "CommitmentActivated",
					EventVersion: 1,
					Data: map[string]any{
						"id": cid,
					},
				})
			} else {
				proposedCommitments = append(proposedCommitments, cid)
			}
		}

		// Fill remaining slots in batch with valid M1 events
		for len(specs) < batchSize {
			actionChoices := []int{0}
			if len(commitments) > 0 {
				actionChoices = append(actionChoices, 1) // edit commitment
				actionChoices = append(actionChoices, 3) // request approval
			}
			if len(proposedCommitments) > 0 {
				actionChoices = append(actionChoices, 2) // activate commitment
			}
			if len(pendingApprovals) > 0 {
				actionChoices = append(actionChoices, 4) // resolve approval
			}
			actionChoices = append(actionChoices, 5) // create pending receipt
			if len(pendingReceipts) > 0 {
				actionChoices = append(actionChoices, 6) // resolve receipt
			}

			action := actionChoices[rng.Intn(len(actionChoices))]
			switch action {
			case 0: // CommitmentCreated
				cid := "C-" + strconv.Itoa(nextCommitment)
				wid := "W-" + strconv.Itoa(nextCommitment)
				nextCommitment++
				specs = append(specs, codec.EventSpec{
					Type:         "CommitmentCreated",
					EventVersion: 1,
					Data: map[string]any{
						"id":        cid,
						"root":      wid,
						"principal": "principal:local",
						"statement": "statement-" + strconv.Itoa(batchIdx) + "-" + strconv.Itoa(len(specs)),
					},
				})
				commitments = append(commitments, cid)
				proposedCommitments = append(proposedCommitments, cid)

			case 1: // CommitmentEdited
				cid := commitments[rng.Intn(len(commitments))]
				specs = append(specs, codec.EventSpec{
					Type:         "CommitmentEdited",
					EventVersion: 1,
					Data: map[string]any{
						"id":        cid,
						"statement": "statement-" + strconv.Itoa(batchIdx) + "-" + strconv.Itoa(len(specs)),
					},
				})

			case 2: // CommitmentActivated
				idx := rng.Intn(len(proposedCommitments))
				cid := proposedCommitments[idx]
				proposedCommitments = slices.Delete(proposedCommitments, idx, idx+1)
				specs = append(specs, codec.EventSpec{
					Type:         "CommitmentActivated",
					EventVersion: 1,
					Data: map[string]any{
						"id": cid,
					},
				})

			case 3: // ApprovalRequested
				cid := commitments[rng.Intn(len(commitments))]
				aid := "A-" + strconv.Itoa(nextApproval)
				nextApproval++
				expiry := clockTime.Add(24 * time.Hour)
				specs = append(specs, codec.EventSpec{
					Type:         "ApprovalRequested",
					EventVersion: 1,
					Data: map[string]any{
						"id":             aid,
						"requester":      "principal:local",
						"request":        map[string]any{"commitment": cid},
						"request_digest": codec.ZeroHash,
						"read_versions":  []any{map[string]any{"key": cid, "changed": int64(0)}},
						"expires_at":     codec.FormatTime(expiry),
					},
				})
				pendingApprovals = append(pendingApprovals, aid)

			case 4: // ApprovalResolved
				idx := rng.Intn(len(pendingApprovals))
				aid := pendingApprovals[idx]
				pendingApprovals = slices.Delete(pendingApprovals, idx, idx+1)
				st := approvalStates[rng.Intn(len(approvalStates))]
				specs = append(specs, codec.EventSpec{
					Type:         "ApprovalResolved",
					EventVersion: 1,
					Data: map[string]any{
						"id":    aid,
						"state": st,
					},
				})

			case 5: // ProposalReceipted (pending)
				pid := "p-rcpt-" + strconv.Itoa(nextReceipt)
				nextReceipt++
				specs = append(specs, codec.EventSpec{
					Type:         "ProposalReceipted",
					EventVersion: 1,
					Data: map[string]any{
						"actor_id":    "principal:local",
						"proposal_id": pid,
						"digest":      codec.ZeroHash,
						"disposition": "pending",
						"code":        "ok",
						"details":     map[string]any{},
					},
				})
				pendingReceipts = append(pendingReceipts, [2]string{"principal:local", pid})

			case 6: // ProposalReceipted (final disposition)
				idx := rng.Intn(len(pendingReceipts))
				rcpt := pendingReceipts[idx]
				pendingReceipts = slices.Delete(pendingReceipts, idx, idx+1)
				disp := finalDispositions[rng.Intn(len(finalDispositions))]
				specs = append(specs, codec.EventSpec{
					Type:         "ProposalReceipted",
					EventVersion: 1,
					Data: map[string]any{
						"actor_id":    rcpt[0],
						"proposal_id": rcpt[1],
						"digest":      codec.ZeroHash,
						"disposition": disp,
						"code":        "ok",
						"details":     map[string]any{},
					},
				})
			}
		}

		snap, events, err := s.Transact("principal:local", proposalID, clock, func(Snapshot, time.Time) ([]codec.EventSpec, error) {
			return specs, nil
		})
		if err != nil {
			t.Fatalf("batch %d Transact: %v", batchIdx, err)
		}

		for _, ev := range events {
			fold, _, err = reg.Apply(fold, ev)
			if err != nil {
				t.Fatalf("batch %d Apply event rev %d: %v", batchIdx, ev.Revision, err)
			}
		}

		headSnap, err := s.Snapshot(nil)
		if err != nil {
			t.Fatalf("batch %d Snapshot(nil): %v", batchIdx, err)
		}
		if headSnap.Revision != snap.Revision {
			t.Fatalf("batch %d Snapshot revision %d != snap revision %d", batchIdx, headSnap.Revision, snap.Revision)
		}
		if mustChecksum(t, headSnap.Projection) != mustChecksum(t, fold) {
			t.Fatalf("batch %d projection checksum mismatch: snap=%q, fold=%q",
				batchIdx, mustChecksum(t, headSnap.Projection), mustChecksum(t, fold))
		}
	}
}

// AC-3 (baseline): A head-hash mismatch fails with integrity_error.
func TestAC3_HeadHashMismatchIntegrityError(t *testing.T) {
	defer guard(t)

	s, w := newTestStore(t)
	if _, err := s.Initialize(); err != nil {
		t.Fatalf("Initialize: %v", err)
	}

	snap, _ := playBatch(t, s, 0, w)
	if snap.Revision != 2 {
		t.Fatalf("expected revision 2, got %d", snap.Revision)
	}

	// Corrupt meta.head_hash to ZeroHash (does not match the last event's hash)
	execStoreSQL(t, s, "UPDATE meta SET head_hash=?", codec.ZeroHash)

	_, err := s.Snapshot(nil)
	if err == nil {
		t.Fatal("Snapshot(nil) succeeded with corrupted meta.head_hash, want integrity_error")
	}
	if !hasCode(err, "integrity_error") {
		t.Fatalf("expected integrity_error, got: %v", err)
	}

	// Corrupt meta.head_hash to a mismatched non-zero hash
	mismatch := strings.Repeat("0", 63) + "1"
	execStoreSQL(t, s, "UPDATE meta SET head_hash=?", mismatch)

	_, err = s.Snapshot(nil)
	if err == nil {
		t.Fatal("Snapshot(nil) succeeded with mismatched meta.head_hash, want integrity_error")
	}
	if !hasCode(err, "integrity_error") {
		t.Fatalf("expected integrity_error, got: %v", err)
	}
}

// AC-4: With the same final projection, a head snapshot over 10,000 events takes at most
// twice as long as over 1,000 events; the benchmark applies the same rule at 100,000 events under benchcheck.
func TestAC4_HeadSnapshotTimingRatio(t *testing.T) {
	defer guard(t)

	s1k := makeUniformStore(t, 1000)
	s10k := makeUniformStore(t, 10000)

	snap1k, err := s1k.Snapshot(nil)
	if err != nil {
		t.Fatalf("s1k.Snapshot(nil): %v", err)
	}
	snap10k, err := s10k.Snapshot(nil)
	if err != nil {
		t.Fatalf("s10k.Snapshot(nil): %v", err)
	}

	// Verify both stores reached the exact same final projection size (2 records)
	if len(snap1k.Projection.Records) != len(snap10k.Projection.Records) {
		t.Fatalf("records mismatch: 1k has %d, 10k has %d",
			len(snap1k.Projection.Records), len(snap10k.Projection.Records))
	}
	if len(snap1k.Projection.Receipts) != len(snap10k.Projection.Receipts) {
		t.Fatalf("receipts mismatch: 1k has %d, 10k has %d",
			len(snap1k.Projection.Receipts), len(snap10k.Projection.Receipts))
	}
	if len(snap1k.Projection.Edges) != len(snap10k.Projection.Edges) {
		t.Fatalf("edges mismatch: 1k has %d, 10k has %d",
			len(snap1k.Projection.Edges), len(snap10k.Projection.Edges))
	}

	// Measure repeated head reads across multiple samples
	// Time a loop of 25 reads per sample and take the median of 9 samples.
	const samples = 9
	const readsPerSample = 25
	times1k := make([]time.Duration, samples)
	times10k := make([]time.Duration, samples)

	for i := 0; i < samples; i++ {
		start := time.Now()
		for r := 0; r < readsPerSample; r++ {
			if _, err := s1k.Snapshot(nil); err != nil {
				t.Fatalf("sample %d read %d s1k.Snapshot: %v", i, r, err)
			}
		}
		times1k[i] = time.Since(start)

		start = time.Now()
		for r := 0; r < readsPerSample; r++ {
			if _, err := s10k.Snapshot(nil); err != nil {
				t.Fatalf("sample %d read %d s10k.Snapshot: %v", i, r, err)
			}
		}
		times10k[i] = time.Since(start)
	}

	slices.Sort(times1k)
	slices.Sort(times10k)
	median1k := times1k[samples/2]
	median10k := times10k[samples/2]

	ratio := float64(median10k) / float64(median1k)
	if ratio > 2.0 {
		t.Fatalf("head snapshot over 10,000 events took %.2fx as long as over 1,000 events (want <= 2.0); median 1k=%v, 10k=%v",
			ratio, median1k, median10k)
	}
}

// AC-5 (baseline): A historical snapshot at a mid-batch revision fails with bad_revision.
func TestAC5_HistoricalSnapshotMidBatchBadRevision(t *testing.T) {
	defer guard(t)

	s, w := newTestStore(t)
	if _, err := s.Initialize(); err != nil {
		t.Fatalf("Initialize: %v", err)
	}

	// Batch 0 has 2 events, covering revisions 1 and 2.
	playBatch(t, s, 0, w)

	// Mid-batch revision (revision 1) must fail with bad_revision
	midRev := int64(1)
	_, err := s.Snapshot(&midRev)
	if err == nil {
		t.Fatal("Snapshot(&midRev) succeeded for mid-batch revision 1, want bad_revision")
	}
	if !hasCode(err, "bad_revision") {
		t.Fatalf("expected bad_revision for revision 1, got: %v", err)
	}

	// Batch boundary revision (revision 2) must succeed
	boundRev := int64(2)
	snap2, err := s.Snapshot(&boundRev)
	if err != nil {
		t.Fatalf("Snapshot(&boundRev) failed at batch boundary: %v", err)
	}
	if snap2.Revision != 2 {
		t.Errorf("revision = %d, want 2", snap2.Revision)
	}

	// Revision 0 (empty store state) must succeed
	revZero := int64(0)
	snap0, err := s.Snapshot(&revZero)
	if err != nil {
		t.Fatalf("Snapshot(&revZero) failed: %v", err)
	}
	if snap0.Revision != 0 {
		t.Errorf("revision = %d, want 0", snap0.Revision)
	}

	// Negative revision must fail with bad_revision
	negRev := int64(-1)
	_, err = s.Snapshot(&negRev)
	if !hasCode(err, "bad_revision") {
		t.Fatalf("expected bad_revision for revision -1, got: %v", err)
	}

	// Revision beyond head must fail with bad_revision
	futureRev := int64(999)
	_, err = s.Snapshot(&futureRev)
	if !hasCode(err, "bad_revision") {
		t.Fatalf("expected bad_revision for revision 999, got: %v", err)
	}
}
