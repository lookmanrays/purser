# Registration of the first scripted E3 series

The architect committed the 66 original trial identities at `00644c87aa5e5b01068a6835b6eef103a505f853`. Collection started at 23:26:43 UTC on 18 November 2026. At launch, the directory held only the committed `freeze.json`, with SHA-256 `b22c4beaf543cd01ae122b8b2dbbc68965c82e32e48653660b66c30a01abe82e`. The independent source reviewer had released `b99e7ddfc82f413e8d5b2a2b2dce9b4e7a3fb3fd` for this run. [Registration](../../../experiments/results/e3-scripted-v1/freeze.json) · [Independent source review](observer/source-qualification-b99e7dd.zip)

| Registered input | Identity or boundary |
| --- | --- |
| Implementation | 88 Python source files. E3 tree `44695424638e0f2e75bdecc04cbe2b08d67561a0`, product tree `be08b616456806724c0bd66f9239d855214dc427`. |
| Fixture | Starting Git commit `55686bd5152e10a53dfdc2c003974ea405a3a075`, prepared update `5828b906ebc2aca8bbaf69c8d50fa8eb1295af7b`, branch `refs/heads/notify`. Every trial receives a fresh disposable repository. |
| Original replicate | Twenty primary seeds 3001–3020 in each of the three arms. Each arm also has one clean run and one separate run with a live writer. All 66 entries match the independent reviewer’s frozen order. |
| Budget and host | 96 public steps, 60 seconds from release and zero model calls per unit. Local arm64 Darwin 25.5.0, CPython 3.11.15, SQLite 3.53.1, Apple Git 2.50.1, C locale and UTC. |

The independent reviewer reconstructed 12 private qualification trials from 305 referenced objects, 12 repository identities and two original Git packs. The replacement stress writer stayed pending while it held permission to write. In a separate control the reviewer resumed the same live process and observed its single branch update. In another control it ended without applying the update. Both behaviors were possible at the participant’s decision time. The private offline report reproduced byte for byte. [Reporter qualification and costs](e3-report-verification.md)

An additional process stop after a second attempt wrote but before its reply preserved the first attempt’s known outcome, its timely public decision, six observed calls and one observed update. The exact final elapsed time remained unknown. The partial record’s generic eligibility message also mentioned the starting exposure, which its detailed evidence still established. The independent source packet records this limitation and has SHA-256 `924b688a0d72b5bb2d3944ad658d76eb56f0fcdf05aa59eb28e276f3c1c57dc3`. [Frozen scoring controls](observer/e3-reference-controls.json)
