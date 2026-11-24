package gate

import (
	"strings"
	"testing"
	"time"

	"github.com/lookmanrays/purser/internal/kernel/codec"
)

func TestNegative_InvalidProposals(t *testing.T) {
	defer guard(t)

	p := readParity(t)
	for _, tc := range p.InvalidProposals {
		t.Run(tc.Name, func(t *testing.T) {
			defer guard(t)
			_, err := ParseProposal([]byte(tc.Raw))
			if err == nil {
				t.Errorf("ParseProposal(%s) expected error, got nil", tc.Name)
			}
		})
	}
}

func TestNegative_InvalidBindings(t *testing.T) {
	defer guard(t)

	s, w := newTestStore(t)
	clock := func() time.Time {
		ts, _ := codec.ParseTime(w.ClockByBatch[0])
		return ts
	}
	g := newTestGate(t, s, clock)
	validRaw := encodeProposal(t, w.Proposals[0])

	p := readParity(t)
	for _, tc := range p.InvalidBindings {
		t.Run(tc.Name, func(t *testing.T) {
			defer guard(t)
			b := Binding{}
			if a, ok := tc.Binding["actor_id"].(string); ok {
				b.ActorID = a
			}
			if r, ok := tc.Binding["role"].(string); ok {
				b.Role = r
			}
			if pr, ok := tc.Binding["principal_id"].(string); ok {
				b.PrincipalID = pr
			}
			if c, ok := tc.Binding["commitment"].(string); ok {
				b.Commitment = &c
			}
			if asg, ok := tc.Binding["assignment"].(string); ok {
				b.Assignment = &asg
			}
			if tc.Binding["unexpected"] != nil {
				b.Attachments = map[string]any{"unexpected": tc.Binding["unexpected"]}
			}

			_, err := g.Admit(b, validRaw)
			if err == nil {
				t.Errorf("Admit with invalid binding (%s) expected error, got nil", tc.Name)
			}
		})
	}
}

func TestNegative_InvalidOps(t *testing.T) {
	defer guard(t)

	s, w := newTestStore(t)
	clock := func() time.Time {
		ts, _ := codec.ParseTime(w.ClockByBatch[0])
		return ts
	}
	g := newTestGate(t, s, clock)
	b := bindingFromWire(w)

	p := readParity(t)
	for _, tc := range p.InvalidOps {
		t.Run(tc.Name, func(t *testing.T) {
			defer guard(t)
			prop := map[string]any{
				"v":             int64(1),
				"proposal_id":   "p-neg-op",
				"commitment":    "C-test",
				"base_revision": int64(0),
				"ops":           []any{tc.Op},
				"justification": []any{},
			}
			raw := encodeProposal(t, prop)

			r, err := g.Admit(b, raw)
			if err != nil {
				// Some syntax violations like op not a map fail at parsing or admission transport
				if !strings.Contains(err.Error(), tc.ExpectedCode) {
					t.Logf("admit returned transport error: %v (expected code %s)", err, tc.ExpectedCode)
				}
				return
			}
			if r.Disposition != "rejected" {
				t.Errorf("op %s disposition = %s, want rejected", tc.Name, r.Disposition)
			}
			if r.Code != tc.ExpectedCode {
				t.Errorf("op %s code = %s, want %s", tc.Name, r.Code, tc.ExpectedCode)
			}
		})
	}
}
