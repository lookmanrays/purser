// Package main implements the benchcheck tool.
package main

import (
	"flag"
	"fmt"
	"go/parser"
	"go/token"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"golang.org/x/perf/benchfmt"
)

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

var reRule = regexp.MustCompile(`^(\S+)\s*/\s*(\S+)\s*<=\s*([0-9]+(?:\.[0-9]+)?)$`)

func stripGomaxprocs(name string) string {
	idx := strings.LastIndex(name, "-")
	if idx != -1 && idx < len(name)-1 {
		allDigits := true
		for _, c := range name[idx+1:] {
			if c < '0' || c > '9' {
				allDigits = false
				break
			}
		}
		if allDigits {
			return name[:idx]
		}
	}
	return name
}

// ParseBenchmarkOutput parses Go benchmark output (from go test -bench) and returns sample results by benchmark name.
func ParseBenchmarkOutput(content string) (map[string][]BenchmarkResult, error) {
	r := benchfmt.NewReader(strings.NewReader(content), "<input>")
	results := make(map[string][]BenchmarkResult)

	for r.Scan() {
		rec := r.Result()
		res, ok := rec.(*benchfmt.Result)
		if !ok {
			continue
		}

		name := string(res.Name)
		if !strings.HasPrefix(name, "Benchmark") {
			name = "Benchmark" + name
		}
		name = stripGomaxprocs(name)

		var nsPerOp float64
		var bytesPerOp, allocsPerOp int64

		for _, val := range res.Values {
			switch val.Unit {
			case "sec/op":
				if val.OrigUnit == "ns/op" {
					nsPerOp = val.OrigValue
				} else {
					nsPerOp = val.Value * 1e9
				}
			case "ns/op":
				nsPerOp = val.Value
			case "B/op":
				bytesPerOp = int64(val.Value)
			case "allocs/op":
				allocsPerOp = int64(val.Value)
			}
		}

		results[name] = append(results[name], BenchmarkResult{
			Name:        name,
			NsPerOp:     nsPerOp,
			BytesPerOp:  bytesPerOp,
			AllocsPerOp: allocsPerOp,
		})
	}

	if err := r.Err(); err != nil {
		return nil, fmt.Errorf("parsing benchmark output: %w", err)
	}
	return results, nil
}

// ParseRule parses a single benchcheck ratio rule (e.g. from a comment line).
// If a comment starts with "benchcheck:" but fails to parse, it returns an error.
func ParseRule(comment string) (*Rule, error) {
	trimmed := strings.TrimSpace(comment)
	trimmed = strings.TrimPrefix(trimmed, "//")
	trimmed = strings.TrimSpace(trimmed)

	if !strings.HasPrefix(trimmed, "benchcheck:") {
		return nil, nil
	}

	expr := strings.TrimSpace(strings.TrimPrefix(trimmed, "benchcheck:"))
	matches := reRule.FindStringSubmatch(expr)
	if matches == nil {
		return nil, fmt.Errorf("malformed benchcheck rule comment: %q", comment)
	}

	ratio, err := strconv.ParseFloat(matches[3], 64)
	if err != nil || ratio <= 0 {
		return nil, fmt.Errorf("invalid benchcheck ratio in %q: %w", comment, err)
	}

	return &Rule{
		Target: matches[1],
		Base:   matches[2],
		Ratio:  ratio,
	}, nil
}

// ParseRulesFromSource extracts benchcheck rules from comments in Go source code.
func ParseRulesFromSource(source string) ([]Rule, error) {
	fset := token.NewFileSet()
	node, err := parser.ParseFile(fset, "", source, parser.ParseComments)
	if err != nil {
		node, err = parser.ParseFile(fset, "", "package p\n"+source, parser.ParseComments)
		if err != nil {
			return nil, fmt.Errorf("parsing source: %w", err)
		}
	}

	var rules []Rule
	for _, cg := range node.Comments {
		for _, c := range cg.List {
			comment := strings.TrimSpace(c.Text)
			comment = strings.TrimPrefix(comment, "//")
			comment = strings.TrimSpace(comment)
			if strings.HasPrefix(comment, "benchcheck:") {
				rule, err := ParseRule(comment)
				if err != nil {
					return nil, err
				}
				if rule != nil {
					rules = append(rules, *rule)
				}
			}
		}
	}
	return rules, nil
}

