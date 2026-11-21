package codec

// Stubs for P1-01, from contracts/kernel/codec.yaml (parameter names blanked
// for the linter). The builder replaces this file with the implementation.

import "time"

// unimplemented is a variable so static analysis does not treat the stubs as
// never returning.
var unimplemented = func() { panic("unimplemented: P1-01") }

// Encode returns the canonical JSON bytes of v.
func Encode(_ any) ([]byte, error) { unimplemented(); return nil, nil }

// Decode strictly decodes data into a Value.
func Decode(_ []byte) (any, error) { unimplemented(); return nil, nil }

// DecodeObject strictly decodes data that must be a JSON object.
func DecodeObject(_ []byte) (map[string]any, error) { unimplemented(); return nil, nil }

// Tag names a hash domain.
type Tag string

// Hash domains.
const (
	TagProposal   Tag = "proposal"
	TagEvent      Tag = "event"
	TagProjection Tag = "projection"
	TagOutcome    Tag = "outcome"
	TagEffect     Tag = "effect"
)

// ZeroHash is the prev_hash of the first event.
const ZeroHash = "0000000000000000000000000000000000000000000000000000000000000000"

// Hash returns H(tag, v).
func Hash(_ Tag, _ any) (string, error) { unimplemented(); return "", nil }

// ValidID reports whether s is a valid record ID.
func ValidID(_ string) bool { unimplemented(); return false }

// ValidApprovalID reports whether s is a generated approval ID.
func ValidApprovalID(_ string) bool { unimplemented(); return false }

// FormatTime formats t in the canonical UTC form.
func FormatTime(_ time.Time) string { unimplemented(); return "" }

// ParseTime parses a canonical UTC time.
func ParseTime(_ string) (time.Time, error) { unimplemented(); return time.Time{}, nil }

// Event is one stored kernel event.
type Event struct {
	V            int64
	EventVersion int64
	Revision     int64
	BatchStart   int64
	BatchSize    int64
	ActorID      string
	ProposalID   string
	At           string
	Type         string
	Data         map[string]any
	PrevHash     string
	Hash         string
}

// EventSpec is the part of an event that operators decide; the store fills in
// the envelope (revision, batch, actor, proposal, time, hashes).
type EventSpec struct {
	Type         string
	EventVersion int64
	Data         map[string]any
}

// ParseEvent strictly decodes one stored event.
func ParseEvent(_ []byte) (Event, error) { unimplemented(); return Event{}, nil }

// Body returns the event as a map without its hash.
func (Event) Body() map[string]any { unimplemented(); return nil }

// Map returns the event as a map including its hash.
func (Event) Map() map[string]any { unimplemented(); return nil }

// ComputeHash returns H("event", e.Body()).
func (Event) ComputeHash() (string, error) { unimplemented(); return "", nil }

// VerifyChain checks hashes, revisions, links and batches.
func VerifyChain(_ []Event, _ string, _ int64) error { unimplemented(); return nil }

// Error is the codec's typed error.
type Error struct {
	Code     string
	Revision int64
	Detail   string
}

// Error implements the error interface.
func (*Error) Error() string { unimplemented(); return "" }
