package main

import (
	"bytes"
	"path/filepath"
	"strings"
	"testing"
)

func TestAC1_ValidateRepoOwnBriefs(t *testing.T) {
	defer guard(t)

	repoRoot := findRepoRoot(t)
	violations, err := Validate(repoRoot)
	if err != nil {
		t.Fatalf("Validate failed unexpectedly: %v", err)
	}
	if len(violations) != 0 {
		t.Fatalf("Validate found unexpected violations on repo's own briefs: %v", violations)
	}
}

func TestAC1_ValidateMalformedFixtures(t *testing.T) {
	defer guard(t)

	tests := []struct {
		name        string
		file        string
		expectedKey string
	}{
		{
			name:        "missing key",
			file:        "invalid_missing_key.md",
			expectedKey: "size",
		},
		{
			name:        "id mismatch",
			file:        "P0-81.md",
			expectedKey: "id",
		},
		{
			name:        "phase mismatch",
			file:        "P0-82.md",
			expectedKey: "phase",
		},
		{
			name:        "bad size",
			file:        "P0-83.md",
			expectedKey: "size",
		},
		{
			name:        "size L ready",
			file:        "P0-84.md",
			expectedKey: "size",
		},
		{
			name:        "budget exceeded",
			file:        "P0-85.md",
			expectedKey: "budget",
		},
		{
			name:        "bad builder",
			file:        "P0-86.md",
			expectedKey: "builder",
		},
		{
			name:        "bad test author",
			file:        "P0-87.md",
			expectedKey: "test_author",
		},
		{
			name:        "same agent",
			file:        "P0-88.md",
			expectedKey: "same agent",
		},
		{
			name:        "bad reviewer",
			file:        "P0-89.md",
			expectedKey: "reviewer",
		},
		{
			name:        "bad merge",
			file:        "P0-90.md",
			expectedKey: "merge",
		},
		{
			name:        "auto merge size M",
			file:        "P0-71.md",
			expectedKey: "merge auto requires size S",
		},
		{
			name:        "auto merge contract change",
			file:        "P0-72.md",
			expectedKey: "contract change",
		},
		{
			name:        "auto merge no author",
			file:        "P0-73.md",
			expectedKey: "test author",
		},
		{
			name:        "contracts write without flag",
			file:        "P0-74.md",
			expectedKey: "contracts/",
		},
		{
			name:        "no author has frozen",
			file:        "P0-75.md",
			expectedKey: "tests_frozen",
		},
		{
			name:        "ready no frozen",
			file:        "P0-76.md",
			expectedKey: "tests_frozen",
		},
		{
			name:        "bad reuse",
			file:        "P0-64.md",
			expectedKey: "reuse",
		},
		{
			name:        "missing section",
			file:        "P0-61.md",
			expectedKey: "Context",
		},
		{
			name:        "ac numbering gap",
			file:        "P0-62.md",
			expectedKey: "AC numbering",
		},
		{
			name:        "no ac",
			file:        "P0-63.md",
			expectedKey: "no acceptance criteria",
		},
	}

	knownIDs := []string{"P0-71", "P0-72", "P0-73", "P0-74", "P0-75", "P0-76", "P0-81", "P0-82", "P0-83", "P0-84", "P0-85", "P0-86", "P0-87", "P0-88", "P0-89", "P0-90", "P0-61", "P0-62", "P0-63", "P0-64"}

	for _, tc := range tests {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			defer guard(t)

			path := testdataPath(t, filepath.Join("briefs", tc.file))
			violations, err := ValidateBrief(path, knownIDs)
			if err != nil {
				t.Fatalf("ValidateBrief error: %v", err)
			}
			if len(violations) == 0 {
				t.Fatalf("expected violations for %s, got none", tc.file)
			}
			matched := false
			for _, v := range violations {
				if strings.Contains(strings.ToLower(v), strings.ToLower(tc.expectedKey)) {
					matched = true
					break
				}
			}
			if !matched {
				t.Fatalf("expected violation mentioning %q in %v", tc.expectedKey, violations)
			}
		})
	}
}

