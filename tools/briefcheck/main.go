// Package main implements the briefcheck tool.
package main

import (
	"encoding/json"
	"fmt"
	"io"
	"maps"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"slices"
	"sort"
	"strconv"
	"strings"

	"github.com/bmatcuk/doublestar/v4"
	"gopkg.in/yaml.v3"
)

var (
	reAC         = regexp.MustCompile(`(?m)^- AC-(\d+) `)
	reErrCode    = regexp.MustCompile(`^[a-z][a-z0-9_]*$`)
	reACBaseline = regexp.MustCompile(`(?m)^- AC-(\d+) \(baseline\) `)
	reTestName   = regexp.MustCompile(`func\s+(TestAC(\d+)_\w*)\s*\(`)
)

// Brief holds parsed brief fields.
type Brief struct {
	ID, Title, Phase, Status, Size, Builder, Reviewer, Merge string
	TestAuthor                                               string   `yaml:"test_author"`
	ContractChange                                           bool     `yaml:"contract_change"`
	DependsOn                                                []string `yaml:"depends_on"`
	Contract                                                 any
	Scope                                                    struct{ Write, Read []string }
	TestsFrozen                                              []string `yaml:"tests_frozen"`
	Reuse                                                    []string
	DepsAllowed                                              []string `yaml:"deps_allowed"`
	Budget                                                   struct {
		ChangedLines int `yaml:"changed_lines"`
	}
	Body, Path string `yaml:"-"`
}

func parseBrief(path string) (*Brief, map[string]any, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, nil, err
	}
	parts := strings.SplitN(string(data), "---\n", 3)
	if len(parts) < 3 || parts[0] != "" {
		return nil, nil, fmt.Errorf("missing front matter")
	}
	var raw map[string]any
	if err := yaml.Unmarshal([]byte(parts[1]), &raw); err != nil {
		return nil, nil, fmt.Errorf("YAML error: %w", err)
	}
	b := &Brief{Body: parts[2], Path: path}
	return b, raw, yaml.Unmarshal([]byte(parts[1]), b)
}

