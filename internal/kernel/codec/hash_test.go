package codec

import (
	"reflect"
	"testing"
)

func vectorEvents(t *testing.T, section string) []map[string]any {
	t.Helper()
	vec := wireVector(t)
	var list []any
	if section == "legacy" {
		list = vec["legacy"].(map[string]any)["events"].([]any)
	} else {
		list = vec["events"].([]any)
	}
	out := make([]map[string]any, len(list))
	for i, e := range list {
		out[i] = e.(map[string]any)
	}
	return out
}

// AC-2: Hash("event", event without hash) reproduces every stored event hash
// in wire-v1.json, including the legacy events.
func TestAC2_EventHashesMatchVectors(t *testing.T) {
	defer guard(t)
	for _, section := range []string{"events", "legacy"} {
		for _, e := range vectorEvents(t, section) {
			body := copyMap(e)
			want := body["hash"].(string)
			delete(body, "hash")
			got, err := Hash(TagEvent, body)
			if err != nil {
				t.Fatalf("%s revision %v: Hash: %v", section, e["revision"], err)
			}
			if got != want {
				t.Fatalf("%s revision %v: hash %s, want %s", section, e["revision"], got, want)
			}
		}
	}
}

// AC-2: Hash("proposal", {actor_id, proposal}) reproduces every request digest.
func TestAC2_RequestDigestsMatchVectors(t *testing.T) {
	defer guard(t)
	vec := wireVector(t)
	proposals := vec["proposals"].([]any)
	digests := vec["request_digests"].([]any)
	if len(proposals) != len(digests) {
		t.Fatalf("fixture mismatch: %d proposals, %d digests", len(proposals), len(digests))
	}
	for i := range proposals {
		got, err := Hash(TagProposal, map[string]any{"actor_id": vec["actor"], "proposal": proposals[i]})
		if err != nil {
			t.Fatalf("proposal %d: Hash: %v", i, err)
		}
		if got != digests[i].(string) {
			t.Fatalf("proposal %d: digest %s, want %s", i, got, digests[i])
		}
	}
}

// AC-2: hashes are lowercase hex and depend on the tag.
func TestAC2_HashFormatAndTagSeparation(t *testing.T) {
	defer guard(t)
	v := map[string]any{"a": int64(1)}
	seen := map[string]Tag{}
	for _, tag := range []Tag{TagProposal, TagEvent, TagProjection, TagOutcome, TagEffect} {
		h, err := Hash(tag, v)
		if err != nil {
			t.Fatalf("Hash(%s): %v", tag, err)
		}
		if len(h) != 64 {
			t.Fatalf("Hash(%s) length %d, want 64", tag, len(h))
		}
		for _, c := range h {
			if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f') {
				t.Fatalf("Hash(%s) = %s is not lowercase hex", tag, h)
			}
		}
		if other, dup := seen[h]; dup {
			t.Fatalf("tags %s and %s produce the same hash", other, tag)
		}
		seen[h] = tag
	}
	if _, err := Hash(TagEvent, 1.5); codeOf(err) != "invalid_value" {
		t.Fatalf("Hash of a float: want invalid_value, got %v", err)
	}
}

// AC-2: ParseEvent decodes every vector event; ComputeHash, Body and Map agree
// with the stored data.
func TestAC2_ParseEventAndComputeHash(t *testing.T) {
	defer guard(t)
	for _, section := range []string{"events", "legacy"} {
		for _, e := range vectorEvents(t, section) {
			ev, err := ParseEvent(rawJSON(t, e))
			if err != nil {
				t.Fatalf("%s revision %v: ParseEvent: %v", section, e["revision"], err)
			}
			if ev.Revision != e["revision"].(int64) || ev.Type != e["type"].(string) || ev.Hash != e["hash"].(string) ||
				ev.PrevHash != e["prev_hash"].(string) || ev.EventVersion != e["event_version"].(int64) ||
				ev.BatchStart != e["batch_start"].(int64) || ev.BatchSize != e["batch_size"].(int64) ||
				ev.ActorID != e["actor_id"].(string) || ev.ProposalID != e["proposal_id"].(string) ||
				ev.At != e["at"].(string) || ev.V != e["v"].(int64) {
				t.Fatalf("%s revision %v: fields not decoded as stored: %+v", section, e["revision"], ev)
			}
			if !reflect.DeepEqual(ev.Data, e["data"]) {
				t.Fatalf("%s revision %v: data %v, want %v", section, e["revision"], ev.Data, e["data"])
			}
			if !reflect.DeepEqual(ev.Map(), e) {
				t.Fatalf("%s revision %v: Map() = %v, want %v", section, e["revision"], ev.Map(), e)
			}
			body := ev.Body()
			if _, has := body["hash"]; has || len(body) != len(e)-1 {
				t.Fatalf("%s revision %v: Body() must be the event without hash, got %v", section, e["revision"], body)
			}
			h, err := ev.ComputeHash()
			if err != nil || h != ev.Hash {
				t.Fatalf("%s revision %v: ComputeHash = %s, %v; want %s", section, e["revision"], h, err, ev.Hash)
			}
		}
	}
}

// AC-2: ParseEvent enforces the closed twelve-field set and field types.
func TestAC2_ParseEventRejectsMalformedEvents(t *testing.T) {
	defer guard(t)
	base := vectorEvents(t, "events")[0]
	mutate := func(f func(m map[string]any)) []byte {
		m := copyMap(base)
		f(m)
		return rawJSON(t, m)
	}
	cases := []struct {
		name string
		raw  []byte
		code string
	}{
		{"missing at", mutate(func(m map[string]any) { delete(m, "at") }), "bad_event"},
		{"missing hash", mutate(func(m map[string]any) { delete(m, "hash") }), "bad_event"},
		{"extra field", mutate(func(m map[string]any) { m["extra"] = true }), "bad_event"},
		{"revision as string", mutate(func(m map[string]any) { m["revision"] = "1" }), "bad_event"},
		{"data as array", mutate(func(m map[string]any) { m["data"] = []any{} }), "bad_event"},
		{"type as null", mutate(func(m map[string]any) { m["type"] = nil }), "bad_event"},
		{"not an object", []byte(`[1,2]`), "bad_event"},
		{"duplicate key", []byte(`{"v":1,"v":1}`), "duplicate_key"},
		{"invalid JSON", []byte(`{"v":`), "invalid_json"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			defer guard(t)
			_, err := ParseEvent(c.raw)
			requireCode(t, err, c.code)
		})
	}
}
