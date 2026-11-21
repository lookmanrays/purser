package codec

import (
	"reflect"
	"testing"
)

// AC-3: the strict decoder rejects duplicate keys, non-integers, integers
// outside ±(2^53−1), invalid UTF-8 and escaped lone surrogates, each with its
// own error code. Trailing data and other syntax errors have their own codes.
func TestAC3_DecodeRejections(t *testing.T) {
	defer guard(t)
	cases := []struct {
		name string
		raw  []byte
		code string
	}{
		{"duplicate key", []byte(`{"a":1,"a":2}`), "duplicate_key"},
		{"nested duplicate key", []byte(`{"x":{"a":1,"a":2}}`), "duplicate_key"},
		{"fraction", []byte(`1.0`), "non_integer"},
		{"exponent", []byte(`1e3`), "non_integer"},
		{"negative fraction in array", []byte(`[-0.5]`), "non_integer"},
		{"above max safe integer", []byte(`9007199254740992`), "integer_range"},
		{"below min safe integer", []byte(`-9007199254740992`), "integer_range"},
		{"invalid UTF-8 byte", []byte("\"\xff\""), "invalid_utf8"},
		{"overlong UTF-8", []byte("\"\xc0\xaf\""), "invalid_utf8"},
		{"lone high surrogate", []byte(`"\ud800"`), "lone_surrogate"},
		{"lone low surrogate", []byte(`"\udc00"`), "lone_surrogate"},
		{"surrogate in key", []byte(`{"\ud800":1}`), "lone_surrogate"},
		{"two values", []byte(`1 2`), "trailing_data"},
		{"two objects", []byte(`{} {}`), "trailing_data"},
		{"unterminated object", []byte(`{`), "invalid_json"},
		{"trailing comma", []byte(`[1,]`), "invalid_json"},
		{"NaN", []byte(`NaN`), "invalid_json"},
		{"single quotes", []byte(`'a'`), "invalid_json"},
		{"empty input", []byte(``), "invalid_json"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			defer guard(t)
			v, err := Decode(c.raw)
			if err == nil {
				t.Fatalf("Decode(%q) = %v, want error %q", c.raw, v, c.code)
			}
			requireCode(t, err, c.code)
		})
	}
}

// AC-3: a UTF-8 byte order mark is not JSON (Python's decoder rejects it too).
func TestAC3_DecodeRejectsBOM(t *testing.T) {
	defer guard(t)
	if _, err := Decode([]byte("\xef\xbb\xbf{}")); err == nil {
		t.Fatalf("Decode accepted a byte order mark")
	}
}

// AC-3: accepted values decode to exactly the Value types.
func TestAC3_DecodeProducesValueTypes(t *testing.T) {
	defer guard(t)
	cases := []struct {
		name string
		raw  string
		want any
	}{
		{"max safe integer", `9007199254740991`, int64(9007199254740991)},
		{"min safe integer", `-9007199254740991`, int64(-9007199254740991)},
		{"surrogate pair", `"😀"`, "😀"},
		{"escaped control", `"\u001b"`, "\x1b"},
		{"nested", `{"a":[true,null,"x",-17,{"b":{}}]}`,
			map[string]any{"a": []any{true, nil, "x", int64(-17), map[string]any{"b": map[string]any{}}}}},
		{"empty array", `[]`, []any{}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			defer guard(t)
			got, err := Decode([]byte(c.raw))
			if err != nil {
				t.Fatalf("Decode: %v", err)
			}
			if !reflect.DeepEqual(got, c.want) {
				t.Fatalf("Decode(%s) = %#v, want %#v", c.raw, got, c.want)
			}
		})
	}
}

// AC-3: DecodeObject accepts only a top-level object.
func TestAC3_DecodeObject(t *testing.T) {
	defer guard(t)
	m, err := DecodeObject([]byte(`{"b":1,"a":"x"}`))
	if err != nil {
		t.Fatalf("DecodeObject: %v", err)
	}
	if !reflect.DeepEqual(m, map[string]any{"a": "x", "b": int64(1)}) {
		t.Fatalf("DecodeObject = %#v", m)
	}
	for _, raw := range []string{`[1]`, `"x"`, `1`, `null`} {
		if _, err := DecodeObject([]byte(raw)); err == nil {
			t.Fatalf("DecodeObject(%s) accepted a non-object", raw)
		}
	}
	_, err = DecodeObject([]byte(`{"a":1,"a":2}`))
	requireCode(t, err, "duplicate_key")
}

// roundTrip checks the contract property: if Decode accepts data, then
// Encode(v) is canonical and Decode(Encode(v)) deep-equals v.
func roundTrip(t *testing.T, data []byte) {
	t.Helper()
	v, err := Decode(data)
	if err != nil {
		return
	}
	enc, err := Encode(v)
	if err != nil {
		t.Fatalf("Encode(Decode(%q)): %v", data, err)
	}
	v2, err := Decode(enc)
	if err != nil {
		t.Fatalf("Decode(Encode(...)) of %q: %v", enc, err)
	}
	if !reflect.DeepEqual(v, v2) {
		t.Fatalf("round trip changed the value: %#v -> %q -> %#v", v, enc, v2)
	}
	enc2, err := Encode(v2)
	if err != nil || string(enc2) != string(enc) {
		t.Fatalf("canonical bytes are not stable: %q then %q (%v)", enc, enc2, err)
	}
}

var roundTripSeeds = []string{
	`{"a":[1,2,{"b":null}],"é":"café\n✓"}`,
	`" <&>\u007f\u0000"`,
	`[true,false,null,0,-0,9007199254740991,-9007199254740991]`,
	`{"z":{},"a":[],"m":""}`,
	`"😀 "`,
	` {"spaced" : [ 1 , 2 ] } `,
	`{"a":1,"a":2}`,
	`1.5`,
	`"\ud800"`,
}

// AC-7: the round-trip property holds on a deterministic seed corpus (the
// fuzz target below explores beyond it with `make fuzz`).
func TestAC7_RoundTripSeeds(t *testing.T) {
	defer guard(t)
	for _, s := range roundTripSeeds {
		roundTrip(t, []byte(s))
	}
	vec := wireVector(t)
	for _, e := range vec["events"].([]any) {
		roundTrip(t, rawJSON(t, e))
	}
}

// AC-7: fuzz target; no input may make Decode(Encode(x)) differ from x.
func FuzzRoundTrip(f *testing.F) {
	for _, s := range roundTripSeeds {
		f.Add([]byte(s))
	}
	f.Fuzz(func(t *testing.T, data []byte) {
		defer guard(t)
		roundTrip(t, data)
	})
}
