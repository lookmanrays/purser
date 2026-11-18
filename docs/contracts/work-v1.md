# Work, evidence and closure v1

M2 extends the M1 proposal, event, receipt and snapshot formats. Every operation below goes through `purser.kernel.gate.admit`. Its envelope names the commitment and a complete base revision. Row IDs are unique across the workspace. A role or proof supplied as proposal JSON receives no authority.

## Operators

| Operation | Fields after `op` | Authorized caller and effect |
| --- | --- | --- |
| `work.create` | `id,parent,title` | Principal or scoped planner creates a child. A current executor may add a direct child under its assigned work. |
| `work.coverage` | `id,kind,k` | Principal or planner declares `all`, `any` or `k_of_n`. Use null for k with all/any. A positive k must fit the current child set. |
| `work.assign` | `id,work,executor` | Principal or planner creates an assignment. A fresh assignment ID supersedes the old assignment, advances the generation and clears the previous result and candidate. |
| `result.submit` | `id,assignment,candidate` | Bound current executor submits one immutable candidate for this assignment. Replacing a candidate requires another assignment and generation. |
| `obligation.set` | `id,work,version,title,observer,verifier,definition_sha256,environment_sha256` | Principal enrolls a criterion at version 1 or replaces its definition at the next version. Independently pinned verifier ingress can enroll version 1 for its named work and obligation. |
| `run.open` | `id,obligation,result` | Principal or planner opens a fresh run on the current result and criterion. The run freezes all actor, candidate, assignment, generation, definition and environment identities. |
| `observation.record` | `id,run` | The named run's trusted observer supplies measured candidate and evidence bytes on its binding. A historical observation still belongs to the original run. |
| `evidence.record` | `id,run,observation,verdict` | The named run's verifier reads the retained observation. Verdict is `pass`, `fail` or `inconclusive`. The result producer cannot be this run's observer or verifier. |
| `obligation.waive` | `id,obligation,version,result,reason` | Principal explicitly waives one criterion version for one current result/generation/candidate, retaining the reason. |
| `work.close` | `id` | Scoped closure evaluator requests a fresh gate evaluation for ordinary work. A blocked request returns `closure_blocked` with work ID and typed reasons. |
| `work.accept` | `id,outcome` | Principal accepts the commitment's root by its current SHA-256 outcome digest. The same transaction closes the root and satisfies the commitment. |

Planner and closure roles bind to a commitment with a null assignment. Executors bind to assignment IDs. Observer and verifier bind to the run ID in the M1 binding's `assignment` slot. Trusted verifier enrollment instead binds to the new obligation ID with a `criterion` object containing `work,observer,definition,environment`. Definition and environment are raw bytes. The gate hashes both and checks the proposed digests, independently named observer, authorized verifier, work, empty slot and initial version. The CLI has only its local principal route.

For example, after the planner admits `{"op":"run.open","id":"V-retry","obligation":"O-retry","result":"R-retry"}`, the observer calls through the same proposal envelope with `{"op":"observation.record","id":"Obs-retry","run":"V-retry"}`. Its trusted binding uses the criterion's observer identity, role `observer`, the commitment, assignment `V-retry` and the standard local principal ID. It also supplies `witness={"candidate": measured_candidate,"definition": harness_bytes,"environment": environment_bytes,"artifact": report_bytes}`. Each byte field has an 8 MiB limit. Candidate IDs name a SHA-1/SHA-256 Git commit or `artifact.bytes` with SHA-256. The independent launcher measures the Git object it actually observed. Fixture runners execute the pinned definition against that object. A submitted Git identity alone can never establish a pass.

The content store adjacent to the database retains exact bytes under their SHA-256 names. `Artifacts(db_path).read(hex_digest)` verifies their identity on each read. Publication stages immutable bytes before admitting a reference. An interrupted or rejected batch can leave an unreferenced blob. Replay interprets recorded facts without running a verifier. Admission of evidence, closure and root acceptance verifies the relevant retained bytes through the gate.

## Outcomes and reads

`purser.kernel.workview.WorkView(projection)` exposes `records`, `basis(work_id)`, `outcome(work_id)`, `closed(work_id)`, `reasons(record_id)` and `answers()`. The latter returns q02, q07, q08, q09 and q10 in M0 ordering. q07 states are open, passed, waived, failed, inconclusive and conflict. Positive witnesses identify the qualifying evidence or waiver. q10 emits `assign` for assignable work and `execute` for an existing assignment waiting for a result.

A leaf needs a current candidate and at least one explicit criterion. A structural parent requires its declared coverage, qualifying closed children and all its own criteria. When a parent has its own tests, assign and submit its aggregate candidate. A parent with only child coverage can have a null candidate. Work uses the [frozen outcome basis](lifecycle-v1.json). Positive verdicts must belong to the current run on the current result and criterion. A fail or inconclusive result cannot be redeemed by another verdict in the same run. Mixed verdicts conflict. Open a fresh run or use a scoped principal waiver. Reassignment, changed requirements or changed evidence can reopen previously closed work and its ancestors in the same admitted batch. A satisfied commitment whose basis changes returns to active and requires fresh acceptance.

Creation reads the new slot and its parent. Assignment and submission read the work and bound assignment. Opening a run reads the current criterion, result and work. Observation and evidence address an immutable run and named observation. Waivers read the current result and criterion. Coverage reads child membership. Closure and acceptance read the reachable child and criterion sets, current assignments, results, waivers, runs and contributing observations/evidence. New children or criteria inserted after a closure proposal's base can stale it. Unrelated sibling mutations leave a leaf's own basis intact.
