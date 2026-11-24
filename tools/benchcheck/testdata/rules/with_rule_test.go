package rules

import "testing"

// benchcheck: BenchmarkSnapshotRead/events=100000 / BenchmarkSnapshotRead/events=1000 <= 2.0
func BenchmarkSnapshotRead_Events(_ *testing.B) {}

func TestAC3_RuleFixture(t *testing.T) {
	t.Fatalf("unimplemented: P0-07")
}
