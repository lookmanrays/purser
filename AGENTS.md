# Purser: rules for coding agents

Purser is an agentic engineering runtime written in Go. The design is `docs/design.md`; the plan is `docs/plan.md`.
Your task is exactly one package brief, `docs/packages/<id>.md`. Read all of it before you start.

## Rules

1. Work only inside the brief's `scope.write`, never on paths in `tests_frozen` (frozen wins where both match). In a tests PR, work only inside `tests_frozen` plus stubs. CI rejects anything else.
2. Never edit frozen tests or anything under `contracts/`. If either looks wrong, stop and escalate.
3. To escalate, write `docs/packages/<id>.contradiction.md` (what is wrong, the evidence, your proposed fix), push it, open or update the pull request with it, and stop. The pull request is the escalation channel; do not use a task tracker or a browser.
4. Reuse what the brief lists under `reuse`. Add no third-party module unless it is listed in `deps_allowed`.
5. Done means the brief's Definition of done, including the evidence note in the PR body.
6. Over the brief's budget of changed lines (tests excluded)? Stop and propose a split.
7. For a size M or L package, start the PR description with an execution plan and keep it current.
8. Never weaken a check to get green: no lowered coverage floor, disabled lint rule, skipped race run or deleted fuzz target.
9. Never take identity, role or authority from JSON input. Bindings come only from trusted ingress.

## Branches and freezing

1. Tests: the brief's `test_author` opens a PR from `tests/<id>`, written from the contract and acceptance criteria only. It may also add `zz_stub.go` files in the package directories of `scope.write`, holding exactly the contract's Go declarations with bodies that `panic("unimplemented: <id>")`.
2. CI on a tests branch runs `go vet ./...`, `make briefcheck ID=<id>` and `make red ID=<id>`. `red` passes only when every AC has a `TestACn_` test and every one of them fails on the stubs.
3. Freeze: for size S, CI tags the tests branch head `frozen/<id>` once those checks pass. For size M and L, you approve the tests PR first, as part of the phase's planning batch. The tests PR is closed, not merged, so `main` stays green.
4. Build: the brief's `builder` branches `build/<id>` from the tag `frozen/<id>`, replaces the stubs, and opens one PR into `main` that carries the frozen tests and the implementation together.
5. CI on a build branch checks scope against `frozen/<id>`, checks that the package's frozen files are byte-identical to the tag, and checks that no other package's frozen files changed against `main`.

## Layout

```text
cmd/purser/                 CLI entry point
internal/kernel/codec       canonical JSON, hashes, IDs, UTC times
internal/kernel/projection  records, edges, receipts; the pure event reducer registry
internal/kernel/store       SQLite event log, transactions, projections, replay
internal/kernel/gate        admission, receipts, operator registry, M1 operators
internal/kernel/ops/...     domain operators (work, decision, effect, later extensions)
internal/kernel/view        WorkView: basis, outcome, closure, reasons, answers, frontier
internal/kernel/impact      invalidation and coverage settlement
internal/kernel/artifacts   SHA-256 content store
internal/effects/git        governed Git effects
internal/graph/...          system graph
internal/cli                command implementations
tools/briefcheck            brief, scope and frozen-test checks
contracts/                  contracts and schemas (frozen once approved)
testdata/vectors/           conformance vectors
reference/python/           purser-main, read-only reference implementation
```

## Commands

`make check` · `make test` · `make fuzz` · `make bench` · `make briefcheck ID=<id>` · `make red ID=<id>` · `make golden`

## Environment

Latest stable Go, pinned in `go.mod`. Git 2.45 or newer (reftable). SQLite through `modernc.org/sqlite` (no cgo). Linux or WSL2.
