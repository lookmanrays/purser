// Package main implements the briefcheck tool.
package main

import (
	"io"
)

var unimplemented = func() { panic("unimplemented: P0-02") }

// Run executes the briefcheck CLI with args, writing output to stdout and stderr.
// It returns an exit code: 0 on success, 1 on violations or fatal error.
func Run(_ []string, _ io.Writer, _ io.Writer) int { unimplemented(); return 0 }

// Validate checks every docs/packages/P*.md in repoRoot and all contracts in contracts/*.yaml.
// It returns the list of violations found.
func Validate(_ string) ([]string, error) { unimplemented(); return nil, nil }

// ValidateBrief checks an individual package brief at path against allIDs.
// It returns the list of violations found for this brief.
func ValidateBrief(_ string, _ []string) ([]string, error) { unimplemented(); return nil, nil }

// Scope checks changes on branch against its base (frozen/<id> for build, merge-base with main for tests).
// It verifies that only permitted paths were touched.
// On tests/<id>: only tests_frozen, zz_stub.go in scope.write package dirs, and docs/packages/<id>.*.
// On build/<id>: only scope.write and docs/packages/<id>.* (excluding tests_frozen).
// Renamed files are checked under both old and new paths.
func Scope(_ string, _ string, _ string) ([]string, error) { unimplemented(); return nil, nil }

// Frozen checks that on a build branch, the package's tests_frozen files are byte-identical
// to the tag frozen/<id>, and no other package's frozen files differ from main.
func Frozen(_ string, _ string) ([]string, error) { unimplemented(); return nil, nil }

// Contracts checks that changes under contracts/ relative to main are allowed only if the brief
// sets contract_change: true.
func Contracts(_ string, _ string, _ string) ([]string, error) { unimplemented(); return nil, nil }

// Red lists the TestACn_ functions in the package's frozen tests, verifies that every AC
// has at least one corresponding TestACn_ test, runs them, and passes only if every one fails.
func Red(_ string, _ string) error { unimplemented(); return nil }

// CheckBriefcheckWorkflow checks that the workflow file at workflowPath runs make briefcheck
// on every pull request whose branch matches build/* or tests/*.
func CheckBriefcheckWorkflow(_ string) ([]string, error) { unimplemented(); return nil, nil }

// CheckFreezeWorkflow checks that the freeze workflow at workflowPath tags frozen/<id> for
// size S tests branches only after vet, briefcheck and red pass, and never tags for size M or L.
func CheckFreezeWorkflow(_ string) ([]string, error) { unimplemented(); return nil, nil }

func main() {
	unimplemented()
}
