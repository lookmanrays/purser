package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

var reFuzzAnchored = regexp.MustCompile(`-fuzz[ =]['"]?\^\$\{\{\s*matrix\.[^}]+?\}\}\$['"]?`)

func TestAC4_FuzzTargetsList(t *testing.T) {
	defer guard(t)

	moduleDir := setupFuzzModule(t)
	targets, err := FuzzTargets(moduleDir)
	if err != nil {
		t.Fatalf("FuzzTargets failed: %v", err)
	}

	expected := []FuzzTarget{
		{Package: "purser.test/fuzzmodule/pkgone", Name: "FuzzTargetA"},
		{Package: "purser.test/fuzzmodule/pkgone", Name: "FuzzTargetB"},
		{Package: "purser.test/fuzzmodule/pkgtwo", Name: "FuzzTargetC"},
	}

	if len(targets) != len(expected) {
		t.Fatalf("expected %d fuzz targets, got %d: %+v", len(expected), len(targets), targets)
	}

	for _, exp := range expected {
		found := false
		for _, act := range targets {
			if act.Package == exp.Package && act.Name == exp.Name {
				found = true
				break
			}
		}
		if !found {
			t.Fatalf("expected target %+v not found in %+v", exp, targets)
		}
	}

	// Verify non-target helper Fuzzy is never included
	for _, act := range targets {
		if act.Name == "Fuzzy" {
			t.Fatalf("non-target helper Fuzzy must not be listed: %+v", act)
		}
	}

	// Also test the CLI subcommand "covercheck fuzz-targets <dir>"
	var stdout, stderr bytes.Buffer
	code := Run([]string{"fuzz-targets", moduleDir}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("covercheck fuzz-targets CLI failed with exit code %d: %s", code, stderr.String())
	}
	var cliTargets []FuzzTarget
	if err := json.Unmarshal(stdout.Bytes(), &cliTargets); err != nil {
		t.Fatalf("failed to parse JSON from covercheck fuzz-targets: %v (output: %s)", err, stdout.String())
	}
	if len(cliTargets) != len(expected) {
		t.Fatalf("expected %d targets from CLI, got %d", len(expected), len(cliTargets))
	}
}

