package codec

// Stubs for P1-01, from contracts/kernel/codec.yaml (parameter names blanked
// for the linter). The builder replaces this file with the implementation.

import "time"

// Encode returns the canonical JSON bytes of v.
func Encode(_ any) ([]byte, error) { panic("unimplemented: P1-01") }

// Decode strictly decodes data into a Value.
func Decode(_ []byte) (any, error) { panic("unimplemented: P1-01") }

// DecodeObject strictly decodes data that must be a JSON object.
func DecodeObject(_ []byte) (map[string]any, error) { panic("unimplemented: P1-01") }

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
func Hash(_ Tag, _ any) (string, error) { panic("unimplemented: P1-01") }

// ValidID reports whether s is a valid record ID.
func ValidID(_ string) bool { panic("unimplemented: P1-01") }

// ValidApprovalID reports whether s is a generated approval ID.
func ValidApprovalID(_ string) bool { panic("unimplemented: P1-01") }

// FormatTime formats t in the canonical UTC form.
func FormatTime(_ time.Time) string { panic("unimplemented: P1-01") }

// ParseTime parses a canonical UTC time.
func ParseTime(_ string) (time.Time, error) { panic("unimplemented: P1-01") }

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
func ParseEvent(_ []byte) (Event, error) { panic("unimplemented: P1-01") }

// Body returns the event as a map without its hash.
func (Event) Body() map[string]any { panic("unimplemented: P1-01") }

// Map returns the event as a map including its hash.
func (Event) Map() map[string]any { panic("unimplemented: P1-01") }

// ComputeHash returns H("event", e.Body()).
func (Event) ComputeHash() (string, error) { panic("unimplemented: P1-01") }

// VerifyChain checks hashes, revisions, links and batches.
func VerifyChain(_ []Event, _ string, _ int64) error {
	panic("unimplemented: P1-01")
}

// Error is the codec's typed error.
type Error struct {
	Code     string
	Revision int64
	Detail   string
}

// Error implements the error interface.
func (*Error) Error() string { panic("unimplemented: P1-01") }
