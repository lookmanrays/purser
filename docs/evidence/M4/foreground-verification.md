# Foreground Git publication and recovery

Product candidate `62ac9b63761bb03e1f8ca6a9f65f5fc9a7429c61` has `purser/` tree `be08b616456806724c0bd66f9239d855214dc427`. Its public changes join the executor, SQLite gate, Git broker and independent observer. The architect ran all 230 repository tests on Python 3.11.15. All passed. A separate Python 3.14.3 run passed the 55 tests covering the affected Git, authority, CLI, driver, work and decision paths. The three original M0 vector checks also passed. [Foreground API and CLI usage](../../contracts/git-effects-v1.md#running-a-governed-effect) · [Six end-to-end tests](../../../tests/test_git_foreground.py)

| Point of process termination | What the next process saw and did |
| --- | --- |
| Before dispatch | The intent was ready and Git had zero publications. A CLI turn with the registered repository published once. The next turn confirmed it. |
| After SQLite authorized the write | While the broker lived, the observer recorded pending. After its owner killed and reaped it, the observer saw the original branch, an intact history and no receipt. Re-delivery of T1 returned its old receipt. A fresh T2 published once. |
| After Git published, before its reply | SQLite still listed T1 as dispatching. The native branch history showed one publication. The independent observation attributed that publication to T1. |
| After reconciliation committed, before its reply | The retry recovered the existing observation with Git offline. Re-delivery of the original dispatch also recovered its receipt. A fresh transport carrying the same logical intent returned `confirmed_happened`. Git still showed one publication. |

A separate CLI process revoked the grant on each side of the cutoff. When revocation committed first, the branch never moved. When authorization committed first, the released attempt published once and a new process reconciled it after revocation. The [authority checks](authority-verification.md) also exercise exact expiry, actor and path attenuation, stale assignments and ref conflicts. The [Git component checks](git-verification.md) include the surviving native child and the protected receipt after a later advance.

The architect restored the original M2 project log at revision 24, the accepted M3 project log at revision 51 and the M3 mixed-work control at revision 108. The archived heads, event bytes and all 28 original query replies matched. Newly supported questions on histories predating them returned empty arrays. The M4 reference archive contained all 22 expected members with their recorded digests. [M2 log](../M2/development-log.zip) · [M3 project](../M3/project-ledger.zip) · [M3 control](../M3/kernel-trace.zip) · [M4 reference](observer/independent-reference-v1.zip)

The E1 reporter reproduced its 7,398-byte report from the retained archive on the joined product. The E2 reporter enforces its registered source identity. The registered checkout `c7459fa` reproduced all 416,039 report bytes, and its input archive matched the copy in the M4 checkout. [E1 report](../../../experiments/results/e1-scripted-v2/report.md) · [E2 report](../../../experiments/results/e2-scripted-v1/report.md)

## Kernel size decision

The product has **3,355 physical Python lines** across 25 files, including blanks and comments. Of these, 397 implement the separately qualified physical Git adapter from `b781dcb`. Those files own local Git object and reference operations, process fencing and external observation. The SQLite gate still decides authority and settlement. The independent component review measured five real Git case families and the ten component tests. The kernel, CLI, driver and the new foreground integration together account for **2,958 lines** under the M0 definition. All foreground integration lines count toward that total, including the code in `purser/effects/foreground.py`.

The architect retains the two existing ownership boundaries. Combining Git process management with semantic admission would place external I/O inside the ledger’s responsibility. Both totals will be reported again at the next product gate. The working target remains 3,000 kernel lines. [M0 counting scope](../../delivery.md#operating-envelope) · [Design decision D-10](../../architecture-v0.5.md)
