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
- A stub holds exactly the contract's Go declarations. Function bodies are `panic("unimplemented: <ID>")`. Blank unused parameter names (`_`) so the linter passes, and give every exported identifier a doc comment.
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
- Assert exact values from the contract and the vectors, not just "no error".

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