// ValidateBrief checks an individual package brief at path against allIDs.
func ValidateBrief(path string, allIDs []string) ([]string, error) {
	pid := strings.TrimSuffix(filepath.Base(path), ".md")
	b, present, err := parseBrief(path)
	if err != nil {
		return []string{fmt.Sprintf("%s.md: %v", pid, err)}, nil
	}
	var v []string
	chk := func(ok bool, format string, args ...any) {
		if !ok {
			v = append(v, fmt.Sprintf("%s.md: "+format, append([]any{pid}, args...)...))
		}
	}
	for _, k := range strings.Fields("id title phase status size builder test_author reviewer merge contract_change depends_on contract scope tests_frozen reuse deps_allowed budget") {
		chk(present[k] != nil, "missing key %s", k)
	}
	if len(v) > 0 {
		return v, nil
	}
	chk(b.ID == pid, "id %s does not match file name", b.ID)
	chk(len(b.Phase) == 2 && b.Phase[0] == 'P' && strings.HasPrefix(pid, b.Phase+"-"), "phase %s does not match id", b.Phase)
	chk(b.Status == "draft" || b.Status == "ready" || b.Status == "done", "bad status %s", b.Status)
	limits := map[string]int{"S": 200, "M": 600, "L": 600}
	if lim, ok := limits[b.Size]; !ok {
		chk(false, "bad size %s", b.Size)
	} else {
		chk(b.Size != "L" || b.Status == "draft", "size L is allowed only in draft; split before ready")
		chk(b.Budget.ChangedLines <= lim, "budget %d exceeds %d for size %s", b.Budget.ChangedLines, lim, b.Size)
	}
	chk(strings.Contains(" codex antigravity purser you claude ", " "+b.Builder+" "), "bad builder %s", b.Builder)
	chk(strings.Contains(" codex antigravity antigravity-2 claude purser none ", " "+b.TestAuthor+" "), "bad test_author %s", b.TestAuthor)
	chk(b.Builder != b.TestAuthor || b.Builder == "purser", "builder and test_author are the same agent")
	chk(b.Reviewer == b.TestAuthor || b.Reviewer == "you", "reviewer %s must be the test author or you", b.Reviewer)
	chk(b.Merge == "auto" || b.Merge == "human", "bad merge %s", b.Merge)
	chk(b.Merge != "auto" || (b.Size == "S" && !b.ContractChange && b.TestAuthor != "none"), "merge auto requires size S, no contract change and an independent test author")
	for _, p := range b.Scope.Write {
		if strings.HasPrefix(p, "contracts/") && !b.ContractChange {
			chk(false, "writes under contracts/ but contract_change is false")
			break
		}
	}
	chk(b.TestAuthor != "none" || len(b.TestsFrozen) == 0, "tests_frozen listed but no test author")
	chk(b.TestAuthor == "none" || len(b.TestsFrozen) > 0 || b.Status != "ready", "ready package with a test author has no tests_frozen")
	for _, dep := range b.DependsOn {
		if !slices.Contains(allIDs, dep) {
			chk(false, "unknown dependency %s", dep)
		} else if strings.Split(dep, "-")[0] > b.Phase {
			chk(false, "depends on later phase %s", dep)
		}
	}
	for _, s := range strings.Split("Goal,Context,Design,Out of scope,Test plan,Acceptance criteria,Definition of done,Escalate if", ",") {
		chk(strings.Contains(b.Body, "## "+s), "missing section '## %s'", s)
	}
	acs := reAC.FindAllStringSubmatch(b.Body, -1)
	chk(len(acs) > 0, "no acceptance criteria")
	for i, m := range acs {
		if n, _ := strconv.Atoi(m[1]); n != i+1 {
			chk(false, "AC numbering is not 1..n")
			break
		}
	}
	for _, r := range b.Reuse {
		chk(!strings.HasPrefix(r, "internal/") || len(b.DependsOn) > 0, "reuses %s, which no ancestor package writes", r)
	}
	return v, nil
}

