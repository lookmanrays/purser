package testvectors

import (
	"fmt"
	"os"
	"path/filepath"
	"testing"
)

// AC-6: CI fails if any file under testdata/vectors/ changes without a manifest update.
// Test plan: Check the manifest.

func TestAC6_ManifestMatchesDisk(t *testing.T) {
	defer guard(t)

	if err := VerifyManifest(); err != nil {
		t.Fatalf("VerifyManifest() failed on clean workspace: %v", err)
	}
}

func TestAC6_ManifestDriftDetection(t *testing.T) {
	defer guard(t)

	m := Manifest()
	if len(m) == 0 {
		t.Fatal("manifest is empty")
	}

	root := repoRoot(t)
	vectorsDir := filepath.Join(root, "testdata", "vectors")
	if _, err := os.Stat(vectorsDir); os.IsNotExist(err) {
		t.Skip("testdata/vectors directory does not exist yet")
	}

	// 1. Walk testdata/vectors and assert that every non-manifest file is in the manifest.
	visited := make(map[string]bool)
	err := filepath.Walk(vectorsDir, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() {
			return nil
		}
		rel, err := filepath.Rel(vectorsDir, path)
		if err != nil {
			return err
		}
		if rel == "MANIFEST.sha256" {
			return nil
		}
		visited[rel] = true
		wantHash, ok := m[rel]
		if !ok {
			return fmt.Errorf("file %s exists on disk but is missing from MANIFEST.sha256", rel)
		}
		b, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		gotHash := sha256Hex(b)
		if gotHash != wantHash {
			return fmt.Errorf("file %s hash %s differs from manifest %s", rel, gotHash, wantHash)
		}
		return nil
	})
	if err != nil {
		t.Fatalf("disk check against manifest: %v", err)
	}

	// 2. Assert that no stale files are in the manifest that don't exist on disk.
	for manifestPath := range m {
		if !visited[manifestPath] {
			t.Errorf("manifest lists %s which does not exist on disk", manifestPath)
		}
	}
}
