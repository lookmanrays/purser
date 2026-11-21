package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestAC8_RedMissingTestACn(t *testing.T) {
	defer guard(t)

	repo := setupFixtureRepo(t)

	// In P0-91 brief, there is AC-1 and AC-2.
	// We replace p091_test.go with a test file having only TestAC1_, missing TestAC2_.
	missingAC2Test := filepath.Join(repo, "tools", "p091", "p091_test.go")
	content := `package p091

import "testing"

func TestAC1_OnlyOne(t *testing.T) {
	t.Fatal("fail")
}
`
	if err := os.WriteFile(missingAC2Test, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}

	err := Red(repo, "P0-91")
	if err == nil {
		t.Fatal("expected Red to fail when an AC lacks a TestACn_ test, got nil")
	}
	if !strings.Contains(err.Error(), "AC-2") && !strings.Contains(err.Error(), "TestAC2_") {
		t.Fatalf("expected error mentioning missing AC-2/TestAC2_, got: %v", err)
	}
}

func TestAC8_RedPassesOnStubsFails(t *testing.T) {
	defer guard(t)

	repo := setupFixtureRepo(t)

	// AC-1 fails, but AC-2 passes unexpectedly (does not fail on stub)
	passingTest := filepath.Join(repo, "tools", "p091", "p091_test.go")
	content := `package p091

import "testing"

func TestAC1_Fails(t *testing.T) {
	t.Fatal("failed as expected")
}

func TestAC2_PassesUnexpectedly(t *testing.T) {
	// does not fail
}
`
	if err := os.WriteFile(passingTest, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}

	err := Red(repo, "P0-91")
	if err == nil {
		t.Fatal("expected Red to fail when any test passes on stubs, got nil")
	}
	if !strings.Contains(strings.ToLower(err.Error()), "passed") && !strings.Contains(strings.ToLower(err.Error()), "pass") {
		t.Fatalf("expected error mentioning unexpected passing test, got: %v", err)
	}
}

func TestAC8_RedAllFailPasses(t *testing.T) {
	defer guard(t)

	repo := setupFixtureRepo(t)

	// Both TestAC1_ and TestAC2_ fail on stubs
	failingTests := filepath.Join(repo, "tools", "p091", "p091_test.go")
	content := `package p091

import "testing"

func TestAC1_Fails(t *testing.T) {
	t.Fatal("unimplemented")
}

func TestAC2_Fails(t *testing.T) {
	t.Fatal("unimplemented")
}
`
	if err := os.WriteFile(failingTests, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}

	err := Red(repo, "P0-91")
	if err != nil {
		t.Fatalf("expected Red to pass when all AC tests fail on stubs, got error: %v", err)
	}
}

func TestAC8_RedCLI(t *testing.T) {
	defer guard(t)

	var stdout, stderr bytes.Buffer
	code := Run([]string{"red", "--id", "P0-91"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("expected exit code 0 for red CLI when all tests fail, got %d, stderr: %s", code, stderr.String())
	}
}
