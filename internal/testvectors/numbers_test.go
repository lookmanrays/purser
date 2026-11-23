package testvectors

import (
	"fmt"
	"reflect"
	"testing"
)

// TestVectorsDecodeIntegersAsInt64 guards the kernel tests, which assert int64
// on wire, lifecycle and legacy-status values and pass them to the canonical
// codec, which rejects float64. The vectors contain no non-integer numbers.
func TestVectorsDecodeIntegersAsInt64(t *testing.T) {
	for name, v := range map[string]any{
		"wire":          Wire(),
		"lifecycle":     Lifecycle(),
		"legacy status": LegacyStatus(),
	} {
		ints := 0
		walkDynamic(t, name, reflect.ValueOf(v), &ints)
		if ints == 0 {
			t.Errorf("%s: no int64 values found in dynamic fields", name)
		}
	}

	w := Wire()
	if got, ok := w.Events[0]["event_version"].(int64); !ok || got != 1 {
		t.Errorf("wire events[0].event_version = %#v, want int64(1)", w.Events[0]["event_version"])
	}
	if got, ok := w.Events[7]["revision"].(int64); !ok || got != 8 {
		t.Errorf("wire events[7].revision = %#v, want int64(8)", w.Events[7]["revision"])
	}
	proj, _ := w.Expected.SnapshotAt4["projection"].(map[string]any)
	records, _ := proj["records"].([]any)
	if len(records) == 0 {
		t.Fatal("wire expected.snapshot_at_4.projection.records is empty")
	}
	rec, _ := records[0].(map[string]any)
	if got, ok := rec["changed"].(int64); !ok || got != 1 {
		t.Errorf("snapshot_at_4 records[0].changed = %#v, want int64(1)", rec["changed"])
	}
}

func walkDynamic(t *testing.T, path string, v reflect.Value, ints *int) {
	t.Helper()
	switch v.Kind() {
	case reflect.Pointer, reflect.Interface:
		if !v.IsNil() {
			walkDynamic(t, path, v.Elem(), ints)
		}
	case reflect.Struct:
		for i := 0; i < v.NumField(); i++ {
			walkDynamic(t, path+"."+v.Type().Field(i).Name, v.Field(i), ints)
		}
	case reflect.Slice, reflect.Array:
		for i := 0; i < v.Len(); i++ {
			walkDynamic(t, fmt.Sprintf("%s[%d]", path, i), v.Index(i), ints)
		}
	case reflect.Map:
		for _, k := range v.MapKeys() {
			walkDynamic(t, fmt.Sprintf("%s[%v]", path, k), v.MapIndex(k), ints)
		}
	case reflect.Float32, reflect.Float64:
		t.Errorf("%s holds a float (%v); integers must decode as int64", path, v)
	case reflect.Int64:
		*ints++
	}
}