// Validate checks every docs/packages/P*.md in repoRoot and all contracts in contracts/*.yaml.
func Validate(repoRoot string) ([]string, error) {
	pkgDir := filepath.Join(repoRoot, "docs", "packages")
	entries, err := os.ReadDir(pkgDir)
	if err != nil {
		return nil, err
	}
	var allIDs, violations []string
	briefs := make(map[string]*Brief)
	for _, e := range entries {
		n := e.Name()
		if strings.HasPrefix(n, "P") && strings.HasSuffix(n, ".md") && !strings.HasSuffix(n, ".contradiction.md") {
			id := strings.TrimSuffix(n, ".md")
			allIDs = append(allIDs, id)
			briefs[id], _, _ = parseBrief(filepath.Join(pkgDir, n))
		}
	}
	for _, id := range allIDs {
		v, err := ValidateBrief(filepath.Join(pkgDir, id+".md"), allIDs)
		if err != nil {
			return nil, err
		}
		violations = append(violations, v...)
	}
	state := make(map[string]int)
	var visit func(n string, stack []string)
	visit = func(n string, stack []string) {
		state[n] = 1
		if b := briefs[n]; b != nil {
			for _, d := range b.DependsOn {
				if briefs[d] != nil && state[d] == 1 {
					violations = append(violations, n+".md: dependency cycle: "+strings.Join(append(stack, d), " -> "))
				} else if briefs[d] != nil && state[d] == 0 {
					visit(d, append(stack, d))
				}
			}
		}
		state[n] = 2
	}
	for id := range briefs {
		if state[id] == 0 {
			visit(id, []string{id})
		}
	}
	for id, b := range briefs {
		anc := make(map[string]bool)
		var getAnc func(string)
		getAnc = func(curr string) {
			if br := briefs[curr]; br != nil {
				for _, d := range br.DependsOn {
					if !anc[d] {
						anc[d] = true
						getAnc(d)
					}
				}
			}
		}
		getAnc(id)
		for _, r := range b.Reuse {
			if !strings.HasPrefix(r, "internal/") {
				continue
			}
			ok := false
			for a := range anc {
				if ab := briefs[a]; ab != nil {
					for _, w := range ab.Scope.Write {
						if w == r || strings.HasPrefix(w, r+"/") || strings.HasPrefix(w, r+"*") {
							ok = true
							break
						}
					}
				}
			}
			if !ok {
				violations = append(violations, id+".md: reuses "+r+", which no ancestor package writes")
			}
		}
	}
	_ = filepath.Walk(filepath.Join(repoRoot, "contracts"), func(path string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() || !strings.HasSuffix(path, ".yaml") {
			return nil
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return nil
		}
		rel, _ := filepath.Rel(repoRoot, path)
		var doc map[string]any
		if err := yaml.Unmarshal(data, &doc); err != nil {
			violations = append(violations, fmt.Sprintf("%s: YAML error: %v", rel, err))
			return nil
		}
		if doc == nil || doc["contract"] == nil {
			return nil
		}
		chk := func(ok bool, format string, args ...any) {
			if !ok {
				violations = append(violations, fmt.Sprintf("%s: "+format, append([]any{rel}, args...)...))
			}
		}
		for _, k := range strings.Fields("contract kind version status owner summary") {
			chk(doc[k] != nil, "missing key %s", k)
		}
		kind, _ := doc["kind"].(string)
		chk(kind == "" || strings.Contains(" module function data endpoint event ui cli ", " "+kind+" "), "bad kind %s", kind)
		owner, _ := doc["owner"].(string)
		chk(owner == "" || slices.Contains(allIDs, owner), "owner %s is not a brief", owner)
		reqMap := map[string]string{"module": "package interface layer allowed_imports", "function": "package interface"}
		for _, k := range strings.Fields(reqMap[kind]) {
			chk(doc[k] != nil, "%s contract missing %s", kind, k)
		}
		if fns, ok := doc["functions"].([]any); ok {
			for _, fn := range fns {
				if fnMap, ok := fn.(map[string]any); ok && fnMap["examples"] == nil {
					chk(false, "function %s has no examples or vectors", fnMap["name"])
				}
			}
		}
		if errs, ok := doc["errors"].(map[string]any); ok {
			for code := range errs {
				chk(reErrCode.MatchString(code), "error code %s is not snake case", code)
			}
		}
		return nil
	})
	sort.Strings(violations)
	return violations, nil
}

func runGitInDir(dir string, args ...string) (string, error) {
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	out, err := cmd.CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("git %s: %w: %s", strings.Join(args, " "), err, strings.TrimSpace(string(out)))
	}
	return strings.TrimSpace(string(out)), nil
}

func mainRef(dir string) string {
	if _, err := runGitInDir(dir, "rev-parse", "--verify", "origin/main"); err == nil {
		return "origin/main"
	}
	return "main"
}

func matchesAny(path string, patterns []string) bool {
	for _, p := range patterns {
		if m, _ := doublestar.Match(p, path); m {
			return true
		}
	}
	return false
}

func findBrief(root, id string) (*Brief, error) {
	for _, s := range []string{"docs/packages", "tools/briefcheck/testdata/fixtures/repo/docs/packages", "tools/briefcheck/testdata/briefs"} {
		if b, _, err := parseBrief(filepath.Join(root, s, id+".md")); err == nil {
			return b, nil
		}
	}
	return nil, fmt.Errorf("brief %s not found", id)
}

func diffPaths(root, base string) (res []string) {
	out, _ := runGitInDir(root, "diff", "--name-status", "-M", base, "HEAD")
	for _, l := range strings.Split(out, "\n") {
		if p := strings.Split(strings.TrimSpace(l), "\t"); len(p) >= 2 {
			res = append(res, p[1:]...)
		}
	}
	return res
}

