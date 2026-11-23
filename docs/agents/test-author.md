# Playbook: test author

You write the frozen tests for one package. You write no implementation.

## 1. Set up

Run from the repository root. `<ID>` is your package ID.

```bash
git fetch origin --tags
git worktree add .worktrees/tests-<ID> -b tests/<ID> origin/main   # if the branch already exists: git worktree add .worktrees/tests-<ID> tests/<ID>
cd .worktrees/tests-<ID>
```

Work only inside that directory from now on.

## 2. Read

1. `AGENTS.md`.
2. `docs/packages/<ID>.md`, all of it. It is your specification.
3. Every contract named in its `contract` field, and the vectors and reference files in `scope.read`.

Do not read or write any implementation of this package. If you find one, ignore it.

## 3. Write

- Write only paths matching `tests_frozen`, plus `zz_stub.go` files in the package directories of `scope.write`.
- A stub holds exactly the contract's Go declarations, with blank parameter names (`_`) and a doc comment on every exported identifier. Bodies call a panicking function variable and then return zero values, so staticcheck does not treat the stubs as never returning (it would flag the test code after each call as dead):

  ```go
  var unimplemented = func() { panic("unimplemented: <ID>") }

  // Encode returns the canonical JSON bytes of v.
  func Encode(_ any) ([]byte, error) { unimplemented(); return nil, nil }
  ```
- Every acceptance criterion gets at least one test named `TestACn_<What>`. Cover every bullet of the brief's Test plan.
- Start every test and subtest with a panic guard, so one stub panic does not abort the test binary:

  ```go
  func guard(t *testing.T) {
      if r := recover(); r != nil {
          t.Fatalf("panic: %v", r)
      }
  }
  // first line of each test and subtest: defer guard(t)
  ```

- Prefer table-driven tests. Read vectors through `internal/testvectors` once it exists, or directly from `reference/python/docs/contracts/` with the standard library.
- Assert exact values from the contract and the vectors, not just "no error". **Never type an expected value from memory:** copy it from the vector or reference file, or compute it from that file in the test. (P0-06 froze two invented strings and P1-02a an invented approval ID; all had to be re-frozen.) Before pushing, run `python3 -I tools/check_literals.py <your test files>` and resolve every `SUSPECT` line.
- **Derive negative cases from the reference implementation** when one exists under `reference/python/`: read its validation code and turn every rejection branch into a test. Generate fixtures by running the reference (`python3 -I`, with `reference/python` on `sys.path`), and record how they were generated in the fixture's `_comment`.
- For a package that encodes, decodes or verifies data, add a **parity fixture**: inputs generated from the reference with its exact outputs, plus edge-case probes it accepts or rejects (stray or trailing characters, duplicates, overflow, inconsistent fields inside a group).

## 4. Check

```bash
gofmt -l .                     # must print nothing
go vet ./...
golangci-lint run              # must pass
go test ./<package path>/...   # every TestACn_ test must FAIL, and only by "unimplemented"
```

## 5. Deliver

```bash
git add -A && git commit -m "<ID>: frozen tests and stubs"
git push -u origin tests/<ID>
gh pr create --base main --head tests/<ID> --title "<ID>: frozen tests" --body-file <your notes file>
```

The pull request body contains:

- a table mapping each AC to its tests;
- the failing `go test` output, trimmed to the FAIL lines;
- anything in the contract or brief you found unclear.

CI on a tests pull request is red by design until P0-02 adds `make red`: the new tests fail. Lint, vet and briefcheck must still pass.

Report the pull request URL and stop.

## If the specification is wrong

If the contract is ambiguous, contradicts the brief, or cannot be tested as written, write `docs/packages/<ID>.contradiction.md` (what is wrong, the evidence, your proposed fix), commit it on your branch, open the pull request with that file only, and stop.
