# Contracts

Everything under `contracts/` is frozen once you approve it. Agents never edit it; a change is a decision with its own approval and marks dependents stale.

| Path | What | Becomes final in |
| --- | --- | --- |
| `format/contract-v1.draft.md` | The contract file format | P0-04 |
| `records/records.draft.yaml` | Record kinds and fields | P0-03 |
| `kernel/*.yaml` | Interface contracts for the kernel packages | Approved in the P0 to P2 planning batch |
| `graph/*.yaml` | Interface contracts for the system graph packages | Approved in the P0 to P2 planning batch |
| `kernel/ops-graph.yaml` | The v2 `graph.snapshot` operator | Approved in the P0 to P2 planning batch |
| `model/gateway.yaml`, `plan/contextpack.yaml`, `plan/bundle.yaml`, `plan/validator.yaml`, `iface/mcp.yaml` | Boundary contracts between phases P3 to P7 | Detailed and approved in each phase's planning batch |
| `cli/` | CLI reply schemas and exit codes | P0-05 |

Other contract paths named by P3 to P7 briefs do not exist yet; each phase's planning batch writes them before any code in that phase.

Behaviour specifications that purser-main already froze are not restated. Kernel contracts point to them under `reference/python/docs/contracts/` and to the vectors under `testdata/vectors/purser-v1/`. Where a Go contract and a reference spec disagree, stop and escalate.