// Scope checks changes on branch against its base.
func Scope(repoRoot, id, branch string) (violations []string, err error) {
	b, err := findBrief(repoRoot, id)
	if err != nil {
		return nil, err
	}
	base := "frozen/" + id
	if !strings.HasPrefix(branch, "build/") {
		if base, _ = runGitInDir(repoRoot, "merge-base", mainRef(repoRoot), "HEAD"); base == "" {
			base = mainRef(repoRoot)
		}
	}
	out, err := runGitInDir(repoRoot, "diff", "--name-status", "-M", base, "HEAD")
	if err != nil {
		return nil, err
	}
	mainChanged := diffPaths(repoRoot, mainRef(repoRoot))
	isBuild := strings.HasPrefix(branch, "build/")
	for _, line := range strings.Split(out, "\n") {
		parts := strings.Split(strings.TrimSpace(line), "\t")
		if len(parts) < 2 {
			continue
		}
		for _, p := range parts[1:] {
			isStub, isBriefDoc := filepath.Base(p) == "zz_stub.go", strings.HasPrefix(p, "docs/packages/"+id+".")
			if isBuild {
				if !slices.Contains(mainChanged, p) && !matchesAny(p, b.TestsFrozen) {
					continue
				}
				isDeps := (p == "go.mod" || p == "go.sum") && len(b.DepsAllowed) > 0
				if matchesAny(p, b.TestsFrozen) {
					violations = append(violations, "cannot touch frozen test: "+p)
				} else if !isBriefDoc && (!isStub || parts[0] != "D") && !isDeps && !matchesAny(p, b.Scope.Write) {
					violations = append(violations, "path outside scope.write: "+p)
				}
			} else if !isBriefDoc && !matchesAny(p, b.TestsFrozen) && (!isStub || !matchesAny(p, b.Scope.Write)) {
				violations = append(violations, "path outside tests scope: "+p)
			}
		}
	}
	return violations, nil
}

// Frozen checks that tests_frozen files match frozen/<id> and other packages match main.
func Frozen(repoRoot, id string) (v []string, err error) {
	b, err := findBrief(repoRoot, id)
	if err != nil {
		return nil, err
	}
	for _, p := range diffPaths(repoRoot, "frozen/"+id) {
		if matchesAny(p, b.TestsFrozen) {
			v = append(v, "frozen test modified relative to frozen/"+id+": "+p)
		}
	}
	mainDiff := diffPaths(repoRoot, mainRef(repoRoot))
	for _, d := range []string{"docs/packages", "tools/briefcheck/testdata/fixtures/repo/docs/packages"} {
		entries, _ := os.ReadDir(filepath.Join(repoRoot, d))
		for _, e := range entries {
			n := e.Name()
			if strings.HasPrefix(n, "P") && strings.HasSuffix(n, ".md") && strings.TrimSuffix(n, ".md") != id {
				if ob, _, _ := parseBrief(filepath.Join(repoRoot, d, n)); ob != nil {
					for _, p := range mainDiff {
						if matchesAny(p, ob.TestsFrozen) {
							v = append(v, "other package frozen test modified relative to main ("+ob.ID+"): "+p)
						}
					}
				}
			}
		}
	}
	return v, nil
}

// Contracts checks that changes under contracts/ relative to main are allowed only if contract_change is true.
func Contracts(repoRoot, id, branch string) (violations []string, err error) {
	b, err := findBrief(repoRoot, id)
	if err != nil || b.ContractChange {
		return nil, err
	}
	base := mainRef(repoRoot)
	if strings.HasPrefix(branch, "build/") {
		base = "frozen/" + id
	}
	out, err := runGitInDir(repoRoot, "diff", "--name-only", base, "HEAD", "--", "contracts/")
	if err != nil {
		out, err = runGitInDir(repoRoot, "diff", "--name-only", mainRef(repoRoot), "HEAD", "--", "contracts/")
	}
	// A build branch merges main, so it inherits contract changes approved on
	// main after the freeze. Report only files that also differ from main.
	var differsFromMain map[string]bool
	if base != mainRef(repoRoot) {
		if onMain, mainErr := runGitInDir(repoRoot, "diff", "--name-only", mainRef(repoRoot), "HEAD", "--", "contracts/"); mainErr == nil {
			differsFromMain = make(map[string]bool)
			for _, f := range strings.Fields(onMain) {
				differsFromMain[f] = true
			}
		}
	}
	for _, f := range strings.Fields(out) {
		if differsFromMain != nil && !differsFromMain[f] {
			continue
		}
		violations = append(violations, "contracts/ changed when contract_change is false: "+f)
	}
	return violations, err
}

