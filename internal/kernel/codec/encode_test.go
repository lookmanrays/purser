package codec

import (
	"crypto/sha256"
	"encoding/hex"
	"testing"
)

// AC-1: Encode of the encoding vector equals canonical_utf8 byte for byte,
// and its SHA-256 matches.
func TestAC1_EncodeMatchesEncodingVector(t *testing.T) {
	defer guard(t)
	vec := wireVector(t)["encoding_vector"].(map[string]any)

	got, err := Encode(vec["value"])
	if err != nil {
		t.Fatalf("Encode: %v", err)
	}
	want := vec["canonical_utf8"].(string)
	if string(got) != want {
		t.Fatalf("canonical bytes differ\n got: %q\nwant: %q", got, want)
	}
	sum := sha256.Sum256(got)
	if h := hex.EncodeToString(sum[:]); h != vec["sha256_canonical_utf8"].(string) {
		t.Fatalf("sha256 = %s, want %s", h, vec["sha256_canonical_utf8"])
	}
}

// AC-1 (contract examples): key order, no whitespace, integers.
func TestAC1_EncodeContractExamples(t *testing.T) {
	defer guard(t)
	cases := []struct {
		name string
		in   any
		want string
	}{
		{"keys sorted, no whitespace", map[string]any{"b": 1, "a": "<&>"}, `{"a":"<&>","b":1}`},
		{"zero", 0, `0`},
		{"int64 zero", int64(0), `0`},
		{"negative", int64(-17), `-17`},
		{"max safe integer", int64(9007199254740991), `9007199254740991`},
		{"min safe integer", int64(-9007199254740991), `-9007199254740991`},
		{"null", nil, `null`},
		{"true", true, `true`},
		{"false", false, `false`},
		{"empty array", []any{}, `[]`},
		{"empty object", map[string]any{}, `{}`},
		{"array order preserved", []any{int64(3), int64(1), int64(2)}, `[3,1,2]`},
		{"nested objects sorted at every depth",
			map[string]any{"z": map[string]any{"b": nil, "a": []any{map[string]any{"y": true, "x": false}}}, "a": ""},
			`{"a":"","z":{"a":[{"x":false,"y":true}],"b":null}}`},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			defer guard(t)
			got, err := Encode(c.in)
			if err != nil {
				t.Fatalf("Encode: %v", err)
			}
			if string(got) != c.want {
				t.Fatalf("got %s, want %s", got, c.want)
			}
		})
	}
}

// AC-4: <, >, &, U+2028, U+2029 and DEL encode raw; control characters encode
// as lowercase \u00xx; the named escapes match Python's json.dumps.
func TestAC4_EscapingMatchesPython(t *testing.T) {
	defer guard(t)
	cases := []struct {
		name string
		in   string
		want string // exact bytes, including the surrounding quotes
	}{
		{"html characters raw", "<>&", `"<>&"`},
		{"line and paragraph separators raw", "\u2028\u2029", "\"\u2028\u2029\""},
		{"DEL raw", "\x7f", "\"\x7f\""},
		{"slash raw", "/", `"/"`},
		{"control characters lowercase hex", "\x00\x1f\x0b\x1b", `"\u0000\u001f\u000b\u001b"`},
		{"named escapes", "\"\\\n\r\t\b\f", `"\"\\\n\r\t\b\f"`},
		{"non-ASCII raw UTF-8", "é✓😀", "\"é✓😀\""},
		{"noncharacter raw", "\uffff", "\"\uffff\""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			defer guard(t)
			got, err := Encode(c.in)
			if err != nil {
				t.Fatalf("Encode: %v", err)
			}
			if string(got) != c.want {
				t.Fatalf("got %q, want %q", got, c.want)
			}
		})
	}
}

// AC-4: object keys sort by Unicode code point (UTF-8 byte order), not by
// UTF-16 code units: U+E000 sorts before U+1F600. Keys use the same escaping.
func TestAC4_KeyOrderAndKeyEscaping(t *testing.T) {
	defer guard(t)
	in := map[string]any{"é": 1, "a": 2, "z": 3, "Z": 4, "\ue000": 5, "😀": 6, "\n<": 7}
	want := "{\"\\n<\":7,\"Z\":4,\"a\":2,\"z\":3,\"é\":1,\"\ue000\":5,\"😀\":6}"
	got, err := Encode(in)
	if err != nil {
		t.Fatalf("Encode: %v", err)
	}
	if string(got) != want {
		t.Fatalf("got %q\nwant %q", got, want)
	}
}

// Contract errors for Encode: invalid_value, integer_range, invalid_utf8.
func TestAC4_EncodeRejectsValuesOutsideTheModel(t *testing.T) {
	defer guard(t)
	type point struct{ X int }
	cases := []struct {
		name string
		in   any
		code string
	}{
		{"float64", 1.5, "invalid_value"},
		{"float64 integral", float64(1), "invalid_value"},
		{"struct", point{1}, "invalid_value"},
		{"map with int keys", map[int]any{1: true}, "invalid_value"},
		{"nested float", map[string]any{"a": []any{int64(1), 2.5}}, "invalid_value"},
		{"int64 above range", int64(9007199254740992), "integer_range"},
		{"int below range", -9007199254740992, "integer_range"},
		{"invalid UTF-8 string", "\xff", "invalid_utf8"},
		{"invalid UTF-8 key", map[string]any{"\xc0\xaf": true}, "invalid_utf8"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			defer guard(t)
			_, err := Encode(c.in)
			requireCode(t, err, c.code)
		})
	}
}

// Encode must not depend on map iteration order: repeated runs give the same bytes.
func TestAC1_EncodeIsDeterministic(t *testing.T) {
	defer guard(t)
	m := map[string]any{}
	for i := 0; i < 200; i++ {
		m[string(rune('a'+i%26))+string(rune('A'+i/26))] = int64(i)
	}
	first, err := Encode(m)
	if err != nil {
		t.Fatalf("Encode: %v", err)
	}
	for i := 0; i < 20; i++ {
		again, err := Encode(m)
		if err != nil {
			t.Fatalf("Encode: %v", err)
		}
		if string(again) != string(first) {
			t.Fatalf("Encode is not deterministic")
		}
	}
}
