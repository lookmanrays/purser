package main

import (
	"path/filepath"
	"strings"
	"testing"
)

func TestAC7_CIWorkflowRunsBriefcheckOnBuildAndTests(t *testing.T) {
	defer guard(t)

	workflowPath := testdataPath(t, filepath.Join("workflows", "briefcheck_valid.yml"))
	violations, err := CheckBriefcheckWorkflow(workflowPath)
	if err != nil {
		t.Fatalf("CheckBriefcheckWorkflow failed: %v", err)
	}
	if len(violations) != 0 {
		t.Fatalf("expected valid briefcheck workflow to have no violations, got: %v", violations)
	}
}

func TestAC7_CIWorkflowMalformedTriggers(t *testing.T) {
	defer guard(t)

	tests := []struct {
		name        string
		file        string
		expectedErr string
	}{
		{
			name:        "invalid trigger branch",
			file:        "briefcheck_invalid_trigger.yml",
			expectedErr: "branches",
		},
		{
			name:        "missing make briefcheck",
			file:        "briefcheck_missing_make.yml",
			expectedErr: "make briefcheck",
		},
	}

	for _, tc := range tests {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			defer guard(t)

			path := testdataPath(t, filepath.Join("workflows", tc.file))
			violations, err := CheckBriefcheckWorkflow(path)
			if err != nil {
				t.Fatalf("CheckBriefcheckWorkflow error: %v", err)
			}
			if len(violations) == 0 {
				t.Fatalf("expected violations for %s, got none", tc.file)
			}
			matched := false
			for _, v := range violations {
				if strings.Contains(strings.ToLower(v), strings.ToLower(tc.expectedErr)) {
					matched = true
					break
				}
			}
			if !matched {
				t.Fatalf("expected violation mentioning %q in %v", tc.expectedErr, violations)
			}
		})
	}
}

func TestAC9_FreezeWorkflowSizeSOnly(t *testing.T) {
	defer guard(t)

	workflowPath := testdataPath(t, filepath.Join("workflows", "freeze_valid.yml"))
	violations, err := CheckFreezeWorkflow(workflowPath)
	if err != nil {
		t.Fatalf("CheckFreezeWorkflow failed: %v", err)
	}
	if len(violations) != 0 {
		t.Fatalf("expected valid freeze workflow to have no violations, got: %v", violations)
	}
}

func TestAC9_FreezeWorkflowNeverMOrL(t *testing.T) {
	defer guard(t)

	workflowPath := testdataPath(t, filepath.Join("workflows", "freeze_invalid_tags_m.yml"))
	violations, err := CheckFreezeWorkflow(workflowPath)
	if err != nil {
		t.Fatalf("CheckFreezeWorkflow error: %v", err)
	}
	if len(violations) == 0 {
		t.Fatalf("expected violations for tagging non-S packages, got none")
	}

	found := false
	for _, v := range violations {
		if strings.Contains(strings.ToLower(v), "size s") || strings.Contains(strings.ToLower(v), "tag") {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("expected size S tagging violation, got: %v", violations)
	}
}

func TestAC9_FreezeWorkflowRequiresVetBriefcheckRed(t *testing.T) {
	defer guard(t)

	workflowPath := testdataPath(t, filepath.Join("workflows", "freeze_missing_checks.yml"))
	violations, err := CheckFreezeWorkflow(workflowPath)
	if err != nil {
		t.Fatalf("CheckFreezeWorkflow error: %v", err)
	}
	if len(violations) == 0 {
		t.Fatalf("expected violations for missing required checks before tagging, got none")
	}

	found := false
	for _, v := range violations {
		if strings.Contains(strings.ToLower(v), "briefcheck") || strings.Contains(strings.ToLower(v), "red") {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("expected violation naming missing check (briefcheck/red), got: %v", violations)
	}
}