// ParseRulesFromDir scans Go test files in dir for benchcheck rule comments.
func ParseRulesFromDir(dir string) ([]Rule, error) {
	var rules []Rule
	err := filepath.Walk(dir, func(p string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() {
			if (strings.HasPrefix(info.Name(), ".") && info.Name() != ".") || (info.Name() == "testdata" && p != dir) {
				return filepath.SkipDir
			}
			return nil
		}
		if strings.HasSuffix(info.Name(), "_test.go") {
			data, readErr := os.ReadFile(p)
			if readErr != nil {
				return readErr
			}
			fileRules, parseErr := ParseRulesFromSource(string(data))
			if parseErr != nil {
				return parseErr
			}
			rules = append(rules, fileRules...)
		}
		return nil
	})
	if err != nil {
		return nil, fmt.Errorf("reading directory %s: %w", dir, err)
	}

	return rules, nil
}

func median(samples []float64) float64 {
	if len(samples) == 0 {
		return 0
	}
	s := make([]float64, len(samples))
	copy(s, samples)
	sort.Float64s(s)
	n := len(s)
	if n%2 == 1 {
		return s[n/2]
	}
	return (s[n/2-1] + s[n/2]) / 2.0
}

// Check verifies that benchmark output meets the given ratio rules using the median of samples.
// It returns a list of violations for any rule that is exceeded or missing benchmark.
func Check(benchContent string, rules []Rule) ([]string, error) {
	results, err := ParseBenchmarkOutput(benchContent)
	if err != nil {
		return nil, err
	}

	var violations []string
	for _, rule := range rules {
		targetSamples, ok := results[rule.Target]
		if !ok || len(targetSamples) == 0 {
			violations = append(violations, fmt.Sprintf("missing target benchmark %s", rule.Target))
			continue
		}
		baseSamples, ok := results[rule.Base]
		if !ok || len(baseSamples) == 0 {
			violations = append(violations, fmt.Sprintf("missing base benchmark %s", rule.Base))
			continue
		}

		targetVals := make([]float64, len(targetSamples))
		for i, s := range targetSamples {
			targetVals[i] = s.NsPerOp
		}
		baseVals := make([]float64, len(baseSamples))
		for i, s := range baseSamples {
			baseVals[i] = s.NsPerOp
		}

		targetMed := median(targetVals)
		baseMed := median(baseVals)
		if baseMed <= 0 {
			violations = append(violations, fmt.Sprintf("base benchmark %s median non-positive: %v", rule.Base, baseMed))
			continue
		}

		ratio := targetMed / baseMed
		if ratio > rule.Ratio {
			violations = append(violations, fmt.Sprintf("benchmark ratio exceeded for %s / %s: %.2f > %.2f", rule.Target, rule.Base, ratio, rule.Ratio))
		}
	}

	return violations, nil
}

// CheckFiles verifies benchmark output at benchPath against rules in Go source files at sourcePath.
func CheckFiles(benchPath string, sourcePath string) ([]string, error) {
	var benchData []byte
	var err error
	if benchPath == "-" {
		benchData, err = io.ReadAll(os.Stdin)
	} else {
		benchData, err = os.ReadFile(benchPath)
	}
	if err != nil {
		return nil, fmt.Errorf("reading benchmark input: %w", err)
	}

	st, err := os.Stat(sourcePath)
	if err != nil {
		return nil, fmt.Errorf("stat source path %s: %w", sourcePath, err)
	}

	var rules []Rule
	if st.IsDir() {
		rules, err = ParseRulesFromDir(sourcePath)
	} else {
		var srcData []byte
		srcData, err = os.ReadFile(sourcePath)
		if err != nil {
			return nil, fmt.Errorf("reading source file %s: %w", sourcePath, err)
		}
		rules, err = ParseRulesFromSource(string(srcData))
	}
	if err != nil {
		return nil, err
	}

	return Check(string(benchData), rules)
}

// Run executes the benchcheck CLI with args, writing output to stdout and stderr.
func Run(args []string, stdout io.Writer, stderr io.Writer) int {
	fs := flag.NewFlagSet("benchcheck", flag.ContinueOnError)
	fs.SetOutput(stderr)
	bench := fs.String("bench", "-", "path to benchmark output (or - for stdin)")
	source := fs.String("source", ".", "path to source file or directory containing rules")
	if err := fs.Parse(args); err != nil {
		return 1
	}

	violations, err := CheckFiles(*bench, *source)
	if err != nil {
		_, _ = fmt.Fprintln(stderr, err)
		return 1
	}
	if len(violations) > 0 {
		for _, v := range violations {
			_, _ = fmt.Fprintln(stdout, v)
		}
		return 1
	}
	return 0
}

func main() {
	os.Exit(Run(os.Args[1:], os.Stdout, os.Stderr))
}
