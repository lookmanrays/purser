package codec

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
)

// Frozen test helpers for P1-01. They use only the standard library, so the
// tests do not depend on any other Purser package. Vectors are read from the
// read-only reference copy; P0-06 guarantees testdata/vectors holds the same bytes.

const referenceContracts = "../../../reference/python/docs/contracts"

// guard turns a panic (for example from an unimplemented stub) into a test
// failure, so one failing test does not abort the whole test binary.
// Use as the first statement of every test and subtest: defer guard(t)
func guard(t *testing.T) {
	if r := recover(); r != nil {
		t.Fatalf("panic: %v", r)
	}
}

// readJSON decodes a JSON file into Value-shaped data (nil, bool, string,
// int64, []any, map[string]any) without going through the package under test.
func readJSON(t *testing.T, path string) any {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return parseJSON(t, raw)
}

func parseJSON(t *testing.T, raw []byte) any {
	t.Helper()
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		t.Fatalf("parse JSON: %v", err)
	}
	return normalize(t, v)
}

func normalize(t *testing.T, v any) any {
	t.Helper()
	switch x := v.(type) {
	case json.Number:
		n, err := x.Int64()
		if err != nil {
			t.Fatalf("non-integer number %q in fixture", x)
		}
		return n
	case []any:
		out := make([]any, len(x))
		for i, item := range x {
			out[i] = normalize(t, item)
		}
		return out
	case map[string]any:
		out := make(map[string]any, len(x))
		for k, item := range x {
			out[k] = normalize(t, item)
		}
		return out
	default:
		return x
	}
}

func wireVector(t *testing.T) map[string]any {
	t.Helper()
	return readJSON(t, filepath.Join(referenceContracts, "wire-v1.json")).(map[string]any)
}

// codeOf returns the Code of a *Error anywhere in err's chain, or "" if none.
func codeOf(err error) string {
	var ce *Error
	if errors.As(err, &ce) {
		return ce.Code
	}
	return ""
}

func requireCode(t *testing.T, err error, want string) {
	t.Helper()
	if err == nil {
		t.Fatalf("want error %q, got nil", want)
	}
	if got := codeOf(err); got != want {
		t.Fatalf("want error code %q, got %q (%v)", want, got, err)
	}
}

func copyMap(m map[string]any) map[string]any {
	out := make(map[string]any, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}

// rawJSON marshals fixture data back to (non-canonical) JSON bytes for the
// functions that take raw input.
func rawJSON(t *testing.T, v any) []byte {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatalf("marshal fixture: %v", err)
	}
	return b
}
