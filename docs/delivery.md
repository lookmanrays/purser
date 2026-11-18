# Purser delivery

The orchestrator integrates worker candidates on `lookman/purser-development`. Each gate links the exact reviewed implementation or design candidate, the independent evidence, the decision and any residual scope. The contract for this delivery is [architecture v0.5](architecture-v0.5.md), the [implementation plan](implementation-plan-v0.5.md) and [D-30 on principal approval](decisions/0030-principal-approval.md).

## M0 · Design and experimental protocol

Accepted at `4460381e44f3dd42d22848bf6d82fea9a817a833` after independent review. See [M0 acceptance](evidence/M0/acceptance.md). M1-A and M1-B are released against the [M1 preflight](evidence/M1/preflight.md).

Dispatched from `122b729bb5e549383dec72d2065b242cfbe5d9d5`. The two planning documents at that commit have SHA-256 digests `28bfc613d93c238bf4211b19fa5484f4eb16cc84ebb6c64d3b08a7237c0a51cf` and `e7ad1f76a62a1ab2ffeb35549b36c7b79f75d69a157bef3f41a55b9b732bdbd7`. D-30 applies to the M7 transport choice. It changes no experimental threshold.

| Packet | Writer | Scope and return |
| --- | --- | --- |
| M0-A | Contracts worker | Versioned proposal, canonical event and receipt bytes, CLI queries, trust binding and worked transitions in `docs/contracts/`. Return local candidate and reproducible vectors. |
| M0-B | Evaluation worker | Versioned manifest and fixture identity, paired seeds, controls, budgets, three arms, external truth, measurements and hand-scored examples in `experiments/manifests/`. Return local candidate and reproducible manifest. |
| M0-G | Orchestrator with independent reviewer | Integrate both packets and verify the correct closure, stale pass, unaffected sibling, guarded Git effect and original thresholds from a clean checkout. |

### Operating envelope

The reference development machine is Darwin 25.5.0 on arm64. Python 3.11.15 with SQLite 3.53.1 and Git 2.50.1 passed local preflight. On this machine the unqualified system `python3` is 3.9.6. Select a project-compatible Python 3.11+ interpreter for all worker commands. No service account or paid model is required to build and exercise the offline kernel and scripted experiments.

A two-connection test on a temporary local WAL database observed zero rows before the writer committed and one afterwards. A live online backup contained the committed row. A guarded Git ref transaction on a temporary bare repository created the branch update and protected receipt together. Repeating the same operation was refused and the receipt identified the intended commit. These are host primitive observations. The product has separate acceptance gates.

The first domain is one local coding repository. The sole runtime principal accepts root outcomes, issues grants and waives obligations. Normal executor work uses disposable state. The first shared effect is a guarded update to an allowed local branch. Working credentials, real-model spending, real-agent isolation, public distribution and off-machine backup activation each belong to their stated later gate.

Before M5 runs model-authored Python, qualify the narrow candidate-execution boundary used by the evaluator. Even a frozen test suite imports candidate code. Keep the trusted scorer, journal, provider credential and canonical repository outside that process. M6 additionally qualifies the interactive coding agent, its tools and its transport. The scripted M0 fixture only exercises known, controlled code.

Track the 3,000-line target using physical Python source lines, including comments and blank lines, in the tracked `purser/` production package. Include semantic authority, projection, admission and closure code even if subsequently extracted under another package. Exclude tests, fixtures and separately qualified adapter-only infrastructure. Crossing the target triggers an architectural review. Record the measured scope and count with each integrated code gate.

## M1 · Local ledger, admission and CLI

Dispatch base `dad1118aee40c509bbbc30a31224777deaf79026` contains the accepted M0 inputs and [implementation preflight](evidence/M1/preflight.md). The store worker owns `lookman/purser-m1-store`. The CLI worker owns `lookman/purser-m1-cli`. Both return local commits to the orchestrator. The orchestrator owns the dependent admission module and integrates the three packages. The independent reviewer tests the resulting immutable commit through the CLI and separate SQLite processes.

M1-G declined `fc6d72a` because a named historical read could contradict its surviving head. The corrected candidate `59454662450feb58e7a76a80f36000ea093d7ef0` passed independent M1-G. See the [acceptance and reproduced outcomes](evidence/M1/acceptance.md).

## M2 · Independent evidence and work closure

M2-A and M2-B are released against the [M2 preflight](evidence/M2/preflight.md). The work worker owns the shared gate/projector registrations and the evaluation worker owns the independent scorer, baseline and scripted harness. The orchestrator owns the dependent driver, CLI integration and real development commitment. The existing frozen inputs remain at their M0 identities.

The actual work item is `W-cold-reasons` in commitment `C-m2-explain-work`. Its live authority is the local Purser workspace at `~/.local/state/purser/development/ledger.sqlite3`. Query the live workspace with `python3.11 -m purser --db "$HOME/.local/state/purser/development/ledger.sqlite3" why C-m2-explain-work`. The scoped intake actor and the local human response have separate actor IDs and immutable receipts.

The product passed its independent [feature and kernel checks](evidence/M2/feature-verification.md) at `70097e0a2b933713d4fe91e6eddb5c2132951f89`. The retained real ledger at revision 20 records independently passing evidence and a root awaiting its principal. All three arms caught 20/20 injected faults in the [first scripted E1 series](../experiments/results/e1-scripted-v1/report.md). The [measurement review](evidence/M2/e1-measurement-review.md) records why the original result is retained and why the orchestrator requires a separately frozen replication before M2-G.
