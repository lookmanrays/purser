package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// writeIncrementBrief adds P0-93, a package that extends tools/p091 after
// P0-91 has merged: its own tests live in new_test.go, while p091_test.go
// holds the ancestor's tests, which pass.
func writeIncrementBrief(t *testing.T, repo, criteria, newTests string) {
	t.Helper()
	brief, err := os.ReadFile(filepath.Join(repo, "docs", "packages", "P0-91.md"))
	if err != nil {
		t.Fatal(err)
	}
	text := strings.Replace(string(brief), "id: P0-91", "id: P0-93", 1)
	text = strings.Replace(text, "tests_frozen: [tools/p091/*_test.go]", "tests_frozen: [tools/p091/new_test.go]", 1)
	text = strings.Replace(text, "- AC-1 First criterion.\n- AC-2 Second criterion.\n", criteria, 1)
	files := map[string]string{
		filepath.Join(repo, "docs", "packages", "P0-93.md"): text,
		filepath.Join(repo, "tools", "p091", "p091_test.go"): "package p091\n\nimport \"testing\"\n\nfunc TestAC1_AncestorPasses(t *testing.T) {}\n",
		filepath.Join(repo, "tools", "p091", "new_test.go"):  "package p091\n\nimport \"testing\"\n\n" + newTests,
		filepath.Join(repo, "tools", "p091", "testdata", "fixture_test.go"): "package fixture\n\nimport \"testing\"\n\nfunc TestAC2_InTestdata(t *testing.T) {}\n",
	}
	for path, body := range files {
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
}

func TestRedIncrementJudgesOnlyOwnTests(t *testing.T) {
	defer guard(t)

	repo := setupFixtureRepo(t)
	writeIncrementBrief(t, repo,
		"- AC-1 (baseline) An invariant the existing code holds.\n- AC-2 New behaviour.\n",
		"func TestAC1_InvariantHolds(t *testing.T) {}\n\nfunc TestAC2_NewBehaviour(t *testing.T) { t.Fatal(\"not built\") }\n")
	if err := Red(repo, "P0-93"); err != nil {
		t.Fatalf("ancestor tests, testdata files and a passing baseline AC must not fail red: %v", err)
	}
}

func TestRedIncrementBaselineTestMustPass(t *testing.T) {
	defer guard(t)

	repo := setupFixtureRepo(t)
	writeIncrementBrief(t, repo,
		"- AC-1 (baseline) An invariant the existing code holds.\n- AC-2 New behaviour.\n",
		"func TestAC1_InvariantHolds(t *testing.T) { t.Fatal(\"broken\") }\n\nfunc TestAC2_NewBehaviour(t *testing.T) { t.Fatal(\"not built\") }\n")
	err := Red(repo, "P0-93")
	if err == nil || !strings.Contains(err.Error(), "baseline AC-1") {
		t.Fatalf("expected a failing baseline test to be reported, got %v", err)
	}
}

func TestRedIncrementNewTestMustFail(t *testing.T) {
	defer guard(t)

	repo := setupFixtureRepo(t)
	writeIncrementBrief(t, repo,
		"- AC-1 (baseline) An invariant the existing code holds.\n- AC-2 New behaviour.\n",
		"func TestAC1_InvariantHolds(t *testing.T) {}\n\nfunc TestAC2_NewBehaviour(t *testing.T) {}\n")
	err := Red(repo, "P0-93")
	if err == nil || !strings.Contains(err.Error(), "TestAC2_NewBehaviour passed") {
		t.Fatalf("expected the passing non-baseline test to be reported, got %v", err)
	}
}

func TestRedRejectsBriefWithOnlyBaselineCriteria(t *testing.T) {
	defer guard(t)

	repo := setupFixtureRepo(t)
	writeIncrementBrief(t, repo,
		"- AC-1 (baseline) One invariant.\n- AC-2 (baseline) Another invariant.\n",
		"func TestAC1_One(t *testing.T) {}\n\nfunc TestAC2_Two(t *testing.T) {}\n")
	err := Red(repo, "P0-93")
	if err == nil || !strings.Contains(err.Error(), "every AC as baseline") {
		t.Fatalf("expected a brief with only baseline ACs to be rejected, got %v", err)
	}
}
