package codec

import (
	"crypto/sha256"
	"encoding/hex"
	"regexp"
	"strconv"
	"time"
)

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
func Hash(tag Tag, v any) (string, error) {
	data, err := appendValue([]byte("purser/"+tag+"/v1\x00"), v, 0)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:]), nil
}

var recordID = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9_.-]{0,95}$`)
var approvalID = regexp.MustCompile(`^a:[0-9a-f]{64}$`)

// ValidID reports whether s is a valid record ID.
func ValidID(s string) bool { return recordID.MatchString(s) }

// ValidApprovalID reports whether s is a generated approval ID.
func ValidApprovalID(s string) bool { return approvalID.MatchString(s) }

const timeLayout = "2006-01-02T15:04:05.000000Z"

// FormatTime formats t in the canonical UTC form, or returns an empty string
// if the UTC year is outside the wire grammar.
func FormatTime(t time.Time) string {
	t = t.UTC()
	if t.Year() < 1 || t.Year() > 9999 {
		return ""
	}
	return t.Format(timeLayout)
}

// ParseTime parses a canonical UTC time.
func ParseTime(s string) (time.Time, error) {
	t, err := time.Parse(timeLayout, s)
	if err != nil || FormatTime(t) != s {
		return time.Time{}, &Error{Code: "bad_time"}
	}
	return t, nil
}

// Error is the codec's typed error.
type Error struct {
	Code     string
	Revision int64
	Detail   string
}

// Error implements the error interface.
func (e *Error) Error() string {
	message := e.Code
	if e.Revision != 0 {
		message += " at revision " + strconv.FormatInt(e.Revision, 10)
	}
	if e.Detail != "" {
		message += ": " + e.Detail
	}
	return message
}
