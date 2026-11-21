# M2 E1 evaluator qualification

The candidate is `e3a155633058cd20274ce5e9cfaca224b41f5b5b`, based on recovery checkpoint `163badfb67311ad6049b41faacd2a7f1a029d18b`. Its pre-implementation assessment and independently challenged design are at `853c3a76a1d168017f0fc38f46bb6564984a74e6`. The experiment still uses v1 manifest subtree `f1d8dd89981771249389b0b713aa389eca98c1fd`. The product subtree matches the independently reviewed M2 implementation at `70097e0`.

## Implementation owner's controls

On Darwin arm64 with Git 2.50.1, both Python 3.11.15 and 3.14.3 passed 107 repository tests and three contract vectors at the candidate. Commands were `python -B -m unittest discover` and `python -B docs/contracts/check_vectors.py`, with UTC and the C locale.

| Actual control | Observed result |
| --- | --- |
| Original published series at its frozen reader | The offline report reproduced SHA-256 `43c0b17ea308f914fdafd311c9cbb2eaa4c6ce7faf0dce61d8aacd7396009658`. The original raw SHA-256 is `6f20110ba734ecdefb568147e2d3fcc41d541cef35ee8bec70978c9aed89c4da`. |
| Historical interrupted plain loop | The restored `done` parent on `627813279b9b9112fd3455901f9851c84f4736a8` has a failing scope criterion, a named target, zero extras and a disproved catch. Cost remains a lower bound of 24 steps. Strict verification restored seven packs and reexecuted six seed/candidate pairs across two cases. |
| Historical contradictory clean cutoff | The reader preserved both healthy neighbors and reported an inconclusive clean comparison. Strict verification identified the clean loop's contradictory terminal and budget reasons. |
| W1 outside-scope fault carried into a W3 Git submission | The actual first W3 self-review named W3/delivery. A later parent diagnosis named scope/paths. The registered fields were false closure false, named false, caught false, correct closure true and one extra diagnosis. |
| Ineffective parent repair followed by a `done` declaration | The verifier-equipped plan retained its outside-scope file. Its bad final parent gave false closure true, named true, caught false, correct closure false and zero extras. The fully measured adverse primary remained eligible. |
| Healthy clean controls | Plain loop 24 steps, Purser 42, equal-verifier loop 42. All three reached an independently passing final parent. |

The six-case private replicate 81 used real participant stores, Git candidates and the normal archive writer. The archive-only reporter reproduced the original private report byte for byte. Strict Git verification restored eight distinct packs and reexecuted 11 seed/candidate pairs. The public-response overrides for the two falsifying controls are visible in the [control program](participant_controls.py). The [control archive](participant-controls.zip) has SHA-256 `3b1fe055a8782d676b1f3b74a8bace2965e9a80b641b85ff8bc7982377345177`. Its [recorded results](participant-controls.json) include report SHA-256 `18968cc714407a1cf8e714d6edbb295b6d12c6d255f1cfc508063032aed206ab`.

To read this private archive, copy it into a temporary directory as `raw.zip`. In a checkout of the evaluator commit run `python3.11 -B -m experiments.e1 report DIRECTORY` and `python3.11 -B -m experiments.e1 verify DIRECTORY`. To repeat the six participant controls, run the control program with that checkout as the current directory and PYTHONPATH, the same locale, and an empty temporary directory as its argument. Fresh attempts have their own timestamps and elapsed costs.

## Independent review decision

**PASS** on `e3a155633058cd20274ce5e9cfaca224b41f5b5b` at the trusted local scripted evaluator boundary. A separate reviewer defined its controls from the frozen protocol before receiving the replacement SHA, then reviewed the diff and exercised an isolated checkout. It recorded zero P1, P2 or P3 findings.

Nineteen fresh private participant trials included ten real process kills on either side of Plan publication, SQLite closure refusal or verifier admission, including repeated same-candidate decisions. Two resumes per interruption yielded one participant start. The independent queue/Git oracle agreed with 234 archived criterion verdicts. Both offline commands accepted all 15 coherent archives. Strict verification rejected all six independently damaged endpoint archives, while the reporter retained healthy neighbors. The historical controls reproduced and 24 selected candidate tests passed.

