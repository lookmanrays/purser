# Playbook: builder

You implement one package so that its frozen tests pass. You never change the tests.

## 1. Set up

Run from the repository root. `<ID>` is your package ID. In a bake-off, the prompt gives you a branch suffix such as `-codex`; add it to the branch and worktree names.

```bash
git fetch origin --tags
git worktree add .worktrees/build-<ID> -b build/<ID> frozen/<ID>   # if the branch already exists: git worktree add .worktrees/build-<ID> build/<ID>
cd .worktrees/build-<ID>
```

If the tag `frozen/<ID>` does not exist, stop and report it: the tests are not frozen yet.

Work only inside that directory from now on.

## 2. Read

1. `AGENTS.md`.
2. `docs/packages/<ID>.md`, all of it. It is your contract for this task.
3. Every contract named in its `contract` field, the files in `scope.read`, and the frozen tests.

## 3. Rules

- Write only paths matching `scope.write`. Never edit a path matching `tests_frozen`, anything under `contracts/` or `reference/`, or another brief.
- Replace every `zz_stub.go` with the real implementation (delete the stub file).
- No module outside `deps_allowed`. Adding a listed module may change the root `go.mod` and `go.sum` (`go get <module>`, then `go mod tidy`); the scope check allows it whenever `deps_allowed` is non-empty, so this is not a scope conflict.
- Stay within `budget.changed_lines` (tests, `go.mod` and `go.sum` excluded). If you would exceed it, stop and propose a split.
- Do not refactor, rename or improve anything the brief does not ask for.

## 4. Fidelity rules

These come from the P1-01 bake-off, where a build passed every frozen test and still accepted input the reference rejects.

- **Match the reference, not only the tests.** When `scope.read` names files under `reference/python/`, read their validation code and reproduce every rule, including rules the tests do not cover. Before opening the pull request, check edge cases against the reference directly, for example `python3 -I -c "import sys; sys.path.insert(0, 'reference/python'); ..."`.
- **Probe the negative space of a strict contract yourself:** trailing or stray characters, duplicates, empty and maximum values, overflow, wrong types, and inconsistent fields inside a group.
- **Copy the contract's Go declarations verbatim:** same names, field order, types and methods. Do not regroup or reorder them.
- **Validate data where it is parsed,** inside the parser or the type, never with a byte or regex scan over structured input.
- **Keep error context:** `Error()` reports the code, the revision or position, and the detail; wrap underlying errors with `%w`.
- **Prefer strict standard-library facilities** (for example Go 1.27's `encoding/json/v2` and `jsontext`) over hand-written parsers when they meet the contract.
- **One file per responsibility;** split files that grow past about 300 lines.

## 5. Work

1. For size M or L, write an execution plan first: files, order, and the command that proves each AC.
2. Implement in small commits.
3. If your launch prompt allows delegation to Antigravity, follow [delegation.md](delegation.md); otherwise do all the work yourself.
4. Keep going until every frozen test passes and `make check` is green. Do not stop early and do not report partial work as done. If something truly blocks you, name the AC and the reason.

## 6. Check

```bash
gofmt -l .                                          # must print nothing
make check                                          # build, vet, lint, go test -race
go test -cover ./<package path>/...                 # report coverage: 85% for internal/kernel, 70% elsewhere
git diff --stat frozen/<ID> -- $(paths in tests_frozen)   # must print nothing
```

If the package has fuzz targets, also run `make fuzz FUZZ_PACKAGE=./<package path>` and report the result.

## 7. Deliver

```bash
git fetch origin && git rebase origin/main          # frozen files must stay byte-identical to the tag
git push -u origin build/<ID>
gh pr create --base main --head build/<ID> --title "<ID>: <title from the brief>" --body-file <your notes file>
```

The pull request body contains: the execution plan (M and L), a checklist with the command and output proving each AC, coverage, a **Reference parity** section (each reference rule the frozen tests do not cover, and how you matched it; write "no reference" when there is none), and the evidence note (Go version, commands run, results).

Report the pull request URL and stop. When CI or the reviewer asks for changes, fix them in this same chat on the same branch.

## If the specification is wrong

If the brief, a contract or a frozen test is wrong or contradicts another, write `docs/packages/<ID>.contradiction.md` (what is wrong, the evidence, your proposed fix), commit and push it on your branch, open the pull request, and stop. Never work around a frozen test.
