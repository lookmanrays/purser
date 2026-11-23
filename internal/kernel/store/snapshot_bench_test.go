package store

import (
	"fmt"
	"testing"
)

// benchcheck: BenchmarkSnapshotRead/events=100000 / BenchmarkSnapshotRead/events=1000 <= 2.0
func BenchmarkSnapshotRead(b *testing.B) {
	for _, count := range []int{1000, 10000, 100000} {
		b.Run(fmt.Sprintf("events=%d", count), func(b *testing.B) {
			s := makeUniformStore(b, count)
			b.ResetTimer()
			for i := 0; i < b.N; i++ {
				_, err := s.Snapshot(nil)
				if err != nil {
					b.Fatalf("Snapshot(nil): %v", err)
				}
			}
		})
	}
}
