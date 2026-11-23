package main

import (
	"os"
	"path/filepath"
	"testing"
)

// CI checks out only the package branch, so origin/main exists but a local
// main does not. Scope and Contracts must work from origin/main.
func TestScopeAndContractsWithoutLocalMain(t *testing.T) {
	defer guard(t)

	repo := setupFixtureRepo(t)
	runGit(t, repo, "checkout", "-b", "tests/P0-91")
	test := filepath.Join(repo, "tools", "p091", "p091_test.go")
	if err := os.WriteFile(test, []byte("package p091\n\nimport \"testing\"\n\nfunc TestAC1_First(t *testing.T) { t.Fatal(\"unimplemented\") }\n\nfunc TestAC2_Second(t *testing.T) { t.Fatal(\"unimplemented\") }\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	runGit(t, repo, "commit", "-am", "tests for P0-91")

	clone := filepath.Join(t.TempDir(), "clone")
	runGit(t, filepath.Dir(clone), "clone", "--quiet", "--branch", "tests/P0-91", repo, clone)
	if out := runGit(t, clone, "branch", "--list", "main"); out != "" {
		t.Fatalf("clone unexpectedly has a local main: %q", out)
	}

	violations, err := Scope(clone, "P0-91", "tests/P0-91")
	if err != nil || len(violations) != 0 {
		t.Fatalf("Scope without a local main = %v, %v; want no violations", violations, err)
	}
	violations, err = Contracts(clone, "P0-91", "tests/P0-91")
	if err != nil || len(violations) != 0 {
		t.Fatalf("Contracts without a local main = %v, %v; want no violations", violations, err)
	}
}
