# Scripted E1 notification experiment

The [preregistered protocol](../manifests/v1/protocol.md) defines the primary comparison, control and scoring rules. Every run starts in a fresh Git workspace. The worker edits preferences, delivery and retries in that order. It tests its local edits and submits an immutable Git commit. The plain plan rechecks the sealed commit with the worker's public smoke test and checks the expected path. At integration it checks scope, compatibility and a two-attempt delivery example. A diagnosed defect gets one repair attempt. A verifier-equipped plan receives the same extra fixed-probe results that Purser receives through independent observer/verifier bindings. These two plans persist their decisions in ordinary JSON. Purser uses its normal scoped assignments, ContextPacks, retained observation bytes, verdicts and closure proposals. Its public reasons identify missing criteria.

The protected scorer measures the independently archived candidate using the frozen seed-specific inputs after the trial clock stops. The operational verifier has its own fixed cases. The scorer's seeded results never enter the treatment. All candidates are trusted scripted fixture outputs in M2. The preregistered M5 evaluation boundary applies before importing any real model-authored candidate.

Run these commands from the repository root. Commit the code and then the generated freeze before collection.

```shell
python -m experiments.e1 freeze experiments/results/e1-scripted-v1
python -m experiments.e1 collect experiments/results/e1-scripted-v1
python -m experiments.e1 report experiments/results/e1-scripted-v1
python -m experiments.e1 verify experiments/results/e1-scripted-v1
```

The ZIP retains raw run records, journals, observer reports, full Git packs and the arm's own history. Paths in artifact references are relative to the archive root. Identical content shares an object. The clean control uses seed 1001 with a distinct track. The report recomputes E1 outcomes and verifies stored artifact identities before aggregation. The verify command restores every distinct Git pack in a private repository, checks its tree and parents, and reruns the seed-specific protected checks on those archived bytes. Collection refuses an uncommitted or changed freeze and preserves the identity of every started attempt.
