package codec

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"slices"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
)

// Tag names a hash domain.
type Tag string

// Hash domains and constants.
const (
	TagProposal   Tag = "proposal"
	TagEvent      Tag = "event"
	TagProjection Tag = "projection"
	TagOutcome    Tag = "outcome"
	TagEffect     Tag = "effect"
	ZeroHash          = "0000000000000000000000000000000000000000000000000000000000000000"
	maxInt            = (1 << 53) - 1
	hexDigits         = "0123456789abcdef"
	timeFmt           = "2006-01-02T15:04:05.000000Z"
)

// Event is one stored kernel event.
type Event struct {
	V, EventVersion, Revision, BatchStart, BatchSize int64
	ActorID, ProposalID, At, Type, PrevHash, Hash    string
	Data                                             map[string]any
}

// EventSpec is the part of an event that operators decide.
type EventSpec struct {
	Type         string
	EventVersion int64
	Data         map[string]any
}

// Error is the codec's typed error.
type Error struct {
	Code     string
	Revision int64
	Detail   string
}

func (e *Error) Error() string { return e.Code }
func errCode(c string) error   { return &Error{Code: c} }

// ValidID reports whether s is a valid record ID.
func ValidID(s string) bool {
	if len(s) == 0 || len(s) > 96 || !isAlpha(s[0]) {
		return false
	}
	for i := 1; i < len(s); i++ {
		if b := s[i]; !isAlpha(b) && !isDigit(b) && b != '_' && b != '.' && b != '-' {
			return false
		}
	}
	return true
}

// ValidApprovalID reports whether s is a generated approval ID.
func ValidApprovalID(s string) bool {
	if len(s) != 66 || !strings.HasPrefix(s, "a:") {
		return false
	}
	for _, b := range []byte(s[2:]) {
		if !isDigit(b) && (b < 'a' || b > 'f') {
			return false
		}
	}
	return true
}

func isAlpha(b byte) bool { return (b >= 'a' && b <= 'z') || (b >= 'A' && b <= 'Z') }
func isDigit(b byte) bool { return b >= '0' && b <= '9' }

// FormatTime formats t in the canonical UTC form.
func FormatTime(t time.Time) string { return t.UTC().Format(timeFmt) }

// ParseTime parses a canonical UTC time.
func ParseTime(s string) (time.Time, error) {
	if len(s) != 27 || s[26] != 'Z' {
		return time.Time{}, errCode("bad_time")
	}
	t, err := time.Parse(timeFmt, s)
	if err != nil || t.Year() < 1 || t.Year() > 9999 || FormatTime(t) != s {
		return time.Time{}, errCode("bad_time")
	}
	return t, nil
}

// Body returns the event as a map without its hash.
func (e Event) Body() map[string]any {
	return map[string]any{
		"actor_id": e.ActorID, "at": e.At, "batch_size": e.BatchSize, "batch_start": e.BatchStart,
		"data": e.Data, "event_version": e.EventVersion, "prev_hash": e.PrevHash, "proposal_id": e.ProposalID,
		"revision": e.Revision, "type": e.Type, "v": e.V,
	}
}

// Map returns the event as a map including its hash.
func (e Event) Map() map[string]any { m := e.Body(); m["hash"] = e.Hash; return m }

// ComputeHash returns H("event", e.Body()).
func (e Event) ComputeHash() (string, error) { return Hash(TagEvent, e.Body()) }

// ParseEvent strictly decodes one stored event.
func ParseEvent(raw []byte) (Event, error) {
	v, err := Decode(raw)
	if err != nil {
		return Event{}, err
	}
	m, ok := v.(map[string]any)
	if !ok || len(m) != 12 {
		return Event{}, errCode("bad_event")
	}
	ok = true
	ev := Event{
		V: get[int64](m["v"], &ok), EventVersion: get[int64](m["event_version"], &ok),
		Revision: get[int64](m["revision"], &ok), BatchStart: get[int64](m["batch_start"], &ok),
		BatchSize: get[int64](m["batch_size"], &ok), ActorID: get[string](m["actor_id"], &ok),
		ProposalID: get[string](m["proposal_id"], &ok), At: get[string](m["at"], &ok),
		Type: get[string](m["type"], &ok), PrevHash: get[string](m["prev_hash"], &ok),
		Hash: get[string](m["hash"], &ok), Data: get[map[string]any](m["data"], &ok),
	}
	if !ok {
		return Event{}, errCode("bad_event")
	}
	return ev, nil
}

