// Package testvectors provides typed loaders and raw fixtures for Purser conformance vectors.
package testvectors

import (
	"bufio"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"sort"
	"strings"
)

// WireVector represents the top-level sections of wire-v1.json.
type WireVector struct {
	Contract                string           `json:"contract"`
	Actor                   string           `json:"actor"`
	ClockByBatch            []string         `json:"clock_by_batch"`
	EncodingVector          EncodingVector   `json:"encoding_vector"`
	Proposals               []map[string]any `json:"proposals"`
	RequestDigests          []string         `json:"request_digests"`
	Events                  []map[string]any `json:"events"`
	Expected                ExpectedVector   `json:"expected"`
	Legacy                  LegacyVector     `json:"legacy"`
	IndependentCaseBranches []map[string]any `json:"independent_case_branches"`
	Binding                 map[string]any   `json:"binding"`
}

// EncodingVector is the encoding_vector section of wire-v1.json.
type EncodingVector struct {
	Value               any    `json:"value"`
	CanonicalUTF8       string `json:"canonical_utf8"`
	SHA256CanonicalUTF8 string `json:"sha256_canonical_utf8"`
}

// LegacyVector is the legacy section of wire-v1.json.
type LegacyVector struct {
	Source                 string           `json:"source"`
	Events                 []map[string]any `json:"events"`
	ExpectedProjectionHash string           `json:"expected_projection_hash"`
	ExpectedHeadHash       string           `json:"expected_head_hash"`
}

// ExpectedVector is the expected section of wire-v1.json.
type ExpectedVector struct {
	Init                         map[string]any   `json:"init"`
	InitialStatus                map[string]any   `json:"initial_status"`
	AdmissionReplies             []map[string]any `json:"admission_replies"`
	StatusByRevision             []map[string]any `json:"status_by_revision"`
	SnapshotAt4                  map[string]any   `json:"snapshot_at_4"`
	SnapshotAt8                  map[string]any   `json:"snapshot_at_8"`
	WhyActiveCommitment          map[string]any   `json:"why_active_commitment"`
	ReplayAsOf                   string           `json:"replay_as_of"`
	ReplayEqualsStatusAtRevision int64            `json:"replay_equals_status_at_revision"`
}

// LifecycleVector represents the top-level sections of lifecycle-v1.json.
type LifecycleVector struct {
	Contract                string           `json:"contract"`
	Fixture                 string           `json:"fixture"`
	Interpretation          string           `json:"interpretation"`
	Subjects                map[string]any   `json:"subjects"`
	Support                 []map[string]any `json:"support"`
	EvidenceBindings        []map[string]any `json:"evidence_bindings"`
	ImmutableEffect         map[string]any   `json:"immutable_effect"`
	OutcomeVectors          []map[string]any `json:"outcome_vectors"`
	Steps                   []map[string]any `json:"steps"`
	AlternativeCutoffOrders []map[string]any `json:"alternative_cutoff_orders"`
	AlternativeRecovery     []map[string]any `json:"alternative_recovery"`
	ContinuityOverlay       map[string]any   `json:"continuity_overlay"`
}

// LegacyStatusCheckpoint is one checkpoint in m2-legacy-status-v1.json.
type LegacyStatusCheckpoint struct {
	V              int64          `json:"v"`
	Revision       int64          `json:"revision"`
	HeadHash       string         `json:"head_hash"`
	ProjectionHash string         `json:"projection_hash"`
	AsOf           string         `json:"as_of"`
	Answers        map[string]any `json:"answers"`
}

// GoldenScenario holds the recorded events and snapshots of one reference scenario.
// It exposes exact lines unchanged as EventLines and SnapshotLines.
type GoldenScenario struct {
	Name          string           `json:"name"`
	Meta          GoldenMeta       `json:"meta"`
	Events        []GoldenEvent    `json:"events"`
	Snapshots     []GoldenSnapshot `json:"snapshots"`
	EventLines    [][]byte         `json:"-"`
	SnapshotLines [][]byte         `json:"-"`
}

// GoldenMeta holds the scenario metadata from meta.json.
type GoldenMeta struct {
	Scenario        string `json:"scenario"`
	ReferenceDigest string `json:"reference_digest"`
}

// GoldenEvent is one stored event from events.jsonl with all twelve fields.
type GoldenEvent struct {
	V            int64          `json:"v"`
	EventVersion int64          `json:"event_version"`
	Revision     int64          `json:"revision"`
	BatchStart   int64          `json:"batch_start"`
	BatchSize    int64          `json:"batch_size"`
	ActorID      string         `json:"actor_id"`
	ProposalID   string         `json:"proposal_id"`
	At           string         `json:"at"`
	Type         string         `json:"type"`
	Data         map[string]any `json:"data"`
	PrevHash     string         `json:"prev_hash"`
	Hash         string         `json:"hash"`
}

