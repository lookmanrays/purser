package main

import (
	"os"
	"path/filepath"
	"testing"
)

// A build branch that merges main inherits contract changes approved on main
// after its freeze (P1-02a inherited #23's owner move). Those are not changes
// made by the build; a contract edit the build makes itself still is.
func TestContractsIgnoresChangesInheritedFromMain(t *testing.T) {
	defer guard(t)

	repo := setupFixtureRepo(t)
	contractFile := filepath.Join(repo, "contracts", "test", "module.yaml")
	write := func(version string) {
		t.Helper()
		body := "contract: test/module\nkind: module\nversion: \"" + version + "\"\n"
		if err := os.WriteFile(contractFile, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	runGit(t, repo, "checkout", "main")
	write("3")
	runGit(t, repo, "commit", "-am", "approved contract change on main after the freeze")

	runGit(t, repo, "checkout", "-b", "build/P0-91", "frozen/P0-91")
	runGit(t, repo, "merge", "--no-edit", "main")
	violations, err := Contracts(repo, "P0-91", "build/P0-91")
	if err != nil {
		t.Fatalf("Contracts failed: %v", err)
	}
	if len(violations) != 0 {
		t.Fatalf("contract change inherited from main reported as a build change: %v", violations)
	}

	write("4")
	runGit(t, repo, "commit", "-am", "contract change made by the build")
	violations, err = Contracts(repo, "P0-91", "build/P0-91")
	if err != nil {
		t.Fatalf("Contracts failed: %v", err)
	}
	if len(violations) == 0 {
		t.Fatal("contract change made by the build was not reported")
	}
}
