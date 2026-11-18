# M2 product and development evidence

The independent verifier qualified the work/evidence kernel, stateless driver and cold CLI on Git commit `70097e0a2b933713d4fe91e6eddb5c2132951f89`. Its tree is `c34e297cc406dbc75d7c0c5f361cfd4d4cee77b6`. The product contained 1,623 tracked physical Python source lines. The integrated suite passed 49 tests on Python 3.11.15 and 3.14.3. The frozen wire tests passed 3/3. The verifier also exercised 38 independent checks of authority, evidence integrity, generations, criterion replacement and coverage, and killed an active driver during a callback. A fresh process recovered the same scoped assignment and submitted the candidate once. Historical original bytes, the prior golden answers and the explicitly added M2 answers were checked through the CLI.

## What the real feature did

The real development commitment was to explain the evidence or principal decision still required for work to close. The verifier froze the [criterion](observer/criterion-v1.json), [fixture](observer/fixture-v1.json) and [environment](observer/environment-v1.json) before the feature was submitted. The verifier ran 21 fresh Purser CLI processes and eight Git commands on the actual commit. The separately regenerated fixture reproduced 37 original events.

| Controlled fixture revision | Public behavior |
| ---: | --- |
| 26 | The replacement commit was current. Its named obligation lacked evidence, and the child stayed open. |
| 31 | Genuine passing evidence for the earlier commit arrived. A close request still failed on the replacement. |
| 37 | Independent passing evidence for the replacement closed the child. The root explained that it awaited the principal and the frontier named that action. |

The independent report has SHA-256 `0257b7dbdb67520209f80fe2cfef07c5e6d5e3d8f1c6b254af39fbdcda37e443` and length 37,203 bytes. Its exact [observation program](observer/observe_feature.py) has SHA-256 `028f5299caf3c3aa3b80ca22229e186903c0398c06944947fea7f0d6a383d571`. Both the [fixture constructor](prepare_continuity.py) and observation program refer to the feature candidate and its frozen inputs.

## Retained development history

The actual local ledger is separate from the controlled fixture. Its principal activated the commitment at revision 8. The independent criterion was enrolled at 10, the work was assigned at 12, the candidate was submitted at 14 and its run opened at 16. The observer published the measured observation at 18 and the verifier recorded a pass at 20. At that checkpoint the root was submitted and its exact outcome `2ea75e79227a5076d8b9b599a3f9ffa77a478e5a82682d18bbdbe61556425524` awaited the local principal.

The local principal accepted that exact outcome at 16:45:35 UTC on 18 November 2026. The single admitted batch recorded `WorkAccepted` at 21, `WorkClosed` at 22, `CommitmentSatisfied` at 23 and the principal's receipt at 24. The owner also confirmed acceptance in the architect chat. Resubmitting the same proposal through the local CLI returned the original receipt `a7e4312007ff3c1add28a61236e840b606b28c4939e1b8c6e89e8c193bc5f2be` at 24. Fresh `why` calls read a closed root and a satisfied commitment.

The independent verifier accepted the product and principal trace at integration `066641a3ead3aade78f0af786ed1ee1636daa334`. It reconstructed two blank ledgers, checked all 24 original event envelopes, derived the accepted outcome from its evidence and exercised 17 fresh CLI processes. Redelivery in the restored ledger returned the same receipt and preserved revision 24. The [independent audit](independent-principal-verification.json) has SHA-256 `6d2c9a2a32e84a06b86830d23a00608dbe67382dbff92b61454d1214f1e9430e`.

The [portable evidence archive](development-log.zip) now contains all 24 original events and four digest-addressed artifacts, including the full independent report. Its SHA-256 is `3e26990bb328f6449c8734a75c6a7c8502afcf8e0fdc9378f15f9548a06c6122`. Its first 20 events and all four artifacts match the earlier 20-event archive byte for byte. That earlier archive, SHA-256 `dc9757effb786e67d07b27988b6c9ea7d728ee445f4957bf9ed1a7bbab88e525`, is preserved at commit `a58cf40`. Rebuilding each checkpoint in an empty database reproduced its head, projection and six historical answers.

```sh
python3.11 -B docs/evidence/M2/development_trace.py docs/evidence/M2/development-log.zip
```

| Verified value | Awaiting acceptance at revision 20 | Accepted at revision 24 |
| --- | --- | --- |
| Head | `e3659ee94efdb6e721a25753d65540b307b071ba1b48611b35a54bb5ab1998af` | `09ad2ae06114ebb771ecd87d0d386170bdc897febe701b123056c4378fb8bfe4` |
| Projection | `778dcbd55828a8e027764b2fc382a19018c7e48f82cc25f8ddfef47d2665aca7` | `634218f8cfe75cb3281f349346300232638c54edeb9b202ecd17725e105ade15` |
| Continuity | q01, q02, q07, q08, q09, q10 | q01, q02, q07, q08, q09, q10 |
