package testvectors

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

// AC-5: Running make golden twice gives identical files.

func TestAC5_MakeGoldenDeterministic(t *testing.T) {
	defer guard(t)

	names := GoldenNames()
	if len(names) == 0 {
		t.Fatal("no golden scenario names returned")
	}

	// 1. Verify that each scenario metadata contains deterministic attributes:
	// meta.json holds {scenario, reference_digest}. reference_digest must be 64
	// lowercase hex characters and equal across scenarios.
	var firstDigest string
	for _, name := range names {
		sc := Golden(name)
		if sc == nil {
			t.Fatalf("scenario %q is nil", name)
		}
		if sc.Meta.Scenario != name {
			t.Errorf("scenario %s: Meta.Scenario = %q, want %q", name, sc.Meta.Scenario, name)
		}
		digest := sc.Meta.ReferenceDigest
		if len(digest) != 64 {
			t.Errorf("scenario %s: Meta.ReferenceDigest len = %d, want 64", name, len(digest))
		}
		for _, c := range digest {
			isHex := (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')
			if !isHex {
				t.Errorf("scenario %s: Meta.ReferenceDigest %q is not lowercase hex", name, digest)
				break
			}
		}
		if firstDigest == "" {
			firstDigest = digest
		} else if digest != firstDigest {
			t.Errorf("scenario %s: Meta.ReferenceDigest %q != %q", name, digest, firstDigest)
		}
	}

	// 2. Opt-in regeneration check: running refdump twice and comparing with committed files.
	// Only runs when PURSER_GOLDEN=1 so make check stays fast and does not need Python or a recent Git.
	if os.Getenv("PURSER_GOLDEN") != "1" {
		t.Skip("skipping golden regeneration check; set PURSER_GOLDEN=1 to run")
	}

	root := repoRoot(t)
	refdumpScript := filepath.Join(root, "tools", "refdump", "refdump.py")
	if _, err := os.Stat(refdumpScript); err != nil {
		t.Fatalf("refdump script not found at %s", refdumpScript)
	}

	tmpDir1 := t.TempDir()
	tmpDir2 := t.TempDir()

	runRefdump := func(destDir string) {
		cmd := exec.Command("python3", "-I", refdumpScript, "--output", destDir)
		cmd.Dir = root
		out, err := cmd.CombinedOutput()
		if err != nil {
			t.Fatalf("running refdump failed: %v\nOutput:\n%s", err, string(out))
		}
	}

	runRefdump(tmpDir1)
	runRefdump(tmpDir2)

	// Compare tmpDir1 and tmpDir2 byte-for-byte across all files in all scenarios.
	for _, name := range names {
		for _, file := range []string{"events.jsonl", "snapshots.jsonl", "meta.json"} {
			p1 := filepath.Join(tmpDir1, name, file)
			p2 := filepath.Join(tmpDir2, name, file)
			b1, err := os.ReadFile(p1)
			if err != nil {
				t.Fatalf("read run 1 %s/%s: %v", name, file, err)
			}
			b2, err := os.ReadFile(p2)
			if err != nil {
				t.Fatalf("read run 2 %s/%s: %v", name, file, err)
			}
			if !bytes.Equal(b1, b2) {
				t.Fatalf("scenario %s file %s differs between two refdump runs (non-deterministic)", name, file)
			}

			// Also compare against Raw(filepath.Join("golden", name, file))
			committed := Raw(filepath.Join("golden", name, file))
			if !bytes.Equal(b1, committed) {
				t.Fatalf("scenario %s file %s differs from committed file in testdata/vectors", name, file)
			}
		}
	}
}
