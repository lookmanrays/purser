// Package main implements the covercheck tool.
package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path"
	"sort"
	"strconv"
	"strings"

	"gopkg.in/yaml.v3"
)

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

func isKernel(pkg string) bool {
	return pkg == "internal/kernel" || strings.HasPrefix(pkg, "internal/kernel/") ||
		strings.HasSuffix(pkg, "/internal/kernel") || strings.Contains(pkg, "/internal/kernel/")
}

func baseFloor(pkg string, cfg *Config) float64 {
	if isKernel(pkg) {
		return cfg.KernelFloor
	}
	return cfg.DefaultFloor
}

// LoadConfig reads and parses the coverage configuration from path.
func LoadConfig(configPath string) (*Config, error) {
	data, err := os.ReadFile(configPath)
	if err != nil {
		return nil, fmt.Errorf("reading config %s: %w", configPath, err)
	}

	dec := yaml.NewDecoder(bytes.NewReader(data))
	dec.KnownFields(true)
	var cfg Config
	if err := dec.Decode(&cfg); err != nil {
		return nil, fmt.Errorf("parsing config %s: %w", configPath, err)
	}

	if cfg.KernelFloor < 0 || cfg.KernelFloor > 100 {
		return nil, fmt.Errorf("kernel floor %v out of range [0, 100]", cfg.KernelFloor)
	}
	if cfg.DefaultFloor < 0 || cfg.DefaultFloor > 100 {
		return nil, fmt.Errorf("default floor %v out of range [0, 100]", cfg.DefaultFloor)
	}

	for pkg, override := range cfg.Overrides {
		if override < 0 || override > 100 {
			return nil, fmt.Errorf("override for package %s (%v) out of range [0, 100]", pkg, override)
		}
		base := baseFloor(pkg, &cfg)
		if override < base {
			return nil, fmt.Errorf("override for package %s (%v) cannot be below floor (%v)", pkg, override, base)
		}
	}

	for _, exc := range cfg.Exclusions {
		if exc.Package == "" {
			return nil, errors.New("exclusion package cannot be empty")
		}
		if strings.TrimSpace(exc.Reason) == "" {
			return nil, fmt.Errorf("exclusion for package %s lacks non-empty reason", exc.Package)
		}
	}

	return &cfg, nil
}

type blockRecord struct {
	pkg     string
	numStmt int
	covered bool
}

// ParseCoverprofile parses a Go coverage profile from path and returns coverage per package.
func ParseCoverprofile(coverprofilePath string) (map[string]*PackageCoverage, error) {
	f, err := os.Open(coverprofilePath)
	if err != nil {
		return nil, fmt.Errorf("opening coverprofile %s: %w", coverprofilePath, err)
	}
	defer func() { _ = f.Close() }()

	scanner := bufio.NewScanner(f)
	if !scanner.Scan() {
		if err := scanner.Err(); err != nil {
			return nil, fmt.Errorf("reading coverprofile: %w", err)
		}
		return nil, errors.New("empty coverprofile: missing mode header")
	}

	header := strings.TrimSpace(scanner.Text())
	if !strings.HasPrefix(header, "mode: ") {
		return nil, fmt.Errorf("malformed mode header: %q", header)
	}
	mode := strings.TrimPrefix(header, "mode: ")
	if mode != "set" && mode != "count" && mode != "atomic" {
		return nil, fmt.Errorf("unknown coverage mode: %q", mode)
	}

	blocks := make(map[string]*blockRecord)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) != 3 {
			return nil, fmt.Errorf("malformed coverage line: %q", line)
		}

		colonIdx := strings.LastIndex(fields[0], ":")
		if colonIdx == -1 {
			return nil, fmt.Errorf("malformed coverage target: %q", fields[0])
		}
		filename := fields[0][:colonIdx]
		numStmt, err := strconv.Atoi(fields[1])
		if err != nil {
			return nil, fmt.Errorf("invalid statement count in %q: %w", line, err)
		}
		count, err := strconv.Atoi(fields[2])
		if err != nil {
			return nil, fmt.Errorf("invalid execution count in %q: %w", line, err)
		}

		isCovered := count > 0
		if existing, ok := blocks[fields[0]]; ok {
			existing.covered = existing.covered || isCovered
		} else {
			blocks[fields[0]] = &blockRecord{
				pkg:     path.Dir(filename),
				numStmt: numStmt,
				covered: isCovered,
			}
		}
	}

	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("scanning coverprofile: %w", err)
	}
	if len(blocks) == 0 {
		return nil, errors.New("coverprofile contains no blocks")
	}

	covMap := make(map[string]*PackageCoverage)
	for _, b := range blocks {
		cov, ok := covMap[b.pkg]
		if !ok {
			cov = &PackageCoverage{Package: b.pkg}
			covMap[b.pkg] = cov
		}
		cov.TotalStatements += b.numStmt
		if b.covered {
			cov.CoveredStatements += b.numStmt
		}
	}

	for _, cov := range covMap {
		if cov.TotalStatements > 0 {
			cov.Percentage = (float64(cov.CoveredStatements) / float64(cov.TotalStatements)) * 100.0
		}
	}

	return covMap, nil
}

