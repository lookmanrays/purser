package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestAC3_BenchcheckPassesWhenRatioHolds(t *testing.T) {
	defer guard(t)

	benchData, err := os.ReadFile(testdataPath(t, "bench_median_pass.txt"))
	if err != nil {
		t.Fatalf("failed reading bench_median_pass.txt: %v", err)
	}

	rule := Rule{
		Target: "BenchmarkSnapshotRead/events=100000",
		Base:   "BenchmarkSnapshotRead/events=1000",
		Ratio:  2.0,
	}

	violations, err := Check(string(benchData), []Rule{rule})
	if err != nil {
		t.Fatalf("Check failed unexpectedly: %v", err)
	}
	if len(violations) != 0 {
		t.Fatalf("expected 0 violations when median ratio holds (1.9 <= 2.0), got: %v", violations)
	}
}

func TestAC3_BenchcheckFailsWhenRatioExceeded(t *testing.T) {
	defer guard(t)

	benchData, err := os.ReadFile(testdataPath(t, "bench_median_fail.txt"))
	if err != nil {
		t.Fatalf("failed reading bench_median_fail.txt: %v", err)
	}

	rule := Rule{
		Target: "BenchmarkSnapshotRead/events=100000",
		Base:   "BenchmarkSnapshotRead/events=1000",
		Ratio:  2.0,
	}

	violations, err := Check(string(benchData), []Rule{rule})
	if err != nil {
		t.Fatalf("Check returned unexpected error: %v", err)
	}
	if len(violations) == 0 {
		t.Fatal("expected violation when median ratio is exceeded (2.5 > 2.0), got none")
	}

	found := false
	for _, v := range violations {
		if strings.Contains(v, "BenchmarkSnapshotRead/events=100000") && strings.Contains(v, "BenchmarkSnapshotRead/events=1000") {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("expected violation naming target and base benchmarks, got: %v", violations)
	}
}

func TestAC3_BenchcheckExactLimit(t *testing.T) {
	defer guard(t)

	benchData, err := os.ReadFile(testdataPath(t, "bench_exact_limit.txt"))
	if err != nil {
		t.Fatalf("failed reading bench_exact_limit.txt: %v", err)
	}

	rule := Rule{
		Target: "BenchmarkSnapshotRead/events=100000",
		Base:   "BenchmarkSnapshotRead/events=1000",
		Ratio:  2.0,
	}

	violations, err := Check(string(benchData), []Rule{rule})
	if err != nil {
		t.Fatalf("Check returned unexpected error: %v", err)
	}
	if len(violations) != 0 {
		t.Fatalf("expected 0 violations when ratio is exactly at limit (2.0 <= 2.0), got: %v", violations)
	}
}

func TestAC3_BenchcheckMissingBenchmark(t *testing.T) {
	defer guard(t)

	benchData, err := os.ReadFile(testdataPath(t, "bench_missing.txt"))
	if err != nil {
		t.Fatalf("failed reading bench_missing.txt: %v", err)
	}

	rule := Rule{
		Target: "BenchmarkSnapshotRead/events=100000",
		Base:   "BenchmarkSnapshotRead/events=1000",
		Ratio:  2.0,
	}

	violations, err := Check(string(benchData), []Rule{rule})
	if err != nil {
		if !strings.Contains(err.Error(), "BenchmarkSnapshotRead/events=100000") {
			t.Fatalf("expected error naming missing benchmark BenchmarkSnapshotRead/events=100000, got: %v", err)
		}
		return
	}
	if len(violations) == 0 {
		t.Fatal("expected violation when required benchmark is missing, got none")
	}
	found := false
	for _, v := range violations {
		if strings.Contains(v, "BenchmarkSnapshotRead/events=100000") {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("expected violation mentioning missing benchmark BenchmarkSnapshotRead/events=100000, got: %v", violations)
	}
}

func TestAC3_BenchcheckParseRuleFromComment(t *testing.T) {
	defer guard(t)

	comment := "// benchcheck: BenchmarkSnapshotRead/events=100000 / BenchmarkSnapshotRead/events=1000 <= 2.0"
	rule, err := ParseRule(comment)
	if err != nil {
		t.Fatalf("ParseRule failed: %v", err)
	}
	if rule.Target != "BenchmarkSnapshotRead/events=100000" {
		t.Fatalf("expected Target BenchmarkSnapshotRead/events=100000, got %q", rule.Target)
	}
	if rule.Base != "BenchmarkSnapshotRead/events=1000" {
		t.Fatalf("expected Base BenchmarkSnapshotRead/events=1000, got %q", rule.Base)
	}
	if rule.Ratio != 2.0 {
		t.Fatalf("expected Ratio 2.0, got %v", rule.Ratio)
	}

	malformedComment := "// benchcheck: not a valid ratio rule expression"
	_, malformedErr := ParseRule(malformedComment)
	if malformedErr == nil {
		t.Fatal("expected error on malformed benchcheck rule comment, got nil (must not be silently skipped)")
	}

	source := `
package testdata

// benchcheck: BenchmarkSnapshotRead/events=100000 / BenchmarkSnapshotRead/events=1000 <= 2.0
func BenchmarkSnapshotRead_100k(_ *testing.B) {}
`
	rules, err := ParseRulesFromSource(source)
	if err != nil {
		t.Fatalf("ParseRulesFromSource failed: %v", err)
	}
	if len(rules) != 1 {
		t.Fatalf("expected 1 rule, got %d", len(rules))
	}
	if rules[0].Target != "BenchmarkSnapshotRead/events=100000" || rules[0].Ratio != 2.0 {
		t.Fatalf("unexpected rule parsed: %+v", rules[0])
	}
}

func TestAC3_BenchcheckParseRulesFromDir(t *testing.T) {
	defer guard(t)

	rulesDir := testdataPath(t, "rules")
	rules, err := ParseRulesFromDir(rulesDir)
	if err != nil {
		t.Fatalf("ParseRulesFromDir failed: %v", err)
	}
	if len(rules) != 1 {
		t.Fatalf("expected exactly 1 rule parsed from rules directory, got %d: %+v", len(rules), rules)
	}
	if rules[0].Target != "BenchmarkSnapshotRead/events=100000" || rules[0].Base != "BenchmarkSnapshotRead/events=1000" {
		t.Fatalf("unexpected rule: %+v", rules[0])
	}
}

func TestAC3_BenchcheckCLI(t *testing.T) {
	defer guard(t)

	passBench := testdataPath(t, "bench_median_pass.txt")
	failBench := testdataPath(t, "bench_median_fail.txt")
	rulesDir := testdataPath(t, "rules")
	rulesFile := filepath.Join(rulesDir, "with_rule_test.go")

	// Test CLI with a single source file
	var stdout, stderr bytes.Buffer
	code := Run([]string{"--bench", passBench, "--source", rulesFile}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("expected exit code 0 when ratio holds with file source, got %d (output: %s)", code, stdout.String()+stderr.String())
	}

	// Test CLI with a directory containing rules
	stdout.Reset()
	stderr.Reset()
	code = Run([]string{"--bench", passBench, "--source", rulesDir}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("expected exit code 0 when ratio holds with directory source, got %d (output: %s)", code, stdout.String()+stderr.String())
	}

	stdout.Reset()
	stderr.Reset()
	code = Run([]string{"--bench", failBench, "--source", rulesDir}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("expected exit code 1 when ratio is exceeded, got %d", code)
	}
	output := stdout.String() + stderr.String()
	if !strings.Contains(output, "BenchmarkSnapshotRead/events=100000") {
		t.Fatalf("expected output to mention failing benchmark BenchmarkSnapshotRead/events=100000, got: %s", output)
	}
}
