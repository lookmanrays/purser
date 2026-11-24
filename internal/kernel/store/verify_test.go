package store

import (
	"bytes"
	"path/filepath"
	"testing"

	"github.com/lookmanrays/purser/internal/kernel/codec"
	"github.com/lookmanrays/purser/internal/testvectors"
)

// AC-2: One flipped byte at revision N fails with integrity_error naming N, and the installed projection is unchanged.
func TestAC2_FlippedByteFailsWithIntegrityErrorAtRevision(t *testing.T) {
	defer guard(t)

	w := testvectors.Wire()
	if w == nil {
		t.Fatal("Wire vector is nil")
	}

	tests := []struct {
		name     string
		revision int64
	}{
		{"Revision1", 1},
		{"Revision2", 2},
		{"Revision3", 3},
		{"Revision4", 4},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			defer guard(t)

			path := filepath.Join(t.TempDir(), "store.db")
			s := openStoreForTest(t, path, Options{})
			if _, err := s.Initialize(); err != nil {
				t.Fatalf("Initialize: %v", err)
			}
			playBatch(t, s, 0, w)
			playBatch(t, s, 1, w)

			snapBefore, err := s.Snapshot(nil)
			if err != nil {
				t.Fatalf("Snapshot before mutation: %v", err)
			}
			sumBefore, err := snapBefore.Projection.Checksum()
			if err != nil {
				t.Fatalf("Checksum before mutation: %v", err)
			}

			// Flip one byte at revision N in the raw stored event body
			rewriteRawEvent(t, s, tc.revision, func(raw []byte) []byte {
				mutated := bytes.Clone(raw)
				mutated[len(mutated)/2] ^= 0x01
				return mutated
			})

			// Verify must fail with integrity_error naming N
			errVerify := s.Verify(nil)
			requireStoreError(t, errVerify, "integrity_error", tc.revision)

			// Replay must fail with integrity_error naming N
			_, errReplay := s.Replay(nil)
			requireStoreError(t, errReplay, "integrity_error", tc.revision)

			// The installed projection must remain completely unchanged
			snapAfter, err := s.Snapshot(nil)
			if err != nil {
				t.Fatalf("Snapshot after failed replay: %v", err)
			}
			if snapAfter.Revision != snapBefore.Revision {
				t.Fatalf("Revision changed: got %d, want %d", snapAfter.Revision, snapBefore.Revision)
			}
			if snapAfter.HeadHash != snapBefore.HeadHash {
				t.Fatalf("HeadHash changed: got %s, want %s", snapAfter.HeadHash, snapBefore.HeadHash)
			}
			sumAfter, err := snapAfter.Projection.Checksum()
			if err != nil {
				t.Fatalf("Checksum after failed replay: %v", err)
			}
			if sumAfter != sumBefore {
				t.Fatalf("Projection checksum changed: got %s, want %s", sumAfter, sumBefore)
			}
		})
	}
}

// AC-3: With a trusted head, a missing final batch is detected.
func TestAC3_TrustedHeadDetectsMissingFinalBatch(t *testing.T) {
	defer guard(t)

	w := testvectors.Wire()
	if w == nil {
		t.Fatal("Wire vector is nil")
	}

	setupTwoBatches := func(t *testing.T) (*Store, Snapshot) {
		t.Helper()
		s, _ := newTestStore(t)
		if _, err := s.Initialize(); err != nil {
			t.Fatalf("Initialize: %v", err)
		}
		playBatch(t, s, 0, w)
		snap, _ := playBatch(t, s, 1, w)
		return s, snap
	}

	t.Run("MissingFinalBatchDetected", func(t *testing.T) {
		defer guard(t)

		s, headSnap := setupTwoBatches(t)
		trusted := &TrustedHead{
			Revision: headSnap.Revision,
			Hash:     headSnap.HeadHash,
		}

		// Delete the final batch (revisions 3 and 4) from the events table
		execStoreSQL(t, s, "DELETE FROM events WHERE revision > 2")

		errVerify := s.Verify(trusted)
		if !hasCode(errVerify, "integrity_error") {
			t.Fatalf("Verify(trusted) on truncated log = %v, want integrity_error", errVerify)
		}

		_, errReplay := s.Replay(trusted)
		if !hasCode(errReplay, "integrity_error") {
			t.Fatalf("Replay(trusted) on truncated log = %v, want integrity_error", errReplay)
		}
	})

	t.Run("MatchingTrustedHeadPasses", func(t *testing.T) {
		defer guard(t)

		s, headSnap := setupTwoBatches(t)
		trusted := &TrustedHead{
			Revision: headSnap.Revision,
			Hash:     headSnap.HeadHash,
		}

		if err := s.Verify(trusted); err != nil {
			t.Fatalf("Verify(trusted) matching failed: %v", err)
		}

		snap, err := s.Replay(trusted)
		if err != nil {
			t.Fatalf("Replay(trusted) matching failed: %v", err)
		}
		if snap.Revision != trusted.Revision || snap.HeadHash != trusted.Hash {
			t.Fatalf("Replay = (rev=%d, hash=%s), want (rev=%d, hash=%s)",
				snap.Revision, snap.HeadHash, trusted.Revision, trusted.Hash)
		}
	})

	t.Run("MismatchedHeadHashFails", func(t *testing.T) {
		defer guard(t)

		s, headSnap := setupTwoBatches(t)
		trustedMismatch := &TrustedHead{
			Revision: headSnap.Revision,
			Hash:     codec.ZeroHash,
		}

		errVerify := s.Verify(trustedMismatch)
		if !hasCode(errVerify, "integrity_error") {
			t.Fatalf("Verify with mismatched hash = %v, want integrity_error", errVerify)
		}

		_, errReplay := s.Replay(trustedMismatch)
		if !hasCode(errReplay, "integrity_error") {
			t.Fatalf("Replay with mismatched hash = %v, want integrity_error", errReplay)
		}
	})

	t.Run("IncompleteBatchWithoutTrustedHeadFails", func(t *testing.T) {
		defer guard(t)

		s, _ := setupTwoBatches(t)

		// Delete only revision 4, leaving batch 1 incomplete (only revision 3 present)
		execStoreSQL(t, s, "DELETE FROM events WHERE revision = 4")

		errVerify := s.Verify(nil)
		if !hasCode(errVerify, "integrity_error") {
			t.Fatalf("Verify on incomplete batch = %v, want integrity_error", errVerify)
		}

		_, errReplay := s.Replay(nil)
		if !hasCode(errReplay, "integrity_error") {
			t.Fatalf("Replay on incomplete batch = %v, want integrity_error", errReplay)
		}
	})
}
