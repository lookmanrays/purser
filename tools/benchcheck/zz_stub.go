// Package main implements the benchcheck tool.
package main

import "io"

var unimplemented = func() { panic("unimplemented: P0-07b") }

// BenchmarkResult holds the parsed performance metrics for one benchmark sample.
type BenchmarkResult struct {
	Name        string  `json:"name"`
	NsPerOp     float64 `json:"ns_per_op"`
	BytesPerOp  int64   `json:"bytes_per_op,omitempty"`
	AllocsPerOp int64   `json:"allocs_per_op,omitempty"`
}

// Rule defines a ratio constraint between a target benchmark and a base benchmark.
type Rule struct {
	Target string  `json:"target"`
	Base   string  `json:"base"`
	Ratio  float64 `json:"ratio"`
}

// ParseBenchmarkOutput parses Go benchmark output (from go test -bench) and returns sample results by benchmark name.
func ParseBenchmarkOutput(_ string) (map[string][]BenchmarkResult, error) {
	unimplemented()
	return nil, nil
}

// ParseRule parses a single benchcheck ratio rule (e.g. from a comment line).
// If a comment starts with "benchcheck:" but fails to parse, it returns an error.
func ParseRule(_ string) (*Rule, error) {
	unimplemented()
	return nil, nil
}

// ParseRulesFromSource extracts benchcheck rules from comments in Go source code.
func ParseRulesFromSource(_ string) ([]Rule, error) {
	unimplemented()
	return nil, nil
}

// ParseRulesFromDir scans Go test files in dir for benchcheck rule comments.
func ParseRulesFromDir(_ string) ([]Rule, error) {
	unimplemented()
	return nil, nil
}

// Check verifies that benchmark output meets the given ratio rules using the median of samples.
// It returns a list of violations for any rule that is exceeded or missing benchmark.
func Check(_ string, _ []Rule) ([]string, error) {
	unimplemented()
	return nil, nil
}

// CheckFiles verifies benchmark output at benchPath against rules in Go source files at sourcePath.
func CheckFiles(_ string, _ string) ([]string, error) {
	unimplemented()
	return nil, nil
}

// Run executes the benchcheck CLI with args, writing output to stdout and stderr.
// It returns exit code 0 on success, 1 on failure.
func Run(_ []string, _ io.Writer, _ io.Writer) int {
	unimplemented()
	return 0
}

func main() {
	unimplemented()
}
