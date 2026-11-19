# Purser

Purser is a local research kernel for coding work that survives a change of agent or a process restart. It tracks commitments, scoped authority, observations and the evidence needed to close work.

From a local checkout you can track a piece of work, inspect what evidence would close it, and recover an interrupted update to a dedicated local Git branch. M0–M4 have passed their local checks. The M5 study software is available, with 0 of 195 genuine model trials collected. Connecting a real coding agent through MCP is planned for M6. [Current roadmap and evidence](docs/delivery.md#current-implementation)

The original architecture and implementation plan describe the intended system. The delivery record tracks what has been built. Decision D-30 makes remote delivery of approvals optional. [Architecture v0.5](docs/architecture-v0.5.md) · [Original implementation plan](docs/implementation-plan-v0.5.md) · [Approval decision](docs/decisions/0030-principal-approval.md) · [Contributor setup and tests](CONTRIBUTING.md)

## Try the local CLI

Run these commands from the repository root in a POSIX shell with Python 3.11 or newer. The local CLI uses Python’s standard library. The example creates a private temporary directory for its SQLite ledger.

```sh
workspace=$(mktemp -d)
python3.11 -m purser --db "$workspace/purser.db" init
python3.11 -m purser --db "$workspace/purser.db" propose - <<'JSON'
{
  "v": 1,
  "proposal_id": "p-create",
  "commitment": "C-retries",
  "base_revision": 0,
  "ops": [
    {"op": "commitment.create", "id": "C-retries", "root": "W-root", "statement": "Add bounded notification retries."}
  ],
  "justification": []
}
JSON
python3.11 -m purser --db "$workspace/purser.db" why C-retries
python3.11 -m purser --db "$workspace/purser.db" replay
```

The creation receipt records revision 2. `why` shows the commitment in the proposed state. Sending the identical proposal again returns its existing receipt. The wire contract describes activation by the local principal, historical reads and JSON exit codes. [Wire contract](docs/contracts/kernel-v1.md)

For an active commitment, `frontier` lists permitted next actions. `context ASSIGNMENT` reconstructs the work, candidate, required evidence and scope for that assignment. `why WORK` explains the evidence or acceptance the work still needs. Each command can read a complete historical revision. `run` takes one local driver turn and reports whether it dispatched work or went idle. A trusted launcher can attach a scripted executor to its assigned identity. The owner CLI can evaluate eligible child closure directly. [Work and evidence operations](docs/contracts/work-v1.md)

When a requirement changes, `status` lists the premise that changed, the decisions under reconsideration and the work that became stale. Use `why WORK` to follow a dependency to its observation or to see which evidence still supports an unaffected item. Add `--revision N` to inspect the same item before the change. [Decision and dependency operations](docs/contracts/decisions-v1.md)

For a registered private Git repository, `frontier` shows when an approved publication can run. `run --git-binding FILE` takes one step. After an interruption, the next step inspects Git to determine whether the original write landed. A still-running writer leaves the action pending. `status` reports the grant and any unresolved attempt. [Git setup and foreground commands](docs/contracts/git-effects-v1.md#running-a-governed-effect)

## Read the model study

```sh
python3.11 -m experiments.model_study report
```

With no argument the report shows all 195 scheduled cases and marks the uncollected results as unknown. To rebuild a collected series, pass its directory after `report` from the registered source checkout. Both commands run offline. The tables give each arm’s measured and scheduled counts, costs and comparison with the plain loop. [Experiments and current coverage](docs/evidence/M5/dispatch.md)
