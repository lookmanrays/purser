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
- No module outside `deps_allowed`.
- Stay within `budget.changed_lines` (tests excluded). If you would exceed it, stop and propose a split.
- Do not refactor, rename or improve anything the brief does not ask for.

## 4. Work

1. For size M or L, write an execution plan first: files, order, and the command that proves each AC.
2. Implement in small commits.
3. Keep going until every frozen test passes and `make check` is green. Do not stop early and do not report partial work as done. If something truly blocks you, name the AC and the reason.

## 5. Check

```bash
gofmt -l .                                          # must print nothing
make check                                          # build, vet, lint, go test -race
go test -cover ./<package path>/...                 # report coverage: 85% for internal/kernel, 70% elsewhere
git diff --stat frozen/<ID> -- $(paths in tests_frozen)   # must print nothing
```

If the package has fuzz targets, also run `make fuzz FUZZ_PACKAGE=./<package path>` and report the result.

## 6. Deliver

```bash
git fetch origin && git rebase origin/main          # frozen files must stay byte-identical to the tag
git push -u origin build/<ID>
gh pr create --base main --head build/<ID> --title "<ID>: <title from the brief>" --body-file <your notes file>
```

The pull request body contains: the execution plan (M and L), a checklist with the command and output proving each AC, coverage, and the evidence note (Go version, commands run, results).

Report the pull request URL and stop. When CI or the reviewer asks for changes, fix them in this same chat on the same branch.

## If the specification is wrong

If the brief, a contract or a frozen test is wrong or contradicts another, write `docs/packages/<ID>.contradiction.md` (what is wrong, the evidence, your proposed fix), commit and push it on your branch, open the pull request, and stop. Never work around a frozen test.
