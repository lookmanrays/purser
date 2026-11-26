package gate

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/lookmanrays/purser/internal/kernel/codec"
	"github.com/lookmanrays/purser/internal/kernel/projection"
	"github.com/lookmanrays/purser/internal/kernel/store"
	"github.com/lookmanrays/purser/internal/testvectors"
)

func guard(t *testing.T) {
	if r := recover(); r != nil {
		t.Fatalf("panic: %v", r)
	}
}

type fakeArtifactStore struct {
	data map[string][]byte
}

func newFakeArtifactStore() *fakeArtifactStore {
	return &fakeArtifactStore{data: make(map[string][]byte)}
}

func (f *fakeArtifactStore) Put(data []byte) (string, error) {
	h := sha256.Sum256(data)
	digest := hex.EncodeToString(h[:])
	f.data[digest] = bytes.Clone(data)
	return digest, nil
}

func (f *fakeArtifactStore) Get(digest string) ([]byte, error) {
	b, ok := f.data[digest]
	if !ok {
		return nil, errors.New("not found")
	}
	return bytes.Clone(b), nil
}

func (f *fakeArtifactStore) Has(digest string) (bool, error) {
	_, ok := f.data[digest]
	return ok, nil
}

func newTestStore(t *testing.T) (*store.Store, *testvectors.WireVector) {
	t.Helper()
	w := testvectors.Wire()
	if w == nil {
		t.Fatal("wire vector is nil")
	}
	dbPath := filepath.Join(t.TempDir(), "store.db")
	s, err := store.Open(dbPath, store.Options{
		Registry:    projection.Base(),
		BusyTimeout: 5 * time.Second,
	})
	if err != nil {
		t.Fatalf("store.Open failed: %v", err)
	}
	t.Cleanup(func() { _ = s.Close() })
	if _, err := s.Initialize(); err != nil {
		t.Fatalf("store.Initialize failed: %v", err)
	}
	return s, w
}

func newTestGate(t *testing.T, s *store.Store, clock store.Clock) *Gate {
	t.Helper()
	g := New(Options{
		Store:     s,
		Artifacts: newFakeArtifactStore(),
		Clock:     clock,
	})
	if err := RegisterM1(g); err != nil {
		t.Fatalf("RegisterM1 failed: %v", err)
	}
	return g
}

func bindingFromWire(w *testvectors.WireVector) Binding {
	b := Binding{
		ActorID:     w.Binding["actor_id"].(string),
		Role:        w.Binding["role"].(string),
		PrincipalID: w.Binding["principal_id"].(string),
	}
	if c, ok := w.Binding["commitment"].(string); ok {
		b.Commitment = &c
	}
	if a, ok := w.Binding["assignment"].(string); ok {
		b.Assignment = &a
	}
	return b
}

func bindingFromMap(m map[string]any) Binding {
	b := Binding{}
	if a, ok := m["actor_id"].(string); ok {
		b.ActorID = a
	}
	if r, ok := m["role"].(string); ok {
		b.Role = r
	}
	if pr, ok := m["principal_id"].(string); ok {
		b.PrincipalID = pr
	}
	if c, ok := m["commitment"].(string); ok {
		b.Commitment = &c
	}
	if asg, ok := m["assignment"].(string); ok {
		b.Assignment = &asg
	}
	if m["unexpected"] != nil {
		b.Attachments = map[string]any{"unexpected": m["unexpected"]}
	}
	return b
}

func requireErrorCode(t *testing.T, err error, wantCode string) {
	t.Helper()
	if err == nil {
		t.Fatalf("expected error code %q, got nil", wantCode)
	}
	var ce *codec.Error
	if errors.As(err, &ce) && ce.Code == wantCode {
		return
	}
	type coder interface{ Code() string }
	var c coder
	if errors.As(err, &c) && c.Code() == wantCode {
		return
	}
	t.Fatalf("error %v does not have code %q", err, wantCode)
}

func encodeProposal(t *testing.T, prop map[string]any) []byte {
	t.Helper()
	raw, err := codec.Encode(prop)
	if err != nil {
		t.Fatalf("codec.Encode proposal failed: %v", err)
	}
	return raw
}

func toMapSlice(v any) []map[string]any {
	if s, ok := v.([]map[string]any); ok {
		return s
	}
	items := v.([]any)
	res := make([]map[string]any, len(items))
	for i, it := range items {
		res[i] = it.(map[string]any)
	}
	return res
}

type parityInvalidProposal struct {
	Name          string `json:"name"`
	Raw           string `json:"raw"`
	ExpectedError string `json:"expected_error"`
}

type parityInvalidBinding struct {
	Name          string         `json:"name"`
	Binding       map[string]any `json:"binding"`
	ExpectedError string         `json:"expected_error"`
}

type parityInvalidOp struct {
	Name         string          `json:"name"`
	Binding      map[string]any  `json:"binding"`
	Proposal     json.RawMessage `json:"proposal"`
	ExpectedCode string          `json:"expected_code"`
}

type parityData struct {
	Comment          string                  `json:"_comment"`
	InvalidProposals []parityInvalidProposal `json:"invalid_proposals"`
	InvalidBindings  []parityInvalidBinding  `json:"invalid_bindings"`
	InvalidOps       []parityInvalidOp       `json:"invalid_ops"`
}

func readParity(t *testing.T) *parityData {
	t.Helper()
	data, err := os.ReadFile("testdata/parity.json")
	if err != nil {
		t.Fatalf("read testdata/parity.json failed: %v", err)
	}
	var p parityData
	if err := json.Unmarshal(data, &p); err != nil {
		t.Fatalf("unmarshal parity.json failed: %v", err)
	}
	return &p
}
