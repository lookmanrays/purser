# Purser planning batch 1

This folder drops into the root of the Purser repository. It holds every package brief and the P0 to P2 contracts, so that no code is written before the plan is defined.

## Contents

| Path | What |
| --- | --- |
| `AGENTS.md` | Rules for Codex and Antigravity: scope, frozen tests, escalation, the branch and freeze flow |
| `docs/design.md` | Export of the design doc (diagrams are kept as their Mermaid copies) |
| `docs/packages/README.md` | Index of all 66 packages with a Mermaid dependency graph (generated) |
| `docs/packages/P*.md` | One brief per package: front matter that CI parses, then Goal, Context, Design, Out of scope, Test plan, Acceptance criteria, Definition of done, Escalate if |
| `docs/packages/_template.md` | The brief template and the Standard Definition of done |
| `docs/packages/VOCABULARY.md` | Design names mapped to the kernel's v1 names |
| `contracts/kernel/` | 13 interface contracts for P1 and the graph-snapshot operator |
| `contracts/graph/` | 8 interface contracts for P2 |
| `contracts/model`, `plan`, `iface` | 5 boundary contracts between P3 and P7 |
| `contracts/format/`, `contracts/records/` | Drafts of the contract format and the record kinds, finalized by P0-04 and P0-03 |
| `tools/check_briefs.py` | Validator and index generator until P0-02 replaces it |

Status: P0 to P2 briefs are `ready`; P3 to P7 briefs are complete but `draft`, and each phase's planning batch adds their contracts and moves them to `ready`.

## How to use it

1. Read the briefs and contracts for P0 to P2 and approve or comment. This is the "one or two sittings" approval in the plan.
2. Copy the folder into the repository root (P0-01 moves purser-main under `reference/python/`).
3. Add `docs/plan.md` from the plan doc's Export menu (Markdown).
4. Run `python3 tools/check_briefs.py --index` after any edit to a brief. It needs PyYAML and must report 0 errors. The 34 warnings are the P3 to P7 contract files that do not exist yet.
5. Create one tam task per brief and start with P0-01 (Codex) and P0-06 (Antigravity) in parallel.

## Decisions you need to make

| Where | Decision |
| --- | --- |
| P0-01 | Repository: the existing purser repository with purser-main moved to `reference/python/` (recommended), or a new repository with it as a submodule |
| P2-08 | Fill `bench/graph/repos.yaml` with one of your Go projects and one of your TypeScript projects |
| P3-01 | Confirm the employer-provided Bedrock access may be used for this project |
| P6-01 | tam must confirm MCP access for a custom client |

## Changes from the plan doc

The plan doc has been updated to match this batch.

- 66 packages: P1-08 is split into P1-08a (effect operators) and P1-08b (Git effect adapter).
- P0-06 also records golden event logs from purser-main's own test scenarios. The frozen vectors cover only three record kinds; the golden logs cover all sixteen.
- P2-03 is a syntax fallback using `go/parser` and the TypeScript compiler API. Tree-sitter's Go bindings need cgo, and the build is cgo-free.
- P1-07 is "Decisions and dispatch"; approvals already belong to the gate's M1 operators in P1-03.
- Dependencies were tightened wherever a package reuses another's code; `tools/check_briefs.py` now enforces that rule.
- Freezing works through tags: the tests branch is tagged `frozen/<id>` and never merged on its own; the builder branches from the tag, so `main` stays green.
