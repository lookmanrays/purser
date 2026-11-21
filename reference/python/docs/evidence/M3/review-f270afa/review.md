# Independent E2 precollection review

**NO-GO for freezing or collecting the official E2 series on the reviewed evaluator.** Two P1 findings affect measured outcomes and clean cost conclusions. One P2 finding drops obtainable measures during recovery.

The evaluation source is f270afa2758dafe91791a4302caebbfd263ce719. I tested the joined product reference 9c9f67f423c39ace3797954bd36358680c14ce14 in a private clone. The experiments/e2 tree is 13a957850711f104822077bca67b30f9812eadf9 at both commits. The full experiments directory has zero differences between those references. All review commits only register private controls. Product code and shared checkouts were untouched. The M0 protocol §§47–48, 57–73 and 75–90, M3 dispatch and architecture define the measurement. The independent program estimate.py uses only the Python standard library to compare published rank-one queue observations to the required timings, IDs, channel, order and multiplicities, check six final results, enumerate charged work and audit original admitted kernel batches. The public verify command separately restored and executed Git candidates.

## Findings

**P1. A compound public work operation can hide a genuine reopening, invalid W2 closure and eligible rework.** The folds at experiments/e2/outcomes.py:74 and experiments/e1/accounting.py:224 inspect selected before/after revisions. The kernel replay traverses the original log but evaluates public states at the revisions referenced by the experiment. A single registered journal call can contain multiple independently committed operations through the existing scoped participant ports. The protocol expressly requires compound calls to expand to their public work actions and counts even a temporary reopening.

* The Purser control left W2 adoption genuine. Six additional real scoped W3 work operations were committed within that public call. Separate SQLite connections observed W3 closed at revision 60, assigned at 62, submitted at 64 and closed again at 72 on its original Git commit. The W2 adoption receipt itself ended at revision 60. Its purported action endpoint was revision 72. Original proposal IDs P-24 through P-29 and their six admitted receipts survive in the ZIP. The independent estimator counted at least 31 post-input actions, and the induced trajectory has 54 total. The public result says retained=true, reopened=0, rework=25 and steps=48. Strict verification exited zero.
* The plain-plan control admitted the changed requirement, then genuinely declared old W2 commit 7df4a199e2ada1caafe78f5acec2f99b06f44771 closed and resubmitted it before returning from one planning operation. A second OS process observed the old candidate submitted, closed and submitted with the new observation ID at every read. The independently graded old candidate fails the revised retry criterion. The recovered latest candidate passes. The raw result nevertheless says affected_invalid_closure=false and rework=13, omitting the two intervening public operations. Strict verification exited zero.
* A content-addressed archive control replaced the actual W2 closure response receipt at action 34, proposal P-29, with the earlier W3 receipt P-20. The canonical kernel receipt, correctly hashed outer response identity and actual W2 close remain available. Both public readers accepted the contradictory receipt without a dispute. The actual closure can be retained independently. Its returned identity requires reconciliation.

The relevant measurement is every committed public transition and eligible invocation between the admitted premise observation and the verified end. Both public plans and the kernel need coverage appropriate to their respective backing stores. Original SQLite transaction batches establish intermediate states even when the same candidate and state reappear. An ambiguous interval cannot support an affirmative continuous-retention claim or an exact work total.

**P1. A disputed or unsupported clean case can determine numeric healthy overhead.** experiments/e2/report.py:81 reports an intervention in the clean track but the eligibility calculation at line 175 omits the no-intervention condition. In a real nominally clean plain-loop control the genuine new A1 observation was delivered before the participant returned done. A fresh plan read records the exponential premise. The output simultaneously reports an unregistered intervention and a clean 24-step loop with an overhead flag of **no** against a healthy 42-step Purser. Strict verification correctly rejects this archive. The normal report still publishes the numeric conclusion.

