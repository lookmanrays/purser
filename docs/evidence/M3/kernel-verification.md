# M3-A local kernel verification

Implementation source `1243dde2d23f9dc67986d78ea2d60099bff2b7c4` has product tree `c2d2e50c748324fc0b4fe697ccf490efc47c15f9` and 2,218 physical Python lines in purser. The package preflight is `c04d1d399005fdd7f2efbffd14f7a824ee3c250c`. The callback, observation-subject and acquired-work-outcome audits are recorded at `2d4e05a`, `689b5ea` and `86f2d67`. The original product candidate `e29b092` failed both independent observation-subject and direct-support outcome controls. The [operation contract](../../contracts/decisions-v1.md) describes the replacement.

## Actual scripted product trace

The [raw archive](kernel-trace.zip) has 28,048 bytes and SHA-256 `b04b7bf4afeeef0b340890d78641c584146ff5b5bab7e6402b3b9f27dccc96e8`. It contains all 108 canonical SQLite event originals, all 11 referenced artifact objects, the exact arguments and replies of 23 public CLI processes, original falsification programs and replacement outputs. The trusted local scripted fixture uses controlled artifact-byte candidates and independent role bindings, with zero model calls.

| Complete revision | Operator-visible result |
| ---: | --- |
| 80 | W1, W2, W3 and their all-coverage parent are closed. D-old is effective. |
| 82 | A scoped executor's new opinion leaves D-old and all four closures effective. |
| 86 | Confirmation of A1 and contradiction of an untriggered supporting assumption preserve the decision and W2. |
| 89 | Obs-change at event 87 invalidates A1, reconsiders D-old and marks W2 and the W-B/W-C dependency cycle stale at that event. The automatic parent reopens separately. The child_of edge from W-tree into W-B and W-cross's dependence on W-tree produce no additional assumption invalidations. |
| 91 | D-new, effective revision 90, supersedes D-old. The current W2 explanation retains its former causal edges and their observation. The prior candidate still cannot close. |
| 97 | The new generation has submitted. A late pass for the old run is visible in history. The new W2 obligation has no qualifying pass. |
| 108 | Fresh W2 evidence closes W2 and its parent. W1 has kept generation 1, outcome `e9804b994e4c5e88120e038cfcff30e860a5f099c2c823e90a4f6302ef6ec3d2`, run V-W1 and evidence E-W1. W3 also retained its original closure. |

The archived head is `0bed655e7394d91946ec7a979010e21a6540fe14ed86921b4ed2da0359314294`. The [restoration program](replay_kernel_trace.py) checks membership and all file digests, replays the originals into a fresh WAL store, executes the 23 CLI queries with model access absent and rereads the original event bytes. On Python 3.11.15 and 3.14.3 it reproduced every result. From the checkout root run `python3.11 -B -m docs.evidence.M3.replay_kernel_trace docs/evidence/M3/kernel-trace.zip`.

## Adversarial controls

- With two separate SQLite writers, a close first produced a historical closed checkpoint before an observation made the work stale. In the opposite order the close received stale_base and only its rejection receipt was appended.
- In two driver processes selecting the same revision, the first callback started while its assignment qualified. The observation committed at revision 19. The second physical release was refused at 20 without invoking its adapter. The first callback's late submission was refused at 21. Replaying one original release proposal returned its own receipt, and unrelated receipt traffic preserved current dispatch eligibility.
- A verifier presented a confirmed premise observation for a legitimate run. The gate admitted exactly one rejection receipt at revision 46 with witness_mismatch. A coherently rehashed EvidenceRecorded original claiming such a witness fails replay at that event. A proper delayed observation and pass still belongs to its historical run.
- After a previously closed aggregate reclosed on a changed selected child, its outcome changed from `6fb04313…` to `f6c5f1ba…`. Its consumer kept its original acquired digest, became stale, exposed rework and refused old closure. A new assignment and verification restored closure. The six all/any/k-of-n selected/unchanged controls traverse an additional automatic structural dependent. Each changed direct outcome advanced the structural generation and fenced the downstream worked generation. All three unchanged selected bases preserved the prior positive evidence and closure. Additional controls cover deleted and transient incoming edges, adding/removing direct support on a closure-eligible unassigned aggregate, and a dependency cycle interacting with parent coverage.

The [product regressions](../../../tests/test_decisions.py) repeat the mixed tree, both writer orders, local driver fencing, original-event validity, structural chains and historical no-model queries. The three separately authored falsification programs and observed replacement results for both interpreter versions are in the archive's controls directory.

## Compatibility

Both Python versions passed 118 repository tests and three frozen contract vectors. Restoring the original M2 human-acceptance trace preserved all 24 original events, four artifacts, all six earlier answers, head `09ad2ae06114ebb771ecd87d0d386170bdc897febe701b123056c4378fb8bfe4` and projection `634218f8cfe75cb3281f349346300232638c54edeb9b202ecd17725e105ade15`. The joined technical reviewer and architect own their separate acceptance decisions and E2 measurement boundary.
