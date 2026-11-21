package testvectors

import (
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"testing"
)

const zeroHash = "0000000000000000000000000000000000000000000000000000000000000000"

var sixteenV1RecordKinds = []string{
	"Commitment",
	"WorkItem",
	"Approval",
	"Assignment",
	"Result",
	"Obligation",
	"VerificationRun",
	"Observation",
	"Evidence",
	"Waiver",
	"Assumption",
	"Decision",
	"DecisionOpinion",
	"Effect",
	"AuthorityGrant",
	"EffectAttempt",
}

// guard turns a panic (for example from an unimplemented stub) into a test
// failure, so one failing test does not abort the whole test binary.
// Use as the first statement of every test and subtest: defer guard(t)
func guard(t *testing.T) {
	if r := recover(); r != nil {
		t.Fatalf("panic: %v", r)
	}
}

// repoRoot locates the repository root by finding go.mod from the current directory.
func repoRoot(t *testing.T) string {
	t.Helper()
	dir, err := os.Getwd()
	if err != nil {
		t.Fatalf("getwd: %v", err)
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, "go.mod")); err == nil {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			t.Fatal("could not find repo root with go.mod")
		}
		dir = parent
	}
}

// sha256Hex computes the hex-encoded SHA-256 of data.
func sha256Hex(data []byte) string {
	h := sha256.Sum256(data)
	return hex.EncodeToString(h[:])
}
