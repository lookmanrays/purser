package main

import (
	"bytes"
	"io"
	"os"
	"os/exec"
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

// findRepoRoot finds the root of the purser repository.
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

// testdataPath returns the absolute path to a file or directory under tools/briefcheck/testdata.
func testdataPath(t *testing.T, rel string) string {
	t.Helper()
	root := findRepoRoot(t)
	return filepath.Join(root, "tools", "briefcheck", "testdata", rel)
}

// copyFile copies a single file from src to dst.
func copyFile(t *testing.T, src, dst string) {
	t.Helper()
	in, err := os.Open(src)
	if err != nil {
		t.Fatalf("open %s: %v", src, err)
	}
	defer func() { _ = in.Close() }()

	if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
		t.Fatalf("mkdir %s: %v", filepath.Dir(dst), err)
	}

	out, err := os.Create(dst)
	if err != nil {
		t.Fatalf("create %s: %v", dst, err)
	}
	defer func() { _ = out.Close() }()

	if _, err := io.Copy(out, in); err != nil {
		t.Fatalf("copy %s to %s: %v", src, dst, err)
	}
}

// copyDir recursively copies files and directories from src to dst.
func copyDir(t *testing.T, src, dst string) {
	t.Helper()
	entries, err := os.ReadDir(src)
	if err != nil {
		t.Fatalf("read dir %s: %v", src, err)
	}
	if err := os.MkdirAll(dst, 0o755); err != nil {
		t.Fatalf("mkdir %s: %v", dst, err)
	}
	for _, entry := range entries {
		s := filepath.Join(src, entry.Name())
		d := filepath.Join(dst, entry.Name())
		if entry.IsDir() {
			copyDir(t, s, d)
		} else {
			copyFile(t, s, d)
		}
	}
}

// runGit runs a git command in dir with standard test identity configuration.
func runGit(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(),
		"GIT_AUTHOR_NAME=Briefcheck Test",
		"GIT_AUTHOR_EMAIL=briefcheck@test.local",
		"GIT_COMMITTER_NAME=Briefcheck Test",
		"GIT_COMMITTER_EMAIL=briefcheck@test.local",
		"GIT_CONFIG_GLOBAL=/dev/null",
		"GIT_CONFIG_SYSTEM=/dev/null",
	)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		t.Fatalf("git %s failed: %v\nstderr: %s\nstdout: %s", strings.Join(args, " "), err, stderr.String(), stdout.String())
	}
	return stdout.String()
}

// setupFixtureRepo creates a temporary Git repository populated with testdata/fixtures/repo.
// It initializes Git, creates the initial commit on main, and tags frozen/P0-91.
func setupFixtureRepo(t *testing.T) string {
	t.Helper()
	tmp := t.TempDir()
	fixtureDir := testdataPath(t, filepath.Join("fixtures", "repo"))
	copyDir(t, fixtureDir, tmp)

	runGit(t, tmp, "init", "-b", "main")
	runGit(t, tmp, "config", "user.name", "Briefcheck Test")
	runGit(t, tmp, "config", "user.email", "briefcheck@test.local")
	runGit(t, tmp, "add", "-A")
	runGit(t, tmp, "commit", "-m", "initial main commit")
	runGit(t, tmp, "tag", "frozen/P0-91")

	return tmp
}