// GoldenSnapshot is one batch snapshot from snapshots.jsonl.
type GoldenSnapshot struct {
	Revision           int64          `json:"revision"`
	HeadHash           string         `json:"head_hash"`
	ProjectionChecksum string         `json:"projection_checksum"`
	Projection         map[string]any `json:"projection"`
}

// Records returns the projected records for this snapshot.
func (s GoldenSnapshot) Records() []map[string]any {
	raw, ok := s.Projection["records"]
	if !ok || raw == nil {
		return nil
	}
	items, ok := raw.([]any)
	if !ok {
		return nil
	}
	res := make([]map[string]any, 0, len(items))
	for _, it := range items {
		if m, ok := it.(map[string]any); ok {
			res = append(res, m)
		}
	}
	return res
}

var vectorsRoot = func() string {
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		panic("runtime.Caller failed")
	}
	dir := filepath.Dir(file)
	internal := filepath.Dir(dir)
	root := filepath.Dir(internal)
	return filepath.Join(root, "testdata", "vectors")
}()

func decodeNumbers(val any) any {
	switch v := val.(type) {
	case json.Number:
		if i, err := v.Int64(); err == nil {
			return i
		}
		if f, err := v.Float64(); err == nil {
			return f
		}
		return v.String()
	case map[string]any:
		m := make(map[string]any, len(v))
		for k, child := range v {
			m[k] = decodeNumbers(child)
		}
		return m
	case []any:
		s := make([]any, len(v))
		for i, child := range v {
			s[i] = decodeNumbers(child)
		}
		return s
	default:
		return val
	}
}

// decodeInts decodes b into v and converts every JSON number held in a
// dynamic field (any, map[string]any or a slice of them) to int64, so kernel
// tests see the same integers the canonical codec accepts, never float64.
func decodeInts(b []byte, v any) error {
	dec := json.NewDecoder(bytes.NewReader(b))
	dec.UseNumber()
	if err := dec.Decode(v); err != nil {
		return err
	}
	normalizeNumbers(reflect.ValueOf(v))
	return nil
}

// normalizeNumbers walks typed values and applies decodeNumbers to every
// dynamic value it reaches.
func normalizeNumbers(v reflect.Value) {
	switch v.Kind() {
	case reflect.Pointer:
		if !v.IsNil() {
			normalizeNumbers(v.Elem())
		}
	case reflect.Struct:
		for i := 0; i < v.NumField(); i++ {
			if f := v.Field(i); f.CanSet() {
				normalizeNumbers(f)
			}
		}
	case reflect.Slice, reflect.Array:
		for i := 0; i < v.Len(); i++ {
			normalizeNumbers(v.Index(i))
		}
	case reflect.Map:
		if v.Type().Elem().Kind() != reflect.Interface {
			return
		}
		for _, k := range v.MapKeys() {
			if child := v.MapIndex(k).Interface(); child != nil {
				v.SetMapIndex(k, reflect.ValueOf(decodeNumbers(child)))
			}
		}
	case reflect.Interface:
		if !v.IsNil() && v.CanSet() {
			v.Set(reflect.ValueOf(decodeNumbers(v.Interface())))
		}
	}
}

// Wire returns the typed wire-v1 vector sections.
// If wire-v1.json is missing or corrupted, Wire panics.
func Wire() *WireVector {
	b := Raw("purser-v1/wire-v1.json")
	var w WireVector
	if err := decodeInts(b, &w); err != nil {
		panic(err)
	}
	return &w
}

// Lifecycle returns the typed lifecycle-v1 vector sections.
// If lifecycle-v1.json is missing or corrupted, Lifecycle panics.
func Lifecycle() *LifecycleVector {
	b := Raw("purser-v1/lifecycle-v1.json")
	var lc LifecycleVector
	if err := decodeInts(b, &lc); err != nil {
		panic(err)
	}
	return &lc
}

// LegacyStatus returns the typed checkpoints from m2-legacy-status-v1.json.
// If m2-legacy-status-v1.json is missing or corrupted, LegacyStatus panics.
func LegacyStatus() []LegacyStatusCheckpoint {
	b := Raw("purser-v1/m2-legacy-status-v1.json")
	var checkpoints []LegacyStatusCheckpoint
	if err := decodeInts(b, &checkpoints); err != nil {
		panic(err)
	}
	return checkpoints
}

