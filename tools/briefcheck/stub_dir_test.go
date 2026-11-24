package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// A brief that adds files to an existing package may list them one by one
// (P1-02c: replay.go and verify.go). Its tests branch still needs zz_stub.go in
// that package's directory, and only there.
func TestScopeAllowsStubBesideListedFiles(t *testing.T) {
	defer guard(t)

	repo := setupFixtureRepo(t)
	brief, err := os.ReadFile(filepath.Join(repo, "docs", "packages", "P0-91.md"))
	if err != nil {
		t.Fatal(err)
	}
	text := strings.Replace(string(brief), "id: P0-91", "id: P0-94", 1)
	text = strings.Replace(text, "  write: [tools/p091/**]", "  write: [tools/p091/impl.go]", 1)
	text = strings.Replace(text, "tests_frozen: [tools/p091/*_test.go]", "tests_frozen: [tools/p091/new_test.go]", 1)

	runGit(t, repo, "checkout", "-b", "tests/P0-94")
	files := map[string]string{
		filepath.Join(repo, "docs", "packages", "P0-94.md"): text,
		filepath.Join(repo, "tools", "p091", "new_test.go"): "package p091\n",
		filepath.Join(repo, "tools", "p091", "zz_stub.go"):  "package p091\n\n// Stubbed is a P0-94 stub.\nfunc Stubbed() { panic(\"unimplemented: P0-94\") }\n",
	}
	for path, body := range files {
		if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	runGit(t, repo, "add", "-A")
	runGit(t, repo, "commit", "-m", "tests for P0-94")
	violations, err := Scope(repo, "P0-94", "tests/P0-94")
	if err != nil || len(violations) != 0 {
		t.Fatalf("stub beside a listed file = %v, %v; want no violations", violations, err)
	}

	other := filepath.Join(repo, "tools", "p092", "zz_stub.go")
	if err := os.WriteFile(other, []byte("package p092\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	runGit(t, repo, "add", "-A")
	runGit(t, repo, "commit", "-m", "stub in another package")
	violations, err = Scope(repo, "P0-94", "tests/P0-94")
	if err != nil || len(violations) != 1 || !strings.Contains(violations[0], "tools/p092/zz_stub.go") {
		t.Fatalf("stub outside the package = %v, %v; want one violation for tools/p092/zz_stub.go", violations, err)
	}
}
