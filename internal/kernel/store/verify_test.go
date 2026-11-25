package store

import (
	"bytes"
	"errors"
	"path/filepath"
	"testing"

	"github.com/lookmanrays/purser/internal/kernel/codec"
	"github.com/lookmanrays/purser/internal/testvectors"
)

type installedState struct {
	metaRev  int64
	metaHash string
	records  []kvRow
	receipts []kvRow
	edges    []kvRow
}

type kvRow struct {
	key  string
	body []byte
}

func readInstalledState(t *testing.T, s *Store) installedState {
	t.Helper()
	var state installedState
	err := s.db.QueryRow("SELECT head_revision, head_hash FROM meta WHERE id = 1").Scan(&state.metaRev, &state.metaHash)
	if err != nil {
		t.Fatalf("read meta: %v", err)
	}

	readTable := func(query string) []kvRow {
		rows, err := s.db.Query(query)
		if err != nil {
			t.Fatalf("query %s: %v", query, err)
		}
		defer func() { _ = rows.Close() }()
		var out []kvRow
		for rows.Next() {
			var k string
			var b []byte
			if err := rows.Scan(&k, &b); err != nil {
				t.Fatalf("scan row: %v", err)
			}
			out = append(out, kvRow{key: k, body: b})
		}
		if err := rows.Err(); err != nil {
			t.Fatalf("rows err: %v", err)
		}
		return out
	}

	state.records = readTable("SELECT id, body FROM records ORDER BY id")
	state.receipts = readTable("SELECT actor_id || '/' || proposal_id, body FROM receipts ORDER BY actor_id, proposal_id")
	state.edges = readTable("SELECT kind || '/' || from_id || '/' || to_id, body FROM edges ORDER BY kind, from_id, to_id")
	return state
}

func requireEqualInstalledState(t *testing.T, got, want installedState, phase string) {
	t.Helper()
	if got.metaRev != want.metaRev || got.metaHash != want.metaHash {
		t.Fatalf("%s meta changed: got (rev=%d, hash=%s), want (rev=%d, hash=%s)",
			phase, got.metaRev, got.metaHash, want.metaRev, want.metaHash)
	}
	compareKV := func(table string, g, w []kvRow) {
		if len(g) != len(w) {
			t.Fatalf("%s table %s row count changed: got %d, want %d", phase, table, len(g), len(w))
		}
		for i := range g {
			if g[i].key != w[i].key || !bytes.Equal(g[i].body, w[i].body) {
				t.Fatalf("%s table %s row %d changed: got key=%q, want key=%q", phase, table, i, g[i].key, w[i].key)
			}
		}
	}
	compareKV("records", got.records, want.records)
	compareKV("receipts", got.receipts, want.receipts)
	compareKV("edges", got.edges, want.edges)
}

func rewriteRawEventAt(t *testing.T, s *Store, revision int64, mutate func([]byte) []byte) {
	t.Helper()
	var raw []byte
	if err := s.db.QueryRow("SELECT body FROM events WHERE revision = ?", revision).Scan(&raw); err != nil {
		t.Fatalf("read raw event: %v", err)
	}
	raw = mutate(raw)
	if _, err := s.db.Exec("UPDATE events SET body = ? WHERE revision = ?", raw, revision); err != nil {
		t.Fatalf("write raw event: %v", err)
	}
}

