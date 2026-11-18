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

The product passed its independent [feature and kernel checks](evidence/M2/feature-verification.md) at `70097e0a2b933713d4fe91e6eddb5c2132951f89`. The principal accepted the exact verified CLI outcome. The retained real ledger at revision 24 shows a closed root and a satisfied commitment. All three arms caught 20/20 injected faults in the [first scripted E1 series](../experiments/results/e1-scripted-v1/report.md). The [measurement review](evidence/M2/e1-measurement-review.md) records why the original result is retained and why the orchestrator requires a separately frozen replication before M2-G.

The independent reviewer declined evaluator candidate `3785d19` before registration of replicate 2. An actual `done` declaration on a faulty Git commit disappeared from the score, and a contradictory terminal record still qualified for the overhead comparison. Current recovery state and the preserved raw controls are in the [measurement review](evidence/M2/e1-measurement-review.md#sessionrebaserequired). No result from this candidate contributes to the registered study.

The owner authorized a separate primary implementation chat for recovery. Its Codex ID is `01a1ef8e-6e96-7491-be33-5a7a9351e08d`. The architect chat retains project scope, milestone decisions and integration.

**M2-G accepted.** The independent verifier issued GO on the integrated product, real accepted commitment and registered E1 replication at `63a9439382d68d68c65a9ee56011a94c9ea648e0`. Each arm caught all 20 faults. The plain loop took 24 steps on clean work, compared with 42 for Purser and 42 for the equal-verifier loop. The experimental source, raw results, checksums and gate disposition are in the [M2 acceptance record](evidence/M2/acceptance.md).

## M3 · Reconsider decisions and preserve unaffected work

M3-A and M3-B are released from the accepted M2 base at `661a08c1d26fc9811ecd0d6c9656640f03631e89`. The implementation chat owns the two technical packets, their preflights and their independent candidate evidence. The architect owns integration and the M3-G decision. [M3 dispatch](evidence/M3/dispatch.md)

The real project commitment is `C-purser-delivery` in the same local workspace as the accepted M2 change. The human principal approved its activation at revision 32 through request `6287a01debb5`. The root covers the released milestone work and the final project disposition. The independent verifier enrolled [the M3 criterion](evidence/M3/observer/criterion-v1.json) and [its environment](evidence/M3/observer/environment-v1.json) at revision 38. At revision 40 the architect took assignment `AS-m3-integration-1` for the integrated candidate. Inspect that assignment with `python3.11 -m purser --db "$HOME/.local/state/purser/development/ledger.sqlite3" context AS-m3-integration-1`. M5 and M8 retain their separate decisions.

M3-A is integrated at `283096b9bb4c918c22285cbf83a41e97e384d365`. Its product tree matches the [independent qualification](evidence/M3/observer/independent-product-GO-69f1935.json) on `69f1935`. A cold CLI process on the integrated checkout read the original human-accepted M2 root at revision 24 and the current M3 assignment at revision 40. The [precollection E2 review](evidence/M3/review-f270afa/review.md) found that provisional evaluator `f270afa` missed a real reopen/reclose, misclassified a contaminated clean comparison and failed to recover available Git evidence. The official E2 series awaits qualification of a replacement evaluator.

The next E2 candidate, `42031bfd37a90d2f74905b3c3c64069ccd9cbcc9`, counted the previously hidden state changes. A separate executor then received three real work contexts without changing a plan or database row. Each cost one step under the frozen protocol. The plain loop reported 30 total steps and 13 rework steps where the witness counted 33 and 16. Each verifier-equipped arm reported 48 and 25 where the witness counted 51 and 28. The [independent worker receipts](evidence/M3/review-42031bf/independent-worker-receipts.json) identify the requests, responses and unchanged state. These were private controls.

The technical implementation session stopped after its two prior remediation attempts. Its recovery-only commit is `9b0ee54731c7d07f7e8861ea30e9261ba748a174`. Use `git show 9b0ee54:docs/evidence/M3/e2-recovery-handoff.md` for the source refs and archives. The architect verified every one of the 77 independent evidence files and 34 component evidence files against their manifests. E2 registration and M3-G await a fresh primary implementation and independent qualification. M3-A retains its accepted product boundary.
