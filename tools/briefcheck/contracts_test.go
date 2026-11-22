package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestAC5_ContractsChangeDisallowedWhenFalse(t *testing.T) {
	defer guard(t)

	repo := setupFixtureRepo(t)
	runGit(t, repo, "checkout", "-b", "build/P0-91", "frozen/P0-91")

	// P0-91 has contract_change: false
	// Modifying contracts/ must fail
	contractFile := filepath.Join(repo, "contracts", "test", "module.yaml")
	if err := os.WriteFile(contractFile, []byte("contract: test/module\nkind: module\nversion: \"2\"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	runGit(t, repo, "add", "-A")
	runGit(t, repo, "commit", "-m", "modify contract without permission")

	violations, err := Contracts(repo, "P0-91", "build/P0-91")
	if err != nil {
		t.Fatalf("Contracts failed: %v", err)
	}
	if len(violations) == 0 {
		t.Fatalf("expected violation for contract change when contract_change is false, got none")
	}

	found := false
	for _, v := range violations {
		if strings.Contains(v, "contracts/") {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("expected violation naming contracts/, got: %v", violations)
	}
}

func TestAC5_ContractsChangeAllowedWhenTrue(t *testing.T) {
	defer guard(t)

	repo := setupFixtureRepo(t)

	// Add brief with contract_change: true
	briefP072 := filepath.Join(repo, "docs", "packages", "P0-72.md")
	copyFile(t, testdataPath(t, filepath.Join("briefs", "P0-72.md")), briefP072)
	runGit(t, repo, "add", "-A")
	runGit(t, repo, "commit", "-m", "add P0-72 with contract_change: true")
	runGit(t, repo, "tag", "frozen/P0-72")

	runGit(t, repo, "checkout", "-b", "build/P0-72", "frozen/P0-72")

	// Modify contract
	contractFile := filepath.Join(repo, "contracts", "test", "module.yaml")
	if err := os.WriteFile(contractFile, []byte("contract: test/module\nkind: module\nversion: \"2\"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	runGit(t, repo, "add", "-A")
	runGit(t, repo, "commit", "-m", "modify contract with permission")

	violations, err := Contracts(repo, "P0-72", "build/P0-72")
	if err != nil {
		t.Fatalf("Contracts failed: %v", err)
	}
	if len(violations) != 0 {
		t.Fatalf("expected no violations when contract_change is true, got: %v", violations)
	}
}

func TestAC5_ContractsNoChangesPasses(t *testing.T) {
	defer guard(t)

	repo := setupFixtureRepo(t)
	runGit(t, repo, "checkout", "-b", "build/P0-91", "frozen/P0-91")

	violations, err := Contracts(repo, "P0-91", "build/P0-91")
	if err != nil {
		t.Fatalf("Contracts failed: %v", err)
	}
	if len(violations) != 0 {
		t.Fatalf("expected no violations when contracts are untouched, got: %v", violations)
	}
}

func TestAC5_ContractsCLI(t *testing.T) {
	defer guard(t)

	var stdout, stderr bytes.Buffer
	code := Run([]string{"contracts", "--id", "P0-91"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("expected exit code 0 for contracts CLI on clean state, got %d, stderr: %s", code, stderr.String())
	}
}