func get[T any](v any, ok *bool) (x T) {
	if val, is := v.(T); is {
		return val
	}
	*ok = false
	return x
}

// VerifyChain checks hashes, revisions, links and batches.
func VerifyChain(events []Event, prevHash string, nextRevision int64) error {
	var curStart, curSize, inBatch int64
	for _, e := range events {
		h, err := e.ComputeHash()
		if err != nil || h != e.Hash {
			return &Error{Code: "bad_hash", Revision: e.Revision}
		}
		if e.Revision != nextRevision {
			return &Error{Code: "chain_gap", Revision: e.Revision}
		}
		if e.PrevHash != prevHash {
			return &Error{Code: "bad_link", Revision: e.Revision}
		}
		if inBatch == 0 {
			curStart, curSize = e.BatchStart, e.BatchSize
			if curStart != e.Revision || curSize <= 0 {
				return &Error{Code: "bad_batch", Revision: e.Revision}
			}
		} else if e.BatchStart != curStart || e.BatchSize != curSize {
			return &Error{Code: "bad_batch", Revision: e.Revision}
		}
		if inBatch++; inBatch == curSize {
			inBatch = 0
		}
		prevHash, nextRevision = e.Hash, nextRevision+1
	}
	if inBatch != 0 {
		return &Error{Code: "bad_batch"}
	}
	return nil
}

// Hash returns H(tag, v).
func Hash(tag Tag, v any) (string, error) {
	b, err := Encode(v)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(append([]byte("purser/"+string(tag)+"/v1\x00"), b...))
	return hex.EncodeToString(sum[:]), nil
}

// Encode returns the canonical JSON bytes of v.
func Encode(v any) ([]byte, error) { return encVal(nil, v) }

func encVal(buf []byte, v any) ([]byte, error) {
	switch x := v.(type) {
	case nil:
		return append(buf, "null"...), nil
	case bool:
		if x {
			return append(buf, "true"...), nil
		}
		return append(buf, "false"...), nil
	case string:
		if !utf8.ValidString(x) {
			return nil, errCode("invalid_utf8")
		}
		return escapeString(buf, x), nil
	case int:
		return encInt(buf, int64(x))
	case int64:
		return encInt(buf, x)
	case []any:
		buf = append(buf, '[')
		for i, item := range x {
			if i > 0 {
				buf = append(buf, ',')
			}
			var err error
			if buf, err = encVal(buf, item); err != nil {
				return nil, err
			}
		}
		return append(buf, ']'), nil
	case map[string]any:
		keys := make([]string, 0, len(x))
		for k := range x {
			if !utf8.ValidString(k) {
				return nil, errCode("invalid_utf8")
			}
			keys = append(keys, k)
		}
		slices.Sort(keys)
		buf = append(buf, '{')
		for i, k := range keys {
			if i > 0 {
				buf = append(buf, ',')
			}
			buf = append(escapeString(buf, k), ':')
			var err error
			if buf, err = encVal(buf, x[k]); err != nil {
				return nil, err
			}
		}
		return append(buf, '}'), nil
	default:
		return nil, errCode("invalid_value")
	}
}

func encInt(buf []byte, n int64) ([]byte, error) {
	if n < -maxInt || n > maxInt {
		return nil, errCode("integer_range")
	}
	return strconv.AppendInt(buf, n, 10), nil
}

