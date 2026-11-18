# Purser

Purser is a local research kernel for coding work that survives a change of agent or a process restart. It tracks commitments, scoped authority, observations and the evidence needed to close work.

Read the [architecture](docs/architecture-v0.5.md), the [implementation plan](docs/implementation-plan-v0.5.md) and the [delivery record](docs/delivery.md) for the current milestone and adopted amendments.

## Try the local CLI

Run these commands from this checkout with Python 3.11 or newer. The database lives in a private temporary directory for this example.

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

The creation receipt records revision 2. `why` explains that the commitment awaits activation. Running the identical proposal again returns its existing receipt. The [wire contract](docs/contracts/kernel-v1.md) describes activation, historical reads and JSON exit codes.

For an active commitment, `frontier` lists permitted next actions. `context ASSIGNMENT` reconstructs the work, candidate, required evidence and scope for that assignment. `why WORK` explains the evidence or acceptance the work still needs. Each command can read a complete historical revision. `run` takes one local driver turn and reports whether it dispatched work or went idle. A trusted launcher can attach a scripted executor to its assigned identity. The owner CLI can evaluate eligible child closure directly. [Work and evidence operations](docs/contracts/work-v1.md)

When a requirement changes, `status` lists the premise that changed, the decisions under reconsideration and the work that became stale. Use `why WORK` to follow a dependency to its observation or to see which evidence still supports an unaffected item. Add `--revision N` to inspect the same item before the change. [Decision and dependency operations](docs/contracts/decisions-v1.md)

For a registered private Git repository, `frontier` shows when an approved publication can run. `run --git-binding FILE` takes one step. After an interruption, the next step inspects Git to determine whether the original write landed. A still-running writer leaves the action pending. `status` reports the grant and any unresolved attempt. [Git setup and foreground commands](docs/contracts/git-effects-v1.md#running-a-governed-effect)
