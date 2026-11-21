package codec

import (
	"errors"
	"path/filepath"
	"testing"
)

// Reference-parity regression tests, added after the P1-01 bake-off. The
// fixtures are generated from reference/python (see their _comment fields):
// the codec must accept, reject and re-encode exactly as purser-main does,
// and verify batches by the same rule as purser-main's store.

// TestRef_DecodeEncodeParity re-encodes reference-generated documents and
// checks the reference's accept/reject decision on edge-case probes,
// including stray closing brackets after the top-level value.
func TestRef_DecodeEncodeParity(t *testing.T) {
	defer guard(t)
	fixture := readJSON(t, filepath.Join("testdata", "reference_parity.json")).(map[string]any)

	for i, raw := range fixture["documents"].([]any) {
		doc := raw.(map[string]any)
		v, err := Decode([]byte(doc["raw"].(string)))
		if err != nil {
			t.Fatalf("document %d: Decode(%q): %v", i, doc["raw"], err)
		}
		got, err := Encode(v)
		if err != nil {
			t.Fatalf("document %d: Encode: %v", i, err)
		}
		if string(got) != doc["canonical"].(string) {
			t.Fatalf("document %d: canonical bytes differ\n got: %q\nwant: %q", i, got, doc["canonical"])
		}
	}

	for _, raw := range fixture["probes"].([]any) {
		probe := raw.(map[string]any)
		in := probe["raw"].(string)
		t.Run(in, func(t *testing.T) {
			defer guard(t)
			v, err := Decode([]byte(in))
			if !probe["ok"].(bool) {
				if err == nil {
					t.Fatalf("Decode(%q) = %#v; the reference rejects it", in, v)
				}
				return
			}
			if err != nil {
				t.Fatalf("Decode(%q): %v; the reference accepts it", in, err)
			}
			got, err := Encode(v)
			if err != nil || string(got) != probe["canonical"].(string) {
				t.Fatalf("Encode(Decode(%q)) = %q, %v; want %q", in, got, err, probe["canonical"])
			}
		})
	}
}

// TestRef_StrayClosingBracketIsTrailingData pins the error code for the
// defect found in the bake-off.
func TestRef_StrayClosingBracketIsTrailingData(t *testing.T) {
	defer guard(t)
	for _, in := range []string{`1 ]`, `{} }`, `[1] ]`, `[1,2]]`, `[]]`} {
		_, err := Decode([]byte(in))
		requireCode(t, err, "trailing_data")
	}
}

// TestRef_VerifyChainBatchFields: every event of a batch must agree on
// batch_start, batch_size, at, actor_id and proposal_id, and a batch must end
// within the integer range (reference store, _BATCH and MAX_INTEGER).
func TestRef_VerifyChainBatchFields(t *testing.T) {
	defer guard(t)
	fixture := readJSON(t, filepath.Join("testdata", "chains_batch.json")).(map[string]any)
	for _, raw := range fixture["cases"].([]any) {
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
			requireCode(t, err, c["want_code"].(string))
			var ce *Error
			if !errors.As(err, &ce) {
				t.Fatalf("error is not a *Error: %v", err)
			}
			if want := c["want_revision"].(int64); ce.Revision != want {
				t.Fatalf("error revision %d, want %d", ce.Revision, want)
			}
		})
	}
}
