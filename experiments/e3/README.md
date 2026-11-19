# Scripted E3 · recovery of a local Git effect

Each participant receives the registered notification commit and one permission to publish it to `refs/heads/notify` in a fresh local repository. The three arms share the Git broker and observer. The treatment binds, authorizes and reconciles through Purser. Both loops keep a plain submitted intent and public decisions. The original loop can explicitly inspect any timeout. The equipped loop receives the automatic Git observation at the same point as Purser.

The collector runs a physical child process per dispatch. The first odd-ranked writer exits after the Git transaction. The first even-ranked writer exits immediately after authorization. The supervisor reaps each and releases the same timeout. In the inconclusive control the original child still holds the exclusive repository guard when the participant declares its pending disposition. The end-clock and final native refs are captured before the supervisor ends that child.

| Public action | What crosses the boundary |
| --- | --- |
| Context | Read the assigned task, candidate, branch and scoped permission. |
| Submit | Bind the logical intent to the prepared candidate. |
| Effect | Release a fresh physical attempt. A lost response still costs one invocation. |
| Reconcile | Read the named attempt through the Git observer or the treatment's observer admission. |
| Decide | Publish the attempt disposition, its source action and the next action. A retry follows publication of the original disposition. |

The clean loop takes four calls and the equipped arms five. An ambiguous landed attempt takes five calls in every arm. A known unlanded attempt adds a new dispatch and decision, plus a second automatic observation for equipped arms. The 96-step and 60-second budgets cover the released participant. Source capture preserves attempted calls, missing replies and incomplete cases. Candidate preparation precedes release and independent scoring follows the measured exit. The platform records the start and end of both periods and the exact platform artifact supplied to the first offline scorer.

Use Python 3.11 or later and a local Git with reftable support. To verify or render the published v1 archive, use a disposable clone at results commit `79e963b0db70153b29fc9c7dbeed54bead32bcee`. The contributor guide gives the commands and compares the generated report with the published Markdown. [Reproduce the published studies](../../CONTRIBUTING.md#reproduce-the-published-studies)

To run a new replication, use a development clone with committed study source. Choose an unused directory and replicate number and record the reason. The example below uses replicate 3. The source freeze includes the runner, scorer, treatments, manifest identities and ordered schedule. Commit the new registration before collection. Re-entering collection on that series resumes its original attempts.

```sh
series=experiments/results/e3-scripted-v3
python3.11 -m experiments.e3 freeze "$series" --replicate 3 --reason "Independent replication of the registered Git recovery study"
git add -- "$series/freeze.json"
git commit -m "Preregister the independent E3 replication" -- "$series/freeze.json"
python3.11 -m experiments.e3 collect "$series"
python3.11 -m experiments.e3 verify "$series"
python3.11 -m experiments.e3 report "$series"
```

In the v1 raw archive each case names its journal, Git pack mapping and platform manifest. The platform's `e3` member retains the scoped intent, repository binding, caller requests and replies, public disposition declarations, native refs and reflogs, per-dispatch child and supervisor events, and Purser's original event bytes and observer artifacts. Native Git reads and writer status are captured independently of the participant's report. A separately identified post-interruption read and the stress writer's teardown describe later evidence. [Preregistered protocol](../manifests/v1/protocol.md)