// Check verifies that coverage in coverprofilePath meets floors in configPath.
func Check(coverprofilePath string, configPath string) ([]string, error) {
	cfg, err := LoadConfig(configPath)
	if err != nil {
		return nil, err
	}
	covMap, err := ParseCoverprofile(coverprofilePath)
	if err != nil {
		return nil, err
	}

	isExcluded := func(pkg string) bool {
		for _, exc := range cfg.Exclusions {
			if exc.Package == pkg {
				return true
			}
		}
		return false
	}

	pkgFloor := func(pkg string) float64 {
		if floor, ok := cfg.Overrides[pkg]; ok {
			return floor
		}
		return baseFloor(pkg, cfg)
	}

	var violations []string
	for pkg, cov := range covMap {
		if isExcluded(pkg) {
			continue
		}
		floor := pkgFloor(pkg)
		if cov.Percentage < floor {
			violations = append(violations, fmt.Sprintf("package %s coverage %.1f%% below floor %.1f%%", pkg, cov.Percentage, floor))
		}
	}

	sort.Strings(violations)
	return violations, nil
}

// FuzzTargets lists every fuzz target in the module directory at moduleDir.
func FuzzTargets(moduleDir string) ([]FuzzTarget, error) {
	cmd := exec.Command("go", "test", "-json", "-list", "^Fuzz", "./...")
	cmd.Dir = moduleDir
	out, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("listing fuzz targets in %s: %w", moduleDir, err)
	}

	targets := make([]FuzzTarget, 0)
	seen := make(map[string]bool)
	scanner := bufio.NewScanner(bytes.NewReader(out))
	for scanner.Scan() {
		var ev struct {
			Action  string `json:"Action"`
			Package string `json:"Package"`
			Output  string `json:"Output"`
		}
		if err := json.Unmarshal(scanner.Bytes(), &ev); err != nil {
			continue
		}
		if ev.Action == "output" {
			name := strings.TrimSpace(ev.Output)
			if strings.HasPrefix(name, "Fuzz") && !strings.ContainsAny(name, " \t\n\r") {
				key := ev.Package + "/" + name
				if !seen[key] {
					seen[key] = true
					targets = append(targets, FuzzTarget{Package: ev.Package, Name: name})
				}
			}
		}
	}
	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("parsing go test output: %w", err)
	}
	return targets, nil
}

// Run executes the covercheck CLI with args, writing output to stdout and stderr.
func Run(args []string, stdout io.Writer, stderr io.Writer) int {
	if len(args) > 0 && args[0] == "fuzz-targets" {
		fs := flag.NewFlagSet("fuzz-targets", flag.ContinueOnError)
		fs.SetOutput(stderr)
		runTargets := fs.Bool("run", false, "run discovered fuzz targets")
		fuzzTime := fs.String("fuzztime", "30s", "fuzz time per target")
		if err := fs.Parse(args[1:]); err != nil {
			return 1
		}
		moduleDir := "."
		if fs.NArg() > 0 {
			moduleDir = fs.Arg(0)
		}
		targets, err := FuzzTargets(moduleDir)
		if err != nil {
			_, _ = fmt.Fprintln(stderr, err)
			return 1
		}
		if *runTargets {
			for _, t := range targets {
				_, _ = fmt.Fprintf(stdout, "fuzzing %s/%s for %s\n", t.Package, t.Name, *fuzzTime)
				cmd := exec.Command("go", "test", "-run", "^$", "-fuzz", fmt.Sprintf("^%s$", t.Name), "-fuzztime", *fuzzTime, t.Package)
				cmd.Stdout = stdout
				cmd.Stderr = stderr
				if err := cmd.Run(); err != nil {
					_, _ = fmt.Fprintf(stderr, "fuzz target %s/%s failed: %v\n", t.Package, t.Name, err)
					return 1
				}
			}
			return 0
		}
		enc := json.NewEncoder(stdout)
		if err := enc.Encode(targets); err != nil {
			_, _ = fmt.Fprintln(stderr, err)
			return 1
		}
		return 0
	}

	fs := flag.NewFlagSet("covercheck", flag.ContinueOnError)
	fs.SetOutput(stderr)
	coverprofile := fs.String("coverprofile", "", "path to coverprofile")
	config := fs.String("config", ".coverage.yaml", "path to coverage config")
	if err := fs.Parse(args); err != nil {
		return 1
	}
	if *coverprofile == "" {
		_, _ = fmt.Fprintln(stderr, "missing required --coverprofile flag")
		return 1
	}

	violations, err := Check(*coverprofile, *config)
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
