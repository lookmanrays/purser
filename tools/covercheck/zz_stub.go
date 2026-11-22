// Package main implements the covercheck tool.
package main

import "io"

var unimplemented = func() { panic("unimplemented: P0-07") }

// Exclusion defines a package excluded from coverage floors with an explicit reason.
type Exclusion struct {
	Package string `yaml:"package" json:"package"`
	Reason  string `yaml:"reason" json:"reason"`
}

// Config holds coverage floor configuration, per-package overrides, and exclusions.
type Config struct {
	KernelFloor  float64            `yaml:"kernel_floor" json:"kernel_floor"`
	DefaultFloor float64            `yaml:"default_floor" json:"default_floor"`
	Overrides    map[string]float64 `yaml:"overrides" json:"overrides"`
	Exclusions   []Exclusion        `yaml:"exclusions" json:"exclusions"`
}

// PackageCoverage holds statement statistics and coverage percentage for one package.
type PackageCoverage struct {
	Package           string  `json:"package"`
	TotalStatements   int     `json:"total_statements"`
	CoveredStatements int     `json:"covered_statements"`
	Percentage        float64 `json:"percentage"`
}

// FuzzTarget identifies one fuzz target and the package containing it.
type FuzzTarget struct {
	Package string `json:"package"`
	Name    string `json:"name"`
}

// LoadConfig reads and parses the coverage configuration from path.
// It returns an error if the file cannot be read or parsed, if unknown keys are present,
// if any floor is outside 0–100, if an exclusion lacks a reason, or if any override lowers a floor below the default.
func LoadConfig(_ string) (*Config, error) {
	unimplemented()
	return nil, nil
}

// ParseCoverprofile parses a Go coverage profile from path and returns coverage per package.
// It handles set, count, and atomic modes, merging duplicate blocks as covered if any copy has count > 0.
func ParseCoverprofile(_ string) (map[string]*PackageCoverage, error) {
	unimplemented()
	return nil, nil
}

// Check verifies that coverage in coverprofilePath meets floors in configPath.
// Excluded packages are never reported.
// It returns a list of violations (each naming the failing package).
// If an override is below the default, a profile is malformed/empty, or a file cannot be read, it returns an error.
func Check(_ string, _ string) ([]string, error) {
	unimplemented()
	return nil, nil
}

// FuzzTargets lists every fuzz target in the module directory at moduleDir.
func FuzzTargets(_ string) ([]FuzzTarget, error) {
	unimplemented()
	return nil, nil
}

// Run executes the covercheck CLI with args, writing output to stdout and stderr.
// It supports cover checking and the "fuzz-targets" subcommand.
// It returns exit code 0 on success, 1 on failure.
func Run(_ []string, _ io.Writer, _ io.Writer) int {
	unimplemented()
	return 0
}

func main() {
	unimplemented()
}
