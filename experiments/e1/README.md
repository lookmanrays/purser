# Scripted E1 notification experiment

The [preregistered protocol](../manifests/v1/protocol.md) defines the primary comparison, control and scoring rules. Every run starts in a fresh Git workspace. The worker edits preferences, delivery and retries in that order. It tests its local edits and submits an immutable Git commit. The plain plan rechecks the sealed commit with the worker's public smoke test and checks the expected path. At integration it checks scope, compatibility and a two-attempt delivery example. A diagnosed defect gets one repair attempt. A verifier-equipped plan receives the same extra fixed-probe results that Purser receives through independent observer/verifier bindings. These two plans persist their decisions in ordinary JSON. Purser uses its normal scoped assignments, ContextPacks, retained observation bytes, verdicts and closure proposals. Its public reasons identify missing criteria.

The protected scorer measures the independently archived candidate using the frozen seed-specific inputs after the trial clock stops. The operational verifier has its own fixed cases. The scorer's seeded results never enter the treatment. All candidates are trusted scripted fixture outputs in M2. The preregistered M5 evaluation boundary applies before importing any real model-authored candidate.

Run with Python 3.11 or newer from the repository root. Commit the study source first, then commit the generated freeze before collecting. A correction gets a separate directory, replicate number and reason. The following commands illustrate a new replication using an unused number and directory.

```shell
python3.11 -m experiments.e1 freeze experiments/results/e1-scripted-v3 --replicate 3 --reason "Independent replication on the same registered fixture"
git add experiments/results/e1-scripted-v3/freeze.json
git commit -m "Preregister the independent E1 replication"
python3.11 -m experiments.e1 collect experiments/results/e1-scripted-v3
python3.11 -m experiments.e1 report experiments/results/e1-scripted-v3
python3.11 -m experiments.e1 verify experiments/results/e1-scripted-v3
```

The ZIP retains raw run records, journals, observer reports, full Git packs and the arm's own history. Paths in artifact references are relative to the archive root. Identical content shares an object. The clean control uses seed 1001 with a distinct track. The report recomputes E1 outcomes and verifies stored artifact identities before aggregation. The verify command restores every distinct Git pack in a private repository, checks its tree and parents, and reruns the seed-specific protected checks on those archived bytes. Collection refuses an uncommitted or changed freeze and preserves the identity of every started attempt.

Rerun `collect` on the same directory after interruption. The collector reads each completed row from its published archive. For an interrupted case it reads the durable action journal and the participant's surviving public state. It records a harness failure with the steps and time it can establish, then moves on to the next scheduled case. Publication replaces the whole ZIP atomically. The directory lock serializes collectors on the supported local filesystem.

To reproduce the original v1 report under its original scorer, use an isolated checkout of `bb36a127fa0c534a15f522594efdbbe6d5f9717a` and run `python3.11 -m experiments.e1 report experiments/results/e1-scripted-v1` there. The [measurement review](../../docs/evidence/M2/e1-measurement-review.md) records the reasons for the separate replication.
