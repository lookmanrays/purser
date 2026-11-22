package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestAC2_ScopeBuildBranchOutOfScope(t *testing.T) {
	defer guard(t)

	repo := setupFixtureRepo(t)
	runGit(t, repo, "checkout", "-b", "build/P0-91", "frozen/P0-91")

	// Touch an out-of-scope file
	disallowed := filepath.Join(repo, "internal", "unauthorized.go")
	if err := os.MkdirAll(filepath.Dir(disallowed), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(disallowed, []byte("package internal\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	runGit(t, repo, "add", "-A")
	runGit(t, repo, "commit", "-m", "add disallowed file")

	violations, err := Scope(repo, "P0-91", "build/P0-91")
	if err != nil {
		t.Fatalf("Scope failed: %v", err)
	}
	if len(violations) == 0 {
		t.Fatalf("expected scope violations for build branch, got none")
	}

	found := false
	for _, v := range violations {
		if strings.Contains(v, "internal/unauthorized.go") {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("expected violation naming internal/unauthorized.go, got: %v", violations)
	}
}

func TestAC2_ScopeBuildBranchFrozenPrecedence(t *testing.T) {
	defer guard(t)

	repo := setupFixtureRepo(t)
	runGit(t, repo, "checkout", "-b", "build/P0-91", "frozen/P0-91")

	// P0-91 has scope.write: [tools/p091/**] and tests_frozen: [tools/p091/*_test.go]
	// Modifying a frozen test on a build branch must fail, because frozen paths take precedence.
	frozenTest := filepath.Join(repo, "tools", "p091", "p091_test.go")
	if err := os.WriteFile(frozenTest, []byte("package p091\n// modified frozen test\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	runGit(t, repo, "add", "-A")
	runGit(t, repo, "commit", "-m", "modify frozen test on build branch")

	violations, err := Scope(repo, "P0-91", "build/P0-91")
	if err != nil {
		t.Fatalf("Scope failed: %v", err)
	}
	if len(violations) == 0 {
		t.Fatalf("expected scope violation for modifying frozen test on build branch, got none")
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

func TestAC2_ScopeBuildBranchDoublestarGlob(t *testing.T) {
	defer guard(t)

	repo := setupFixtureRepo(t)
	runGit(t, repo, "checkout", "-b", "build/P0-91", "frozen/P0-91")

	// Doublestar glob tools/p091/** should allow deep subdirectories
	deepFile := filepath.Join(repo, "tools", "p091", "sub", "pkg", "deep.go")
	if err := os.MkdirAll(filepath.Dir(deepFile), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(deepFile, []byte("package pkg\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	runGit(t, repo, "add", "-A")
	runGit(t, repo, "commit", "-m", "add deep file under tools/p091/**")

	violations, err := Scope(repo, "P0-91", "build/P0-91")
	if err != nil {
		t.Fatalf("Scope failed: %v", err)
	}
	if len(violations) != 0 {
		t.Fatalf("expected no violations for file matching doublestar glob tools/p091/**, got: %v", violations)
	}
}

func TestAC2_ScopeBuildBranchAllowedChanges(t *testing.T) {
	defer guard(t)

	repo := setupFixtureRepo(t)
	runGit(t, repo, "checkout", "-b", "build/P0-91", "frozen/P0-91")

	// Allowed: delete zz_stub.go, add implementation file in scope.write, add docs/packages/P0-91.*
	_ = os.Remove(filepath.Join(repo, "tools", "p091", "zz_stub.go"))
	impl := filepath.Join(repo, "tools", "p091", "impl.go")
	if err := os.WriteFile(impl, []byte("package p091\nfunc Real() {}\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	notes := filepath.Join(repo, "docs", "packages", "P0-91.contradiction.md")
	if err := os.WriteFile(notes, []byte("# Contradiction note\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	runGit(t, repo, "add", "-A")
	runGit(t, repo, "commit", "-m", "implement package and delete stub")

	violations, err := Scope(repo, "P0-91", "build/P0-91")
	if err != nil {
		t.Fatalf("Scope failed: %v", err)
	}
	if len(violations) != 0 {
		t.Fatalf("expected no violations for valid build changes, got: %v", violations)
	}
}

func TestAC2_ScopeCLI(t *testing.T) {
	defer guard(t)

	var stdout, stderr bytes.Buffer
	code := Run([]string{"scope", "--id", "P0-91"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("expected exit code 0 for valid scope, got %d, stderr: %s", code, stderr.String())
	}
}

func TestAC3_ScopeTestsBranchAllowedPaths(t *testing.T) {
	defer guard(t)

	repo := setupFixtureRepo(t)
	runGit(t, repo, "checkout", "-b", "tests/P0-91", "main")

	// On tests branch, allowed:
	// 1. tests_frozen: tools/p091/*_test.go
	// 2. zz_stub.go in scope.write pkg dirs: tools/p091/zz_stub.go
	// 3. docs/packages/P0-91.*
	newTest := filepath.Join(repo, "tools", "p091", "extra_test.go")
	if err := os.WriteFile(newTest, []byte("package p091\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	stubFile := filepath.Join(repo, "tools", "p091", "zz_stub.go")
	if err := os.WriteFile(stubFile, []byte("package p091\n// updated stub\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	notes := filepath.Join(repo, "docs", "packages", "P0-91.notes.md")
	if err := os.WriteFile(notes, []byte("# Notes\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	runGit(t, repo, "add", "-A")
	runGit(t, repo, "commit", "-m", "valid test branch changes")

	violations, err := Scope(repo, "P0-91", "tests/P0-91")
	if err != nil {
		t.Fatalf("Scope failed: %v", err)
	}
	if len(violations) != 0 {
		t.Fatalf("expected no violations on valid tests branch, got: %v", violations)
	}
}

func TestAC3_ScopeTestsBranchDisallowedPath(t *testing.T) {
	defer guard(t)

	repo := setupFixtureRepo(t)
	runGit(t, repo, "checkout", "-b", "tests/P0-91", "main")

	// Disallowed: implementation file on tests branch
	impl := filepath.Join(repo, "tools", "p091", "impl.go")
	if err := os.WriteFile(impl, []byte("package p091\nfunc SneakyImpl() {}\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	runGit(t, repo, "add", "-A")
	runGit(t, repo, "commit", "-m", "add disallowed implementation file on tests branch")

	violations, err := Scope(repo, "P0-91", "tests/P0-91")
	if err != nil {
		t.Fatalf("Scope failed: %v", err)
	}
	if len(violations) == 0 {
		t.Fatalf("expected violation for implementation file on tests branch, got none")
	}
	found := false
	for _, v := range violations {
		if strings.Contains(v, "tools/p091/impl.go") {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("expected violation naming tools/p091/impl.go, got: %v", violations)
	}
}

func TestAC3_ScopeTestsBranchStubsInScopePkg(t *testing.T) {
	defer guard(t)

	repo := setupFixtureRepo(t)
	runGit(t, repo, "checkout", "-b", "tests/P0-91", "main")

	// Disallowed: zz_stub.go in a directory outside P0-91's scope.write
	outOfScopeStub := filepath.Join(repo, "tools", "p092", "zz_stub.go")
	if err := os.WriteFile(outOfScopeStub, []byte("package p092\n// illegal stub edit\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	runGit(t, repo, "add", "-A")
	runGit(t, repo, "commit", "-m", "touch stub in out-of-scope package")

	violations, err := Scope(repo, "P0-91", "tests/P0-91")
	if err != nil {
		t.Fatalf("Scope failed: %v", err)
	}
	if len(violations) == 0 {
		t.Fatalf("expected violation for stub in out-of-scope package, got none")
	}
	found := false
	for _, v := range violations {
		if strings.Contains(v, "tools/p092/zz_stub.go") {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("expected violation naming tools/p092/zz_stub.go, got: %v", violations)
	}
}

func TestAC6_RenamedFileCheckedOldAndNewPaths(t *testing.T) {
	defer guard(t)

	t.Run("renamed from in-scope to out-of-scope", func(t *testing.T) {
		defer guard(t)

		repo := setupFixtureRepo(t)
		runGit(t, repo, "checkout", "-b", "build/P0-91", "frozen/P0-91")

		// Create an in-scope file in base
		runGit(t, repo, "checkout", "main")
		inScope := filepath.Join(repo, "tools", "p091", "movable.go")
		if err := os.WriteFile(inScope, []byte("package p091\n"), 0o644); err != nil {
			t.Fatal(err)
		}
		runGit(t, repo, "add", "-A")
		runGit(t, repo, "commit", "-m", "add movable")
		runGit(t, repo, "tag", "-f", "frozen/P0-91")

		// On build branch, rename it to out-of-scope path
		runGit(t, repo, "checkout", "build/P0-91")
		runGit(t, repo, "reset", "--hard", "frozen/P0-91")
		outOfScopeDir := filepath.Join(repo, "internal", "escaped")
		if err := os.MkdirAll(outOfScopeDir, 0o755); err != nil {
			t.Fatal(err)
		}
		runGit(t, repo, "mv", "tools/p091/movable.go", "internal/escaped/movable.go")
		runGit(t, repo, "commit", "-m", "rename file to out-of-scope path")

		violations, err := Scope(repo, "P0-91", "build/P0-91")
		if err != nil {
			t.Fatalf("Scope failed: %v", err)
		}
		if len(violations) == 0 {
			t.Fatalf("expected violation for renamed file ending up out-of-scope, got none")
		}
		found := false
		for _, v := range violations {
			if strings.Contains(v, "internal/escaped/movable.go") || strings.Contains(v, "tools/p091/movable.go") {
				found = true
				break
			}
		}
		if !found {
			t.Fatalf("expected violation naming old or new path, got: %v", violations)
		}
	})

	t.Run("renamed from out-of-scope to in-scope", func(t *testing.T) {
		defer guard(t)

		repo := setupFixtureRepo(t)

		// Create out-of-scope file in base
		outPath := filepath.Join(repo, "internal", "existing.go")
		if err := os.MkdirAll(filepath.Dir(outPath), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(outPath, []byte("package internal\n"), 0o644); err != nil {
			t.Fatal(err)
		}
		runGit(t, repo, "add", "-A")
		runGit(t, repo, "commit", "-m", "add existing out-of-scope file")
		runGit(t, repo, "tag", "-f", "frozen/P0-91")

		// On build branch, rename from out-of-scope to in-scope:
		// Since old path was outside scope.write, renaming it touches out-of-scope path!
		runGit(t, repo, "checkout", "-b", "build/P0-91", "frozen/P0-91")
		runGit(t, repo, "mv", "internal/existing.go", "tools/p091/imported.go")
		runGit(t, repo, "commit", "-m", "rename out-of-scope file into scope")

		violations, err := Scope(repo, "P0-91", "build/P0-91")
		if err != nil {
			t.Fatalf("Scope failed: %v", err)
		}
		if len(violations) == 0 {
			t.Fatalf("expected violation because old path was out of scope, got none")
		}
	})
}
