package gate

import (
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
			requireErrorCode(t, err, tc.ExpectedError)
		})
	}
}

func TestNegative_InvalidBindings(t *testing.T) {
	defer guard(t)

	p := readParity(t)
	for _, tc := range p.InvalidBindings {
		t.Run(tc.Name, func(t *testing.T) {
			defer guard(t)
			s, w := newTestStore(t)
			clock := func() time.Time {
				ts, _ := codec.ParseTime(w.ClockByBatch[0])
				return ts
			}
			g := newTestGate(t, s, clock)
			validRaw := encodeProposal(t, w.Proposals[0])

			b := bindingFromMap(tc.Binding)
			_, err := g.Admit(b, validRaw)
			requireErrorCode(t, err, tc.ExpectedError)
		})
	}
}

func TestNegative_InvalidOps(t *testing.T) {
	defer guard(t)

	p := readParity(t)
	for _, tc := range p.InvalidOps {
		t.Run(tc.Name, func(t *testing.T) {
			defer guard(t)
			s, w := newTestStore(t)
			currTime, _ := codec.ParseTime(w.ClockByBatch[0])
			clock := func() time.Time { return currTime }
			g := newTestGate(t, s, clock)
			bPrincipal := bindingFromWire(w)

			propMap, err := codec.DecodeObject(tc.Proposal)
			if err != nil {
				t.Fatalf("codec.DecodeObject failed: %v", err)
			}
			baseRev, ok := propMap["base_revision"].(int64)
			if !ok {
				t.Fatalf("base_revision is not int64: %T (%v)", propMap["base_revision"], propMap["base_revision"])
			}
			if baseRev >= 2 {
				currTime, _ = codec.ParseTime(w.ClockByBatch[0])
				if _, err := g.Admit(bPrincipal, encodeProposal(t, w.Proposals[0])); err != nil {
					t.Fatalf("setup batch 0 failed: %v", err)
				}
			}
			if baseRev >= 4 {
				currTime, _ = codec.ParseTime(w.ClockByBatch[1])
				if _, err := g.Admit(bPrincipal, encodeProposal(t, w.Proposals[1])); err != nil {
					t.Fatalf("setup batch 1 failed: %v", err)
				}
			}

			b := bindingFromMap(tc.Binding)
			raw := encodeProposal(t, propMap)

			r, err := g.Admit(b, raw)
			if err != nil {
				t.Fatalf("Admit returned transport error: %v, want receipt code %s", err, tc.ExpectedCode)
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
