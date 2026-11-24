package store

import (
	"testing"
	"time"

	"github.com/lookmanrays/purser/internal/kernel/codec"
)

// decide receives the instant the store records: UTC at microsecond precision,
// equal to the parsed stored at, so a decision made at the edge of an expiry
// agrees with what replay shows.
func TestTransactPassesDecideTheRecordedInstant(t *testing.T) {
	defer guard(t)

	s, _ := newTestStore(t)
	if _, err := s.Initialize(); err != nil {
		t.Fatalf("Initialize: %v", err)
	}
	sample := time.Date(2026, 11, 24, 15, 4, 5, 123456789, time.FixedZone("UTC+3", 3*60*60))
	var got time.Time
	_, events, err := s.Transact("principal:local", "p-clock", func() time.Time { return sample },
		func(_ Snapshot, at time.Time) ([]codec.EventSpec, error) {
			got = at
			return []codec.EventSpec{{Type: "CommitmentCreated", EventVersion: 1, Data: map[string]any{
				"id": "C-clock", "root": "W-clock", "principal": "principal:local", "statement": "Clock test",
			}}}, nil
		})
	if err != nil {
		t.Fatalf("Transact: %v", err)
	}

	stamp := codec.FormatTime(sample)
	want, err := codec.ParseTime(stamp)
	if err != nil {
		t.Fatalf("ParseTime(%q): %v", stamp, err)
	}
	if !got.Equal(want) {
		t.Errorf("decide got %v, want the recorded instant %v", got, want)
	}
	if _, offset := got.Zone(); offset != 0 {
		t.Errorf("decide got zone offset %d, want UTC", offset)
	}
	if got.Nanosecond()%1000 != 0 {
		t.Errorf("decide got %d ns, want microsecond precision", got.Nanosecond())
	}
	if len(events) != 1 || events[0].At != stamp {
		t.Fatalf("stored at = %v, want %q", events, stamp)
	}
}