// Golden returns the recorded events and snapshots for a reference scenario.
// If the scenario name is unknown or files cannot be read, Golden panics.
func Golden(name string) *GoldenScenario {
	eventsRaw := Raw(filepath.Join("golden", name, "events.jsonl"))
	snapshotsRaw := Raw(filepath.Join("golden", name, "snapshots.jsonl"))
	metaRaw := Raw(filepath.Join("golden", name, "meta.json"))

	var meta GoldenMeta
	if err := json.Unmarshal(metaRaw, &meta); err != nil {
		panic(err)
	}

	splitLines := func(raw []byte) [][]byte {
		trimmed := bytes.TrimSuffix(raw, []byte("\n"))
		if len(trimmed) == 0 {
			return nil
		}
		return bytes.Split(trimmed, []byte("\n"))
	}

	eventLines := splitLines(eventsRaw)
	snapshotLines := splitLines(snapshotsRaw)

	events := make([]GoldenEvent, len(eventLines))
	for i, line := range eventLines {
		var ev GoldenEvent
		dec := json.NewDecoder(bytes.NewReader(line))
		dec.UseNumber()
		if err := dec.Decode(&ev); err != nil {
			panic(fmt.Sprintf("decode event %d in scenario %s: %v", i, name, err))
		}
		if ev.Data != nil {
			ev.Data = decodeNumbers(ev.Data).(map[string]any)
		}
		events[i] = ev
	}

	snapshots := make([]GoldenSnapshot, len(snapshotLines))
	for i, line := range snapshotLines {
		var sn GoldenSnapshot
		dec := json.NewDecoder(bytes.NewReader(line))
		dec.UseNumber()
		if err := dec.Decode(&sn); err != nil {
			panic(fmt.Sprintf("decode snapshot %d in scenario %s: %v", i, name, err))
		}
		if sn.Projection != nil {
			sn.Projection = decodeNumbers(sn.Projection).(map[string]any)
		}
		snapshots[i] = sn
	}

	return &GoldenScenario{
		Name:          name,
		Meta:          meta,
		Events:        events,
		Snapshots:     snapshots,
		EventLines:    eventLines,
		SnapshotLines: snapshotLines,
	}
}

// GoldenNames returns the list of available golden scenario names.
func GoldenNames() []string {
	dir := filepath.Join(vectorsRoot, "golden")
	entries, err := os.ReadDir(dir)
	if err != nil {
		panic(err)
	}
	var names []string
	for _, e := range entries {
		if e.IsDir() {
			names = append(names, e.Name())
		}
	}
	sort.Strings(names)
	return names
}

// Raw returns the exact bytes of a vector file.
// If path does not exist or cannot be read, Raw panics.
func Raw(path string) []byte {
	fullPath := filepath.Join(vectorsRoot, filepath.FromSlash(path))
	b, err := os.ReadFile(fullPath)
	if err != nil {
		panic(err)
	}
	return b
}

// Manifest returns the mapping of relative paths to SHA-256 digests in MANIFEST.sha256.
// If MANIFEST.sha256 is missing or cannot be read, Manifest panics.
func Manifest() map[string]string {
	data := Raw("MANIFEST.sha256")
	m := make(map[string]string)
	scanner := bufio.NewScanner(bytes.NewReader(data))
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) >= 2 {
			hash := fields[0]
			relPath := strings.TrimSpace(line[len(hash):])
			relClean := filepath.Clean(filepath.FromSlash(relPath))
			m[filepath.ToSlash(relClean)] = hash
			m[relClean] = hash
		}
	}
	if err := scanner.Err(); err != nil {
		panic(err)
	}
	return m
}

// VerifyManifest checks that all files in testdata/vectors match MANIFEST.sha256.
// It returns an error describing any missing, extra, or modified files.
func VerifyManifest() error {
	m := Manifest()
	if len(m) == 0 {
		return errors.New("manifest is empty")
	}

	visited := make(map[string]bool)
	err := filepath.Walk(vectorsRoot, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() {
			return nil
		}
		rel, err := filepath.Rel(vectorsRoot, path)
		if err != nil {
			return err
		}
		relSlash := filepath.ToSlash(rel)
		if relSlash == "MANIFEST.sha256" {
			return nil
		}
		visited[rel] = true
		visited[relSlash] = true

		wantHash, ok := m[relSlash]
		if !ok {
			wantHash, ok = m[rel]
		}
		if !ok {
			return fmt.Errorf("file %s exists on disk but is missing from MANIFEST.sha256", rel)
		}
		b, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		h := sha256.Sum256(b)
		gotHash := hex.EncodeToString(h[:])
		if gotHash != wantHash {
			return fmt.Errorf("file %s hash %s differs from manifest %s", rel, gotHash, wantHash)
		}
		return nil
	})
	if err != nil {
		return err
	}

	for manifestPath := range m {
		if !visited[manifestPath] {
			full := filepath.Join(vectorsRoot, filepath.FromSlash(manifestPath))
			if _, err := os.Stat(full); os.IsNotExist(err) {
				return fmt.Errorf("manifest lists %s which does not exist on disk", manifestPath)
			}
		}
	}
	return nil
}
