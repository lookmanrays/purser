// Package testvectors provides typed loaders and raw fixtures for Purser conformance vectors.
package testvectors

// Stubs for P0-06, Conformance vectors and golden logs.
// The builder replaces this file with the implementation.

// unimplemented is a variable so static analysis does not treat the stubs as
// never returning.
var unimplemented = func() { panic("unimplemented: P0-06") }

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
func (GoldenSnapshot) Records() []map[string]any {
	unimplemented()
	return nil
}

// Wire returns the typed wire-v1 vector sections.
// If wire-v1.json is missing or corrupted, Wire panics.
func Wire() *WireVector {
	unimplemented()
	return nil
}

// Lifecycle returns the typed lifecycle-v1 vector sections.
// If lifecycle-v1.json is missing or corrupted, Lifecycle panics.
func Lifecycle() *LifecycleVector {
	unimplemented()
	return nil
}

// LegacyStatus returns the typed checkpoints from m2-legacy-status-v1.json.
// If m2-legacy-status-v1.json is missing or corrupted, LegacyStatus panics.
func LegacyStatus() []LegacyStatusCheckpoint {
	unimplemented()
	return nil
}

// Golden returns the recorded events and snapshots for a reference scenario.
// If the scenario name is unknown or files cannot be read, Golden panics.
func Golden(_ string) *GoldenScenario {
	unimplemented()
	return nil
}

// GoldenNames returns the list of available golden scenario names.
func GoldenNames() []string {
	unimplemented()
	return nil
}

// Raw returns the exact bytes of a vector file.
// If path does not exist or cannot be read, Raw panics.
func Raw(_ string) []byte {
	unimplemented()
	return nil
}

// Manifest returns the mapping of relative paths to SHA-256 digests in MANIFEST.sha256.
// If MANIFEST.sha256 is missing or cannot be read, Manifest panics.
func Manifest() map[string]string {
	unimplemented()
	return nil
}

// VerifyManifest checks that all files in testdata/vectors match MANIFEST.sha256.
// It returns an error describing any missing, extra, or modified files.
func VerifyManifest() error {
	unimplemented()
	return nil
}
