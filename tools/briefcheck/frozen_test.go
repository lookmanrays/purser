package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestAC4_FrozenModifiedFile(t *testing.T) {
	defer guard(t)

	repo := setupFixtureRepo(t)
	runGit(t, repo, "checkout", "-b", "build/P0-91", "frozen/P0-91")

	// Modify package's own frozen file
	frozenPath := filepath.Join(repo, "tools", "p091", "p091_test.go")
	if err := os.WriteFile(frozenPath, []byte("package p091\n// modified\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	runGit(t, repo, "add", "-A")
	runGit(t, repo, "commit", "-m", "modify frozen file")

	violations, err := Frozen(repo, "P0-91")
	if err != nil {
		t.Fatalf("Frozen failed: %v", err)
	}
	if len(violations) == 0 {
		t.Fatalf("expected violation for modified frozen file, got none")
	}

	found := false
	for _, v := range violations {
		if strings.Contains(v, "p091_test.go") {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("expected violation naming p091_test.go, got: %v", violations)
	}
}

func TestAC4_FrozenDeletedFile(t *testing.T) {
	defer guard(t)

	repo := setupFixtureRepo(t)
	runGit(t, repo, "checkout", "-b", "build/P0-91", "frozen/P0-91")

	// Delete package's own frozen file
	frozenPath := filepath.Join(repo, "tools", "p091", "p091_test.go")
	if err := os.Remove(frozenPath); err != nil {
		t.Fatal(err)
	}
	runGit(t, repo, "add", "-A")
	runGit(t, repo, "commit", "-m", "delete frozen file")

	violations, err := Frozen(repo, "P0-91")
	if err != nil {
		t.Fatalf("Frozen failed: %v", err)
	}
	if len(violations) == 0 {
		t.Fatalf("expected violation for deleted frozen file, got none")
	}

	found := false
	for _, v := range violations {
		if strings.Contains(v, "p091_test.go") {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("expected violation naming p091_test.go, got: %v", violations)
	}
}

func TestAC4_FrozenRenamedFile(t *testing.T) {
	defer guard(t)

	repo := setupFixtureRepo(t)
	runGit(t, repo, "checkout", "-b", "build/P0-91", "frozen/P0-91")

	runGit(t, repo, "mv", "tools/p091/p091_test.go", "tools/p091/p091_renamed_test.go")
	runGit(t, repo, "commit", "-m", "rename frozen file")

	violations, err := Frozen(repo, "P0-91")
	if err != nil {
		t.Fatalf("Frozen failed: %v", err)
	}
	if len(violations) == 0 {
		t.Fatalf("expected violation for renamed frozen file, got none")
	}
}

func TestAC4_FrozenOtherPackageDiffAgainstMain(t *testing.T) {
	defer guard(t)

	repo := setupFixtureRepo(t)
	runGit(t, repo, "checkout", "-b", "build/P0-91", "frozen/P0-91")

	// Modify another package's frozen file (P0-92's tests)
	otherFrozen := filepath.Join(repo, "tools", "p092", "p092_test.go")
	if err := os.WriteFile(otherFrozen, []byte("package p092\n// tampering with other frozen\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	runGit(t, repo, "add", "-A")
	runGit(t, repo, "commit", "-m", "modify other package frozen file")

	violations, err := Frozen(repo, "P0-91")
	if err != nil {
		t.Fatalf("Frozen failed: %v", err)
	}
	if len(violations) == 0 {
		t.Fatalf("expected violation for modifying other package frozen file relative to main, got none")
	}

	found := false
	for _, v := range violations {
		if strings.Contains(v, "p092_test.go") {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("expected violation naming p092_test.go, got: %v", violations)
	}
}

func TestAC4_FrozenUnchangedPasses(t *testing.T) {
	defer guard(t)

	repo := setupFixtureRepo(t)
	runGit(t, repo, "checkout", "-b", "build/P0-91", "frozen/P0-91")

	// Add implementation file, don't touch frozen tests
	impl := filepath.Join(repo, "tools", "p091", "impl.go")
	if err := os.WriteFile(impl, []byte("package p091\nfunc Real() {}\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	runGit(t, repo, "add", "-A")
	runGit(t, repo, "commit", "-m", "add implementation without touching frozen files")

	violations, err := Frozen(repo, "P0-91")
	if err != nil {
		t.Fatalf("Frozen failed: %v", err)
	}
	if len(violations) != 0 {
		t.Fatalf("expected no frozen violations when frozen files are unchanged, got: %v", violations)
	}
}

func TestAC4_FrozenCLI(t *testing.T) {
	defer guard(t)

	var stdout, stderr bytes.Buffer
	code := Run([]string{"frozen", "--id", "P0-91"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("expected exit code 0 for frozen CLI on clean state, got %d, stderr: %s", code, stderr.String())
	}
}
