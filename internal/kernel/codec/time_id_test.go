package codec

import (
	"strings"
	"testing"
	"time"
)

// AC-6: FormatTime and ParseTime accept only YYYY-MM-DDTHH:MM:SS.ffffffZ with
// valid Gregorian dates. Cases were checked against reference/python
// wire.parse_time.
func TestAC6_ParseTimeAcceptsOnlyCanonicalTimes(t *testing.T) {
	defer guard(t)
	valid := []string{
		"2026-11-18T12:00:00.000000Z",
		"2024-02-29T23:59:59.999999Z",
		"0001-01-01T00:00:00.000000Z",
		"9999-12-31T23:59:59.999999Z",
		"2026-01-01T00:00:00.000001Z",
	}
	for _, s := range valid {
		t.Run("valid "+s, func(t *testing.T) {
			defer guard(t)
			tm, err := ParseTime(s)
			if err != nil {
				t.Fatalf("ParseTime(%q): %v", s, err)
			}
			if got := FormatTime(tm); got != s {
				t.Fatalf("FormatTime(ParseTime(%q)) = %q", s, got)
			}
		})
	}
	invalid := []string{
		"2026-02-29T00:00:00.000000Z",      // not a leap year
		"2026-11-31T00:00:00.000000Z",      // November has 30 days
		"2026-13-01T00:00:00.000000Z",      // month 13
		"0000-01-01T00:00:00.000000Z",      // year 0
		"2026-11-18T24:00:00.000000Z",      // hour 24
		"2026-11-18T12:60:00.000000Z",      // minute 60
		"2026-11-18T12:00:60.000000Z",      // leap second
		"2026-11-18T12:00:00Z",             // no fraction
		"2026-11-18T12:00:00.00000Z",       // five digits
		"2026-11-18T12:00:00.0000000Z",     // seven digits
		"2026-11-18T12:00:00.000000+00:00", // offset instead of Z
		"2026-11-18 12:00:00.000000Z",      // space separator
		"2026-11-18T12:00:00.000000z",      // lowercase z
		"2026-11-18T12:00:00,000000Z",      // comma
		"2026-1-18T12:00:00.0000000Z",      // unpadded month
		"",
	}
	for _, s := range invalid {
		t.Run("invalid "+s, func(t *testing.T) {
			defer guard(t)
			_, err := ParseTime(s)
			requireCode(t, err, "bad_time")
		})
	}
}

// AC-6: FormatTime converts to UTC with exactly six fractional digits.
func TestAC6_FormatTimeConvertsToUTC(t *testing.T) {
	defer guard(t)
	moscow := time.FixedZone("MSK", 3*3600)
	tm := time.Date(2026, 11, 18, 15, 0, 0, 123456000, moscow)
	if got := FormatTime(tm); got != "2026-11-18T12:00:00.123456Z" {
		t.Fatalf("FormatTime = %q", got)
	}
	if got := FormatTime(time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC)); got != "2026-01-02T03:04:05.000000Z" {
		t.Fatalf("FormatTime = %q", got)
	}
	parsed, err := ParseTime("2026-11-18T12:00:00.123456Z")
	if err != nil {
		t.Fatalf("ParseTime: %v", err)
	}
	if !parsed.Equal(tm) {
		t.Fatalf("ParseTime = %v, want the same instant as %v", parsed, tm)
	}
}

// Contract: ValidID accepts exactly ^[A-Za-z][A-Za-z0-9_.-]{0,95}$.
func TestContract_ValidID(t *testing.T) {
	defer guard(t)
	cases := map[string]bool{
		"W-root":                      true,
		"C-notify":                    true,
		"a":                           true,
		"x_y.z-1":                     true,
		"Z9":                          true,
		"a" + strings.Repeat("b", 95): true,
		"a" + strings.Repeat("b", 96): false,
		"":                            false,
		"1abc":                        false,
		"_x":                          false,
		"-x":                          false,
		"a:0123":                      false,
		"principal:local":             false, // actor IDs are not record IDs
		"a b":                         false,
		"a/b":                         false,
		"é":                           false,
		"ab\n":                        false,
	}
	for in, want := range cases {
		if got := ValidID(in); got != want {
			t.Errorf("ValidID(%q) = %v, want %v", in, got, want)
		}
	}
}

// Contract: ValidApprovalID accepts exactly ^a:[0-9a-f]{64}$.
func TestContract_ValidApprovalID(t *testing.T) {
	defer guard(t)
	hex64 := strings.Repeat("0123456789abcdef", 4)
	cases := map[string]bool{
		"a:" + hex64:                  true,
		"a:" + strings.ToUpper(hex64): false,
		"a:" + hex64[:63]:             false,
		"a:" + hex64 + "0":            false,
		"b:" + hex64:                  false,
		"A:" + hex64:                  false,
		hex64:                         false,
		"a:" + hex64[:62] + "g0":      false,
		"":                            false,
	}
	for in, want := range cases {
		if got := ValidApprovalID(in); got != want {
			t.Errorf("ValidApprovalID(%q) = %v, want %v", in, got, want)
		}
	}
}