A second control physically removes the clean Purser original-event/final snapshot artifact and its manifest reference. The reader falls back to checkpoint projections without replaying that run's kernel history. Both report and strict verify accept all nine cases and assert the same healthy overhead. This has direct bearing on the demonstrated return-to-same-state trajectory above. Unrelated raw costs and independent final behavioral observations can remain available. A numerical healthy-pair conclusion requires a genuinely clean trajectory.

**P2. Recoverable sealed candidate evidence is dropped after loss of the redundant trial bare directory.** A private process received SIGKILL immediately after the independently observed correct loop parent closure. I made only trial/private-objects unavailable before normal resume. The worker's clean HEAD, plan, durable journal and all five content-addressed Git packs survived. experiments/e2/trial.py:74 and experiments/e2/measure.py:33 attempted recovery from the unavailable live bare path alone. The reader had zero protected observations and rendered every outcome unknown. Strict verify restored five packs and reran zero seed/candidate/version groups. Restoring the very same final pack at a5cd7dfec079a2b38d8b673c47d736ce3161f22a independently reproduced all six passes. The interrupted timer remains a lower bound. The final candidate behavior is recoverable independently of that timer.

The separately reported pre-injection checkpoint finding concerns withholding actual old W2 bytes and sibling qualification. The cases above use an independently passing old checkpoint and examine different authority boundaries.

## Honest controls

Nine normal rank-one runs were collected in private replicate 87. The separately registered compound, clean-intervention, closure and process-death controls use private replicates 88–91. The 66 official runs were outside this review.

| Track | Plain total / rework | Purser total / rework | Equal-verifier total / rework | Independent final outcomes |
| --- | ---: | ---: | ---: | --- |
| Primary | 30 / 13 | 48 / 25 | 48 / 25 | All six pass in each arm. |
| Clean | 24 / inapplicable | 42 / inapplicable | 42 / inapplicable | All six pass under A1 in each arm. |
| Closed-affected stress | 32 / 13 | 53 / 25 | 53 / 25 | All six pass in each arm. The two plans visibly retained stale W2 at delivery. The kernel cleared it. |

The honest baseline used only W2 rework and parent integration. The additional operational verification explains the equal-verifier and Purser counts. Strict verification on the nine original controls restored five packs and reexecuted eight distinct seed/candidate/version groups.

## Reproduction

The packet contains seven raw archives, every private freeze in private-registrations.bundle, the independent scores and cold witnesses, and the short programs that generated the counterfactuals. It contains no official study data. Extract into a private directory containing no prior review runs. Clone the project there as repo, fetch the included bundle's HEAD into that clone and check out FETCH_HEAD. Its prerequisite is 9c9f67f423c39ace3797954bd36358680c14ce14. All five private source pins then resolve. Run from this clone with LC_ALL=C, TZ=UTC and python3.11 -B.

~~~sh
python3.11 -B -m experiments.e2 report ../cases/kernel-transient
python3.11 -B -m experiments.e2 verify ../cases/kernel-transient
python3.11 -B -m experiments.e2 report ../cases/bad-closure
python3.11 -B -m experiments.e2 verify ../cases/bad-closure
python3.11 -B -m experiments.e2 report ../cases/clean-intervention
python3.11 -B -m experiments.e2 verify ../cases/clean-intervention
python3.11 -B -m experiments.e2 verify ../cases/missing-kernel-trace
python3.11 -B -m experiments.e2 verify ../cases/wrong-closure-receipt
python3.11 -B -m experiments.e2 verify ../cases/pack-only
python3.11 -B ../estimate.py ../cases/healthy/raw.zip ../cases/kernel-transient/raw.zip ../cases/bad-closure/raw.zip
~~~

The clean-intervention strict command is the intentionally rejecting case. See cli-results.json for all command results. To regenerate live counterfactuals in another unused disposable clone containing the bundled freezes, run transient.py, bad_closure.py or clean_intervention.py from the packet. For the process-death case expect pack_recovery.py die to terminate with SIGKILL, then run pack_recovery.py recover. The archive mutation program expects a copy of the healthy raw packet at repo/private-assurance/raw.zip.