func requireStoreErr(t *testing.T, err error, code string, revision int64) {
	t.Helper()
	var detail *codec.Error
	if !errors.As(err, &detail) || detail.Code != code || detail.Revision != revision {
		t.Fatalf("error = %v, want %s at revision %d", err, code, revision)
	}
}

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
			s := openStoreForReplayTest(t, path)
			if _, err := s.Initialize(); err != nil {
				t.Fatalf("Initialize: %v", err)
			}
			playBatch(t, s, 0, w)
			playBatch(t, s, 1, w)

			stateBefore := readInstalledState(t, s)

			// Flip one byte at revision N in the raw stored event body
			rewriteRawEventAt(t, s, tc.revision, func(raw []byte) []byte {
				mutated := bytes.Clone(raw)
				mutated[len(mutated)/2] ^= 0x01
				return mutated
			})

			// Verify must fail with integrity_error naming N
			errVerify := s.Verify(nil)
			requireStoreErr(t, errVerify, "integrity_error", tc.revision)

			// Verify writes nothing: installed rows remain byte-identical
			stateAfterVerify := readInstalledState(t, s)
			requireEqualInstalledState(t, stateAfterVerify, stateBefore, "after Verify")

			// Replay must fail with integrity_error naming N
			_, errReplay := s.Replay(nil)
			requireStoreErr(t, errReplay, "integrity_error", tc.revision)

			// Replay failure rolls back: installed rows remain byte-identical
			stateAfterReplay := readInstalledState(t, s)
			requireEqualInstalledState(t, stateAfterReplay, stateBefore, "after Replay")
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

	t.Run("MissingSuffixDetectedByTrustedHead", func(t *testing.T) {
		defer guard(t)

		// Store A plays batch 0 only (revisions 1..2), which is a consistent prefix.
		sA, _ := newTestStore(t)
		if _, err := sA.Initialize(); err != nil {
			t.Fatalf("Initialize sA: %v", err)
		}
		playBatch(t, sA, 0, w)

		// Store B plays batches 0 and 1 (revisions 1..4).
		sB, _ := newTestStore(t)
		if _, err := sB.Initialize(); err != nil {
			t.Fatalf("Initialize sB: %v", err)
		}
		playBatch(t, sB, 0, w)
		snapB, _ := playBatch(t, sB, 1, w)

		trusted := &TrustedHead{
			Revision: snapB.Revision,
			Hash:     snapB.HeadHash,
		}

		// An unanchored prefix passes Verify(nil)
		if err := sA.Verify(nil); err != nil {
			t.Fatalf("sA.Verify(nil) on consistent prefix = %v, want nil", err)
		}

		stateBefore := readInstalledState(t, sA)

		// But with trusted head (from B), missing suffix is detected:
		errVerify := sA.Verify(trusted)
		if !hasCode(errVerify, "integrity_error") {
			t.Fatalf("sA.Verify(trusted) = %v, want integrity_error", errVerify)
		}
		stateAfterVerify := readInstalledState(t, sA)
		requireEqualInstalledState(t, stateAfterVerify, stateBefore, "after Verify(trusted)")

		_, errReplay := sA.Replay(trusted)
		if !hasCode(errReplay, "integrity_error") {
			t.Fatalf("sA.Replay(trusted) = %v, want integrity_error", errReplay)
		}
		stateAfterReplay := readInstalledState(t, sA)
		requireEqualInstalledState(t, stateAfterReplay, stateBefore, "after Replay(trusted)")
	})

	t.Run("MatchingTrustedHeadPasses", func(t *testing.T) {
		defer guard(t)

		s, _ := newTestStore(t)
		if _, err := s.Initialize(); err != nil {
			t.Fatalf("Initialize: %v", err)
		}
		playBatch(t, s, 0, w)
		snap, _ := playBatch(t, s, 1, w)

		trusted := &TrustedHead{
			Revision: snap.Revision,
			Hash:     snap.HeadHash,
		}

		if err := s.Verify(trusted); err != nil {
			t.Fatalf("Verify(trusted) matching failed: %v", err)
		}

		replayed, err := s.Replay(trusted)
		if err != nil {
			t.Fatalf("Replay(trusted) matching failed: %v", err)
		}
		if replayed.Revision != trusted.Revision || replayed.HeadHash != trusted.Hash {
			t.Fatalf("Replay = (rev=%d, hash=%s), want (rev=%d, hash=%s)",
				replayed.Revision, replayed.HeadHash, trusted.Revision, trusted.Hash)
		}
	})

	t.Run("MismatchedHeadHashFails", func(t *testing.T) {
		defer guard(t)

		s, _ := newTestStore(t)
		if _, err := s.Initialize(); err != nil {
			t.Fatalf("Initialize: %v", err)
		}
		playBatch(t, s, 0, w)
		snap, _ := playBatch(t, s, 1, w)

		trustedMismatch := &TrustedHead{
			Revision: snap.Revision,
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

		s, _ := newTestStore(t)
		if _, err := s.Initialize(); err != nil {
			t.Fatalf("Initialize: %v", err)
		}
		playBatch(t, s, 0, w)
		playBatch(t, s, 1, w)

		// Delete only revision 4, leaving batch 1 incomplete (only revision 3 present)
		execSQL(t, s, "DELETE FROM events WHERE revision = 4")

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
