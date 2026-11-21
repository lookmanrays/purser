package codec

import (
	"errors"
	"path/filepath"
	"testing"
)

// AC-5: VerifyChain accepts the wire chain and returns the exact revision of a
// corrupted hash, a gap or a wrong batch field.
//
// testdata/chains.json is generated from the reference implementation
// (reference/python, wire.digest): corrupted chains are re-hashed so that only
// the intended check fails. Per event, in stored order, the checks run in the
// contract's order: own hash, revision contiguity, prev_hash link, batch fields.
// A batch is defined by its first event; a later event whose batch fields
// differ is the bad revision. The reported Revision is the `revision` field of
// the first event that fails. When want_revision is null only the code is
// asserted (a truncated final batch).
func TestAC5_VerifyChain(t *testing.T) {
	defer guard(t)
	fixture := readJSON(t, filepath.Join("testdata", "chains.json")).(map[string]any)
	cases := fixture["cases"].([]any)
	if len(cases) == 0 {
		t.Fatal("no chain cases")
	}
	for _, raw := range cases {
		c := raw.(map[string]any)
		t.Run(c["name"].(string), func(t *testing.T) {
			defer guard(t)
			var events []Event
			for _, e := range c["events"].([]any) {
				ev, err := ParseEvent(rawJSON(t, e))
				if err != nil {
					t.Fatalf("ParseEvent: %v", err)
				}
				events = append(events, ev)
			}
			err := VerifyChain(events, c["prev_hash"].(string), c["next_revision"].(int64))
			wantCode, _ := c["want_code"].(string)
			if wantCode == "" {
				if err != nil {
					t.Fatalf("VerifyChain: unexpected error %v", err)
				}
				return
			}
			requireCode(t, err, wantCode)
			if wantRev, ok := c["want_revision"].(int64); ok {
				var ce *Error
				if !errors.As(err, &ce) {
					t.Fatalf("error is not a *Error: %v", err)
				}
				if ce.Revision != wantRev {
					t.Fatalf("error revision %d, want %d (%v)", ce.Revision, wantRev, err)
				}
			}
		})
	}
}

// AC-5: the zero hash constant is 64 zeroes and the empty chain is valid.
func TestAC5_EmptyChainAndZeroHash(t *testing.T) {
	defer guard(t)
	if len(ZeroHash) != 64 {
		t.Fatalf("ZeroHash has length %d", len(ZeroHash))
	}
	for _, c := range ZeroHash {
		if c != '0' {
			t.Fatalf("ZeroHash = %q", ZeroHash)
		}
	}
	if err := VerifyChain(nil, ZeroHash, 1); err != nil {
		t.Fatalf("VerifyChain(empty): %v", err)
	}
}
