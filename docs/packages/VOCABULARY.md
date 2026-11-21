# Vocabulary: design names and kernel names

The design doc speaks in engineering terms; the kernel keeps purser-main's v1 names so its frozen vectors still apply. This table is the bridge. Agents use kernel names in code and design names in prose.

| Design term | Kernel v1 term | Notes |
| --- | --- | --- |
| Task (from tam) | Commitment with its root WorkItem | Activated by principal approval. |
| WorkNode | WorkItem, plus a `spec` object set by `work.spec` | `spec` is a v2 extension added in P1-04. |
| TestNode | Obligation (criterion) at a version | Its definition and environment digests pin the test. |
| Evidence | Observation plus Evidence (verdict) on a VerificationRun | Only the current run on the current result counts. |
| Result / candidate | Result with an immutable candidate identity | Git commit or SHA-256 artifact. |
| Decision | Decision supported by Assumptions | Triggers name the assumptions whose contradiction forces reconsideration. |
| Contradiction (worker escalation) | v2 record `Contradiction` (P4-10) | Distinct from a premise contradiction, which is `assumption.observe`. |
| Approval | Approval (pending, approved, denied, expired, stale) | Principal-only answers. |
| Effect | Effect, AuthorityGrant and EffectAttempt | Governed Git operations. |
| Contract | v2 record `Contract` (P3-03) | Digest of the exact contract file. |
| System graph snapshot | v2 record `GraphSnapshot` (P2-08) | The graph itself is a derived index outside the log. |
| Routing decision, cost record | v2 records `Routing`, `Cost` (P5) | |
| Frontier | q10 answers plus `purser frontier` | Permitted next actions. |