func escapeString(buf []byte, s string) []byte {
	buf = append(buf, '"')
	for i := 0; i < len(s); i++ {
		switch b := s[i]; b {
		case '"', '\\':
			buf = append(buf, '\\', b)
		case '\n':
			buf = append(buf, '\\', 'n')
		case '\r':
			buf = append(buf, '\\', 'r')
		case '\t':
			buf = append(buf, '\\', 't')
		case '\b':
			buf = append(buf, '\\', 'b')
		case '\f':
			buf = append(buf, '\\', 'f')
		default:
			if b < 0x20 {
				buf = append(buf, '\\', 'u', '0', '0', hexDigits[b>>4], hexDigits[b&0xf])
			} else {
				buf = append(buf, b)
			}
		}
	}
	return append(buf, '"')
}

// Decode strictly decodes data into a Value.
func Decode(data []byte) (any, error) {
	if !utf8.Valid(data) {
		return nil, errCode("invalid_utf8")
	}
	if err := checkSurrogates(data); err != nil {
		return nil, err
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	tok, err := nextTok(dec)
	if err != nil {
		return nil, err
	}
	v, err := parseTok(dec, tok)
	if err != nil {
		return nil, err
	}
	if dec.More() || hasToken(dec) {
		return nil, errCode("trailing_data")
	}
	return v, nil
}

func hasToken(dec *json.Decoder) bool {
	_, err := dec.Token()
	return err == nil
}

// DecodeObject strictly decodes data that must be a JSON object.
func DecodeObject(data []byte) (map[string]any, error) {
	v, err := Decode(data)
	if err != nil {
		return nil, err
	}
	m, ok := v.(map[string]any)
	if !ok {
		return nil, errCode("invalid_value")
	}
	return m, nil
}

func checkSurrogates(d []byte) error {
	for i := 0; i+5 < len(d); i++ {
		if d[i] == '\\' && d[i+1] == '\\' {
			i++
		} else if d[i] == '\\' && d[i+1] == 'u' {
			if cp, err := strconv.ParseUint(string(d[i+2:i+6]), 16, 16); err == nil && cp >= 0xd800 && cp <= 0xdfff {
				if cp <= 0xdbff && i+11 < len(d) && d[i+6] == '\\' && d[i+7] == 'u' {
					if cp2, err2 := strconv.ParseUint(string(d[i+8:i+12]), 16, 16); err2 == nil && cp2 >= 0xdc00 && cp2 <= 0xdfff {
						i += 11
						continue
					}
				}
				return errCode("lone_surrogate")
			}
		}
	}
	return nil
}

func parseTok(dec *json.Decoder, tok json.Token) (any, error) {
	switch x := tok.(type) {
	case json.Delim:
		if x == '{' {
			m := make(map[string]any)
			for dec.More() {
				k, err := nextTok(dec)
				if err != nil {
					return nil, err
				}
				ks, ok := k.(string)
				if !ok {
					return nil, errCode("invalid_json")
				}
				if _, dup := m[ks]; dup {
					return nil, errCode("duplicate_key")
				}
				vtok, err := nextTok(dec)
				if err != nil {
					return nil, err
				}
				if m[ks], err = parseTok(dec, vtok); err != nil {
					return nil, err
				}
			}
			_, err := nextTok(dec)
			return m, err
		}
		if x == '[' {
			arr := []any{}
			for dec.More() {
				itok, err := nextTok(dec)
				if err != nil {
					return nil, err
				}
				item, err := parseTok(dec, itok)
				if err != nil {
					return nil, err
				}
				arr = append(arr, item)
			}
			_, err := nextTok(dec)
			return arr, err
		}
	case json.Number:
		if strings.ContainsAny(string(x), ".eE") {
			return nil, errCode("non_integer")
		}
		n, err := x.Int64()
		if err != nil || n < -maxInt || n > maxInt {
			return nil, errCode("integer_range")
		}
		return n, nil
	case string, bool, nil:
		return x, nil
	}
	return nil, errCode("invalid_json")
}

func nextTok(dec *json.Decoder) (json.Token, error) {
	tok, err := dec.Token()
	if err != nil {
		return nil, errCode("invalid_json")
	}
	return tok, nil
}
