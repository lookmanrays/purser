package testvectors

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

// AC-1: The three frozen files are byte-identical to the reference copies,
// and the manifest proves it.

func TestAC1_FrozenFilesByteIdentical(t *testing.T) {
	defer guard(t)

	files := []string{
		"wire-v1.json",
		"lifecycle-v1.json",
		"m2-legacy-status-v1.json",
	}
	root := repoRoot(t)

	for _, f := range files {
		t.Run(f, func(t *testing.T) {
			defer guard(t)
			got := Raw(filepath.Join("purser-v1", f))
			refPath := filepath.Join(root, "reference", "python", "docs", "contracts", f)
			want, err := os.ReadFile(refPath)
			if err != nil {
				t.Fatalf("read reference file %s: %v", refPath, err)
			}
			if !bytes.Equal(got, want) {
				t.Fatalf("file %s is not byte-identical to reference copy", f)
			}
		})
	}
}

func TestAC1_ManifestProvesChecksums(t *testing.T) {
	defer guard(t)

	m := Manifest()
	root := repoRoot(t)
	files := []string{
		"wire-v1.json",
		"lifecycle-v1.json",
		"m2-legacy-status-v1.json",
	}

	for _, f := range files {
		t.Run(f, func(t *testing.T) {
			defer guard(t)
			relPath := filepath.Join("purser-v1", f)
			manifestHash, ok := m[relPath]
			if !ok {
				t.Fatalf("manifest missing entry for %s", relPath)
			}
			refPath := filepath.Join(root, "reference", "python", "docs", "contracts", f)
			refBytes, err := os.ReadFile(refPath)
			if err != nil {
				t.Fatalf("read reference %s: %v", refPath, err)
			}
			wantHash := sha256Hex(refBytes)
			if manifestHash != wantHash {
				t.Fatalf("manifest hash %s does not match reference hash %s for %s", manifestHash, wantHash, f)
			}
		})
	}
}