func TestAC1_ValidateCycles(t *testing.T) {
	defer guard(t)

	tmp := t.TempDir()
	briefsDir := filepath.Join(tmp, "docs", "packages")
	copyFile(t, testdataPath(t, filepath.Join("briefs", "P0-79a.md")), filepath.Join(briefsDir, "P0-79a.md"))
	copyFile(t, testdataPath(t, filepath.Join("briefs", "P0-79b.md")), filepath.Join(briefsDir, "P0-79b.md"))

	violations, err := Validate(tmp)
	if err != nil {
		t.Fatalf("Validate failed: %v", err)
	}
	found := false
	for _, v := range violations {
		if strings.Contains(strings.ToLower(v), "cycle") {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("expected cycle error in violations, got: %v", violations)
	}
}

func TestAC1_ValidateUnknownDependencies(t *testing.T) {
	defer guard(t)

	path := testdataPath(t, filepath.Join("briefs", "P0-77.md"))
	violations, err := ValidateBrief(path, []string{"P0-77"})
	if err != nil {
		t.Fatalf("ValidateBrief failed: %v", err)
	}
	found := false
	for _, v := range violations {
		if strings.Contains(v, "unknown dependency") || strings.Contains(v, "P9-99") {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("expected unknown dependency violation, got: %v", violations)
	}
}

func TestAC1_ValidateBuilderEqualsTestAuthor(t *testing.T) {
	defer guard(t)

	path := testdataPath(t, filepath.Join("briefs", "P0-88.md"))
	violations, err := ValidateBrief(path, []string{"P0-88"})
	if err != nil {
		t.Fatalf("ValidateBrief failed: %v", err)
	}
	found := false
	for _, v := range violations {
		if strings.Contains(v, "builder and test_author are the same agent") {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("expected same agent error, got: %v", violations)
	}
}

func TestAC1_ValidateAutoMergeSizeM(t *testing.T) {
	defer guard(t)

	path := testdataPath(t, filepath.Join("briefs", "P0-71.md"))
	violations, err := ValidateBrief(path, []string{"P0-71"})
	if err != nil {
		t.Fatalf("ValidateBrief failed: %v", err)
	}
	found := false
	for _, v := range violations {
		if strings.Contains(v, "merge auto") || strings.Contains(v, "size S") {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("expected auto merge size S error, got: %v", violations)
	}
}

func TestAC1_ValidateSectionsAndACNumbering(t *testing.T) {
	defer guard(t)

	t.Run("missing section", func(t *testing.T) {
		defer guard(t)
		path := testdataPath(t, filepath.Join("briefs", "P0-61.md"))
		violations, err := ValidateBrief(path, []string{"P0-61"})
		if err != nil {
			t.Fatalf("ValidateBrief failed: %v", err)
		}
		found := false
		for _, v := range violations {
			if strings.Contains(v, "missing section") || strings.Contains(v, "Context") {
				found = true
				break
			}
		}
		if !found {
			t.Fatalf("expected missing section violation, got: %v", violations)
		}
	})

	t.Run("ac numbering gap", func(t *testing.T) {
		defer guard(t)
		path := testdataPath(t, filepath.Join("briefs", "P0-62.md"))
		violations, err := ValidateBrief(path, []string{"P0-62"})
		if err != nil {
			t.Fatalf("ValidateBrief failed: %v", err)
		}
		found := false
		for _, v := range violations {
			if strings.Contains(v, "AC numbering") || strings.Contains(v, "1..n") {
				found = true
				break
			}
		}
		if !found {
			t.Fatalf("expected AC numbering violation, got: %v", violations)
		}
	})
}

func TestAC1_ValidateCLI(t *testing.T) {
	defer guard(t)

	var stdout, stderr bytes.Buffer
	code := Run([]string{"validate"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("expected exit code 0 for validate on repo root, got %d, stderr: %s", code, stderr.String())
	}
}