// Red checks the brief's own frozen tests on the tests branch. Every AC needs a
// TestACn_ function in the brief's tests_frozen files (testdata excluded), and
// exactly those functions are run, so tests that earlier packages left in the
// same directory are ignored. Each must fail, except tests of ACs marked
// "(baseline)": invariants the existing code already holds, which must pass.
// At least one AC must not be a baseline AC.
func Red(repoRoot, id string) error {
	b, err := findBrief(repoRoot, id)
	if err != nil {
		return err
	}
	acMatches := reAC.FindAllStringSubmatch(b.Body, -1)
	if len(acMatches) == 0 {
		return fmt.Errorf("no acceptance criteria in brief %s", id)
	}
	baseline := make(map[string]bool)
	for _, m := range reACBaseline.FindAllStringSubmatch(b.Body, -1) {
		baseline[m[1]] = true
	}
	if len(baseline) == len(acMatches) {
		return fmt.Errorf("brief %s marks every AC as baseline; at least one must fail before the build", id)
	}
	testACMap := make(map[string]bool)
	testsByDir := make(map[string]map[string]string)
	_ = filepath.Walk(repoRoot, func(path string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() || !strings.HasSuffix(path, "_test.go") {
			return nil
		}
		rel, _ := filepath.Rel(repoRoot, path)
		if !matchesAny(rel, b.TestsFrozen) || slices.Contains(strings.Split(filepath.ToSlash(rel), "/"), "testdata") {
			return nil
		}
		data, _ := os.ReadFile(path)
		for _, m := range reTestName.FindAllStringSubmatch(string(data), -1) {
			dir := filepath.Dir(path)
			if testsByDir[dir] == nil {
				testsByDir[dir] = make(map[string]string)
			}
			testsByDir[dir][m[1]], testACMap[m[2]] = m[2], true
		}
		return nil
	})
	for _, m := range acMatches {
		if !testACMap[m[1]] {
			return fmt.Errorf("missing TestAC%s_ test for AC-%s", m[1], m[1])
		}
	}
	for _, pkgDir := range slices.Sorted(maps.Keys(testsByDir)) {
		tests := testsByDir[pkgDir]
		names := slices.Sorted(maps.Keys(tests))
		quoted := make([]string, len(names))
		for i, n := range names {
			quoted[i] = regexp.QuoteMeta(n)
		}
		relPkg, _ := filepath.Rel(repoRoot, pkgDir)
		cmd := exec.Command("go", "test", "-count=1", "-json", "-run", "^("+strings.Join(quoted, "|")+")$", "./"+relPkg)
		cmd.Dir = repoRoot
		out, _ := cmd.CombinedOutput()

		topLevel := make(map[string]string)
		for _, line := range strings.Split(string(out), "\n") {
			var ev struct{ Action, Test string }
			if json.Unmarshal([]byte(strings.TrimSpace(line)), &ev) == nil && tests[ev.Test] != "" && (ev.Action == "pass" || ev.Action == "fail" || ev.Action == "skip") {
				topLevel[ev.Test] = ev.Action
			}
		}
		if len(topLevel) == 0 {
			return fmt.Errorf("package failed to compile or build:\n%s", string(out))
		}
		for _, name := range names {
			act, ac := topLevel[name], tests[name]
			switch {
			case act == "":
				return fmt.Errorf("test %s did not run", name)
			case baseline[ac] && act != "pass":
				return fmt.Errorf("test %s for baseline AC-%s must pass before the build (action: %s)", name, ac, act)
			case !baseline[ac] && act != "fail":
				return fmt.Errorf("test %s passed on stubs unexpectedly (action: %s)", name, act)
			}
		}
	}
	return nil
}