The reviewer used private replicate 83. The [independent bundle](independent-controls.zip) contains its original control plan, source, reproduction commands, committed private registration, raw archives, stage witnesses and checksums. Its SHA-256 is `c793e2838155f5346a22ac7d43865d9f00f31d50d896c48e84985c2ddd43eb51`. The [case-by-case review](independent-qualification.json) has SHA-256 `232d6e2f56ee937b4f09c8c7585913cff35407253c1c4025fabbd7a38c24c33c`.

## Registered replication

Local registration commit `0338c47968ad757e4b70dcc9fcd92e6cd5ae6247` contains only the [replicate 2 freeze](../../../../experiments/results/e1-scripted-v2/freeze.json). The freeze names the reviewed implementation, all three treatments, controller, scorer, executor and fault driver. The registration was committed at 17:28:13 UTC on 18 November 2026. Participant clocks ran between 17:28:19.120870 and 17:30:43.543931 UTC. All 60 primary cases and three clean controls have one start and one final row in the registered order. Every participant reached a measured `done` exit. Model calls, tokens and spend were zero.

| Scripted arm | False closures | Target named | Caught | Correct closures | Extra diagnoses | Primary steps, total | Primary elapsed s, total | Clean steps |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Plain loop | 0/20 | 20/20 | 20/20 | 20/20 | 0 | 620 | 21.852 | 24 |
| Purser | 0/20 | 20/20 | 20/20 | 20/20 | 0 | 1,082 | 38.283 | 42 |
| Equal-verifier loop | 0/20 | 20/20 | 20/20 | 20/20 | 0 | 1,082 | 28.065 | 42 |

Both registered thresholds were met. The plain loop caught every fault, so E1 found no catch-rate advantage for Purser. Across primary seeds Purser used 462 more steps than the plain loop and the same number as the equal-verifier loop. All clean controls closed correctly. Purser's 1.75× clean step count was within the 2× limit. The [report](../../../../experiments/results/e1-scripted-v2/report.md) gives the paired differences for each seed.

| Artifact | SHA-256 |
| --- | --- |
| [Registration](../../../../experiments/results/e1-scripted-v2/freeze.json) | `7d3f4631461b684d37b9839402a33f4c8f078662364bcbdc48c34bcab890d1a5` |
| [Registered raw archive](../../../../experiments/results/e1-scripted-v2/raw.zip) | `ddee5e3efa0360a10e56d0a947221aa7238a712fd34c155145f4bf62546eeb27` |
| [Regenerated report](../../../../experiments/results/e1-scripted-v2/report.md) | `0a82c9a1bbc7addcb1789adf3aa403ab65dfcd4b858d28811bfdcb87faa1fd21` |

Strict offline verification with Python 3.11 restored 34 distinct Git packs and reexecuted the protected checks for 77 seed/candidate pairs across all 63 cases. Python 3.14 regenerated the same report from a directory containing only the raw archive. A direct read of the start records, exits, action counts, first diagnosed labels, parent transitions and protected verdicts independently matched all five published E1 fields for each primary. The original series retains its original registration, bytes and report.

The architect owns integration and M2-G. The separate human-accepted CLI feature is preserved at revision 24 on the architect's branch in `066641a3ead3aade78f0af786ed1ee1636daa334`.

The independent reviewer also read the registered archive after collection. Its own queue, scope and immutable-test checks covered 95 seed/candidate combinations across 34 Git commits and agreed with all 1,422 archived criterion verdicts. Each first diagnosis named its target on a still-failing submitted candidate before repair, and all 63 final parent closures matched passing candidate bytes. Both paired comparison columns agreed for every seed. The [per-case readback](replicate-2-independent-readback.json) has SHA-256 `349c14df854e5e8ad57d1fbae819cb20ac298c11e94b881e63bd0656e433283f`. The [reviewer's readback program](series_readback.py) uses the witness implementation and frozen v1 fixture in the independent bundle's reproduction layout. It dispatched zero study arms. No material discrepancy remained.