func TestAC4_CheckedInNightlyWorkflow(t *testing.T) {
	defer guard(t)

	workflowPath := filepath.Join(findRepoRoot(t), ".github", "workflows", "nightly.yml")
	data, err := os.ReadFile(workflowPath)
	if err != nil {
		t.Fatalf("failed to read checked-in .github/workflows/nightly.yml: %v", err)
	}

	var wf struct {
		On struct {
			Schedule []struct {
				Cron string `yaml:"cron"`
			} `yaml:"schedule"`
		} `yaml:"on"`
		Jobs map[string]struct {
			Needs          any `yaml:"needs"`
			TimeoutMinutes int `yaml:"timeout-minutes"`
			Strategy       struct {
				FailFast *bool `yaml:"fail-fast"`
				Matrix   any   `yaml:"matrix"`
			} `yaml:"strategy"`
			Steps []struct {
				Name string            `yaml:"name"`
				Run  string            `yaml:"run"`
				Uses string            `yaml:"uses"`
				If   string            `yaml:"if"`
				With map[string]string `yaml:"with"`
			} `yaml:"steps"`
		} `yaml:"jobs"`
	}
	if err := yaml.Unmarshal(data, &wf); err != nil {
		t.Fatalf("failed to parse nightly.yml as YAML: %v", err)
	}

	// 1. Schedule trigger via parsed on.schedule map
	if len(wf.On.Schedule) == 0 || strings.TrimSpace(wf.On.Schedule[0].Cron) == "" {
		t.Fatal("nightly workflow must declare a schedule trigger with cron in on.schedule")
	}

	// 2. No continue-on-error, || true, || exit 0, or set +e
	content := string(data)
	if strings.Contains(content, "continue-on-error: true") {
		t.Fatal("nightly workflow must not use continue-on-error: true")
	}
	if strings.Contains(content, "|| true") {
		t.Fatal("nightly workflow must not mask errors with '|| true'")
	}
	if strings.Contains(content, "|| exit 0") {
		t.Fatal("nightly workflow must not mask errors with '|| exit 0'")
	}
	if strings.Contains(content, "set +e") {
		t.Fatal("nightly workflow must not use 'set +e'")
	}

	// 3. Find the matrix job with fail-fast: false
	var matrixJobName string
	for name, job := range wf.Jobs {
		if job.Strategy.FailFast != nil && !*job.Strategy.FailFast {
			matrixJobName = name
			break
		}
	}
	if matrixJobName == "" {
		t.Fatal("nightly workflow must have a matrix fuzz job with fail-fast: false")
	}

	matrixJob := wf.Jobs[matrixJobName]

	// 4. Matrix job timeout in (60, 360]
	if matrixJob.TimeoutMinutes <= 60 || matrixJob.TimeoutMinutes > 360 {
		t.Fatalf("matrix job timeout-minutes must be in (60, 360], got %d", matrixJob.TimeoutMinutes)
	}

	// 5. The matrix must depend on and come from the list job
	var needsJobs []string
	switch v := matrixJob.Needs.(type) {
	case string:
		needsJobs = []string{v}
	case []any:
		for _, item := range v {
			if s, ok := item.(string); ok {
				needsJobs = append(needsJobs, s)
			}
		}
	}

	var listJobName string
	for _, n := range needsJobs {
		if listJob, ok := wf.Jobs[n]; ok {
			for _, step := range listJob.Steps {
				if strings.Contains(step.Run, "covercheck fuzz-targets") {
					listJobName = n
					break
				}
			}
		}
	}
	if listJobName == "" {
		t.Fatal("matrix job must depend on a list job that runs 'covercheck fuzz-targets'")
	}

	// Check matrix expression contains fromJSON(needs.<listJobName>.outputs.
	matrixBytes, err := yaml.Marshal(matrixJob.Strategy.Matrix)
	if err != nil {
		t.Fatalf("failed to marshal strategy.matrix: %v", err)
	}
	matrixStr := string(matrixBytes)
	expectedFromJSON := fmt.Sprintf("fromJSON(needs.%s.outputs.", listJobName)
	if !strings.Contains(matrixStr, expectedFromJSON) {
		t.Fatalf("matrix job strategy.matrix must reference targets via %q, got: %s", expectedFromJSON, matrixStr)
	}

	// 6. Must run -fuzztime 1h (or 60m / 3600s) and anchor -fuzz pattern
	fuzzStepFound := false
	for _, step := range matrixJob.Steps {
		if strings.Contains(step.Run, "-fuzz") && (strings.Contains(step.Run, "-fuzztime 1h") || strings.Contains(step.Run, "-fuzztime 60m") || strings.Contains(step.Run, "-fuzztime 3600s") || strings.Contains(step.Run, "-fuzztime=1h")) {
			if !reFuzzAnchored.MatchString(step.Run) {
				t.Fatalf("-fuzz pattern must be anchored to matrix target (e.g. ^${{ matrix.target }}$): %s", step.Run)
			}
			fuzzStepFound = true
			break
		}
	}
	if !fuzzStepFound {
		t.Fatal("matrix job must run go test with -fuzztime 1h and anchored -fuzz pattern")
	}

	// 7. Crasher upload, structurally: step in matrix job with upload-artifact, failure(), and testdata/fuzz
	crasherUploadFound := false
	for _, step := range matrixJob.Steps {
		if strings.Contains(step.Uses, "actions/upload-artifact") && strings.Contains(step.If, "failure()") {
			path := step.With["path"]
			if strings.Contains(path, "testdata/fuzz") {
				crasherUploadFound = true
				break
			}
		}
	}
	if !crasherUploadFound {
		t.Fatal("matrix job must contain a step using actions/upload-artifact, if: failure(), and path containing testdata/fuzz")
	}
}
