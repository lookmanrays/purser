package main

import (
	"os"
	"path/filepath"
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

// testdataPath returns the absolute path to a file under tools/benchcheck/testdata.
func testdataPath(t *testing.T, rel string) string {
	t.Helper()
	root := findRepoRoot(t)
	return filepath.Join(root, "tools", "benchcheck", "testdata", rel)
}
