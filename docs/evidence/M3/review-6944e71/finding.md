# M3 early product review · Changed work support

The architect withheld M3 product acceptance for joined candidate `6944e7112ba84124e2032244da518e416930c6bd`. Its product tree is `c8cd54680aa77651fc1e5b67172d91396379678a`. This control used the public gate and real SQLite on Python 3.11.15. It used four synthetic work items and wrote three different candidate payloads before the intervention. The [original trace](trace.json) has SHA-256 `77ad215183290df8468c806fde9b3fb0e63db104d4625756c271c4854efb6ac3`.

| Revision | Observed state |
| --- | --- |
| 67 | W9 implements the linear retry rule. W-aggregate closes on W9. W-consumer depends directly on W-aggregate. Its generation 1 passes and closes. W-unrelated also closes. |
| After the premise changes | W9 becomes stale and the aggregate opens. W-consumer opens and temporarily loses its qualifying evidence. |
| 87 | W9 has a new candidate, generation and evidence. W-aggregate closes on the revised exponential behavior. Its outcome changes from `6fb04313…` to `f6c5f1ba…`. The frontier offers to close W-consumer with the original generation-1 evidence. |
| 89 | The gate accepts W-consumer’s original outcome, `313f17ee…`. W-consumer had no fresh work, observation or verdict. W-unrelated kept its original outcome throughout. |

The issue is the identity of an explicitly declared work dependency. A consumer that used the old aggregate result can close against the replacement using its earlier pass. The distinction matters when the parent’s selected evidence changes. Merely sharing a parent supplies no dependency. An unaffected sibling or a parent that still qualifies on the same witnesses should preserve its evidence.

To reproduce, copy [the original control](reproduce.py) into a disposable directory, check out the candidate, and run `PYTHONPATH="$PWD" python3.11 -B /path/to/copy/reproduce.py` from that checkout. The script writes a fresh trace beside its own file. It uses the repository test fixture to supply authenticated local roles and deterministic inputs. This control exercises the kernel independently of the registered E2 experiment. The technical owner received the evidence before trial registration.

The architect reran the identical control on joined product revision `69f193586de83a7c4eb72974f2038e3ba72d4c5e`. The aggregate changed to the same new outcome as before. W-consumer became stale, its criterion remained open, the frontier offered rework and the attempted old closure was rejected. W-unrelated retained its original outcome. The [replacement trace](replay-69f1935.json) has SHA-256 `9116acfa9071bd6e2365889cc56981428455ba00a3bb1b6e7a0fbc316c92ec4e`.
