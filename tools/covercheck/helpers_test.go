package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// guard catches panics from unimplemented stubs and records them as test failures.
func guard(t *testing.T) {
	t.Helper()
	if r := recover(); r != nil {
		t.Fatalf("panic: %v", r)
	}
}

// findRepoRoot finds the root of the purser repository containing AGENTS.md.
func findRepoRoot(t *testing.T) string {
	t.Helper()
	dir, err := os.Getwd()
	if err != nil {
		t.Fatalf("getwd: %v", err)
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, "AGENTS.md")); err == nil {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			t.Fatal("could not find repository root containing AGENTS.md")
		}
		dir = parent
	}
}

// testdataPath returns the absolute path to a file under tools/covercheck/testdata.
func testdataPath(t *testing.T, rel string) string {
	t.Helper()
	root := findRepoRoot(t)
	return filepath.Join(root, "tools", "covercheck", "testdata", rel)
}

// setupFuzzModule copies the fuzzmodule fixture from testdata into a temporary directory,
// restoring fuzz_test.go files so that go test -list works in it.
func setupFuzzModule(t *testing.T) string {
	t.Helper()
	tmp := t.TempDir()
	src := testdataPath(t, "fuzzmodule")

	err := filepath.Walk(src, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(src, path)
		if err != nil {
			return err
		}
		dst := filepath.Join(tmp, rel)
		if info.IsDir() {
			return os.MkdirAll(dst, 0o755)
		}
		dst = strings.TrimSuffix(dst, ".txt")
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		return os.WriteFile(dst, data, 0o644)
	})
	if err != nil {
		t.Fatalf("setupFuzzModule failed: %v", err)
	}
	return tmp
}