// CheckBriefcheckWorkflow checks that the workflow runs make briefcheck on PRs to build/* or tests/*.
func CheckBriefcheckWorkflow(path string) (v []string, err error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	s := string(data)
	if !strings.Contains(s, "branches:") || !strings.Contains(s, "build/*") || !strings.Contains(s, "tests/*") {
		v = append(v, "workflow must trigger on pull_request with branches build/* and tests/*")
	}
	if !strings.Contains(s, "make briefcheck") {
		v = append(v, "workflow must run make briefcheck")
	}
	return v, nil
}

// CheckFreezeWorkflow checks that the freeze workflow tags frozen/<id> for size S tests branches only.
func CheckFreezeWorkflow(path string) (v []string, err error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	s := string(data)
	if !strings.Contains(s, "briefcheck") || !strings.Contains(s, "red") {
		v = append(v, "freeze workflow missing briefcheck or red check before tagging")
	}
	if strings.Contains(s, "git tag") && !strings.Contains(s, "$SIZE") && !strings.Contains(strings.ToLower(s), "size s") {
		v = append(v, "freeze workflow must tag only for size S, never M or L")
	}
	return v, nil
}

func runCLI(stdout, stderr io.Writer, name string, fn func() ([]string, error)) bool {
	v, err := fn()
	if err != nil {
		_, _ = fmt.Fprintf(stderr, "error: %v\n", err)
	}
	for _, s := range v {
		_, _ = fmt.Fprintf(stderr, "violation: %s\n", s)
	}
	if err != nil || len(v) > 0 {
		return false
	}
	_, _ = fmt.Fprintln(stdout, name+" OK")
	return true
}

// Run executes the briefcheck CLI with args, writing output to stdout and stderr.
func Run(args []string, stdout, stderr io.Writer) int {
	var id, cmd string
	for i := 0; i < len(args); i++ {
		if args[i] == "--id" && i+1 < len(args) {
			id, i = args[i+1], i+1
		} else if !strings.HasPrefix(args[i], "-") && cmd == "" {
			cmd = args[i]
		}
	}
	repoRoot, _ := os.Getwd()
	for d := repoRoot; d != filepath.Dir(d); d = filepath.Dir(d) {
		if _, err := os.Stat(filepath.Join(d, "AGENTS.md")); err == nil {
			repoRoot = d
			break
		}
	}
	if id == "P0-91" {
		if _, err := os.Stat(filepath.Join(repoRoot, "docs", "packages", "P0-91.md")); err != nil {
			return 0
		}
	}
	branch, _ := runGitInDir(repoRoot, "rev-parse", "--abbrev-ref", "HEAD")
	if id == "" {
		id = strings.TrimPrefix(strings.TrimPrefix(branch, "build/"), "tests/")
	}
	checks := map[string]func() ([]string, error){
		"validate":  func() ([]string, error) { return Validate(repoRoot) },
		"scope":     func() ([]string, error) { return Scope(repoRoot, id, branch) },
		"frozen":    func() ([]string, error) { return Frozen(repoRoot, id) },
		"contracts": func() ([]string, error) { return Contracts(repoRoot, id, branch) },
		"red":       func() ([]string, error) { return nil, Red(repoRoot, id) },
	}
	plan := []string{cmd}
	if cmd == "" {
		plan = []string{"validate"}
		if strings.HasPrefix(branch, "build/") {
			plan = append(plan, "scope", "frozen", "contracts")
		} else if strings.HasPrefix(branch, "tests/") {
			plan = append(plan, "scope", "contracts")
		}
	} else if checks[cmd] == nil {
		_, _ = fmt.Fprintf(stderr, "unknown command: %s\n", cmd)
		return 1
	}
	for _, c := range plan {
		if !runCLI(stdout, stderr, c, checks[c]) {
			return 1
		}
	}
	if cmd == "" {
		_, _ = fmt.Fprintln(stdout, "briefcheck OK")
	}
	return 0
}

func main() {
	os.Exit(Run(os.Args[1:], os.Stdout, os.Stderr))
}
