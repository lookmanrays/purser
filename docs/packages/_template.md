---
id: PX-NN
title: Short name
phase: PX
status: draft            # draft | ready | done
size: S                  # S: up to 200 changed lines; M: up to 600; L: allowed only in draft, split before ready
builder: codex           # codex | antigravity | purser | you
test_author: antigravity # the other agent (antigravity-2 = a second Antigravity agent in its own chat); never the builder; "none" only for bootstrap or review work; purser/purser only when Purser's test-author role (P4-06) writes the tests
reviewer: antigravity    # the test author, or you
merge: auto              # auto only for size S with no contract change; otherwise human
contract_change: false   # true when the package adds or changes anything under contracts/
depends_on: []
contract: contracts/<area>/<name>.yaml@v1   # a list when the package implements several; "self" when this brief is the contract
scope:
  write: []
  read: []
tests_frozen: []
reuse: []
deps_allowed: []
budget: {changed_lines: 600, excluding: tests}
---

# PX-NN Short name

## Goal

One to three sentences: what exists when this package is done.

## Context

Why this package exists and which design sections it serves.

## Design

Decisions, data flow, interfaces and invariants the builder must follow. The contract file is authoritative where it is more precise.

## Out of scope

What this package must not do, even if it looks convenient.

## Test plan

What the test author must cover. Tests come from the contract, the acceptance criteria and the vectors, never from an implementation.

## Acceptance criteria

- AC-1 A checkable statement. Every AC is proven by at least one test named after it.

## Definition of done

The standard Definition of done below, plus anything listed here.

## Escalate if

Situations where the agent must stop and write a contradiction file instead of improvising.

---

## Standard Definition of done (applies to every package)

1. Every frozen test passes without being edited.
2. `make check` is green: build, vet, golangci-lint with depguard layer rules, `go test -race ./...`.
3. Coverage is at least 85% for packages under `internal/kernel/` and at least 70% elsewhere.
4. `make briefcheck ID=<id>` passes: scope, frozen tests and contract rules.
5. No third-party module was added unless it is listed in `deps_allowed`.
6. Every exported identifier has a doc comment, and the contract file is unchanged.
7. The PR body has an evidence note: each AC with the tests that prove it, the commands run, coverage numbers and any deviation.
8. No TODO without a linked issue or brief.

## Rules for the test author

- Derive tests only from the contract, the acceptance criteria, the vectors and the reference specs named in the brief.
- Name each test after the AC it proves, for example `TestAC2_DuplicateReturnsSameReceipt`.
- Tests compile against stubs and fail before the implementation exists.
- Write only inside `tests_frozen`, plus `zz_stub.go` stubs in the package directories of `scope.write`.
- Freezing follows AGENTS.md, Branches and freezing: the tag `frozen/<id>` is the frozen state.
