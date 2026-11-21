# M3 local driver release

During package integration the two-process [cutoff control](precollection-controls/duplicate_dispatch.py) failed on the initial mutable M3-A implementation based on `c04d1d399005fdd7f2efbffd14f7a824ee3c250c`. Its driver blob was `8d7cf87965c3d7a1bd07805c11e992d428fbe6a3`. The gate and work-operator blobs were `54acfaba18ff3223097a7aea47b23d1153b7b2e2` and `261ced73c6865fa225d279bc4330cb40bd447884`.

Two independent driver processes took the same initial snapshot. The first received a release receipt and entered its callback. A separately bound observer then invalidated A1 at complete revision 19. The second driver started after that commit from its paused snapshot. It reused the first release receipt and called its own executor. Both results were rejected with `stale_base` at revision 20. The observed second callback violated the M3 cutoff.

| Remediation register | Finding before the first M3 source candidate |
| --- | --- |
| Cluster and severity | M3-local-callback-release, P2. A physically new local callback could start on support already invalidated. |
| Invariant | Each new local callback must obtain its own current admission before release. A callback ordered before invalidation may finish, and its late result is fenced. An authenticated duplicate gate request retains its original receipt. |
| Authority and structural membership | The registered `assignment.dispatch` operator and the sole normal callback invocation in `purser.driver.tick`. Inspect the dispatch request identity, authenticated receipt replay and downstream result submission. Ordinary closure is a transactional state transition. A cli `run` with no configured executor performs no callback. The E2 fixture submits through public gates and does not provide an independent product release path. |
| Root cause | A request identity derived solely from subject, generation, action and initial revision can represent two distinct physical calls. An admitted receipt is durable historical evidence. An individual local invocation needs current release authority. |
| Disposition before implementation | First substantive finding in this cluster. No earlier M3 remedy or Reset. The historical E1 observation/accounting clusters concern measurement and archival population, with different owners and causal mechanisms. This finding uses no Reset. A local fix fits the existing gate and single-machine operating boundary. |
| Planned acceptance | Repeat the two-process control, both invalidation/close orders, invalidation before any release, and an in-flight callback returning afterward. A separate reviewer will repeat these on the immutable joined candidate. |

From the joined checkout run `PYTHONPATH=. python3.11 -B docs/evidence/M3/precollection-controls/duplicate_dispatch.py`. The two marked program boundaries only delay the return of a real SQLite snapshot and a real local callback. The gate, clock ordering, result submissions and store are the normal product paths.
