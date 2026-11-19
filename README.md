# Purser

Purser is an experimental local runtime for developers building coding agents. It keeps a task’s history outside the agent’s conversation. When a session ends, the next agent or human reviewer can see which change was submitted, which checks passed, and what still needs doing.

Purser runs on one trusted machine. It records work, decisions and permissions in SQLite and stores supporting evidence beside the database. Today you can use the foreground CLI or connect a local executor in Python. Interactive coding-agent integration is in development.

## Try a handoff

A scripted agent has changed a notification sender’s configuration to allow three delivery attempts. Its session ends before anyone verifies that the sender actually stops after the third failure. Try picking up where it left off.

You need Git, a POSIX shell and Python 3.11 or newer. After cloning, the example runs locally using only Python’s standard library.

```sh
git clone https://github.com/lookmanrays/purser.git
cd purser
db=$(python3.11 -m examples.handoff)
python3.11 -m purser --db "$db" why W-retry-limit
```

The example creates a temporary workspace. `db` holds its database path. The `why` response shows the work is `submitted`. Its `reasons` list names the outstanding check.

```json
{"code": "missing_evidence", "refs": ["O-three-attempts"]}
```

```sh
python3.11 -m purser --db "$db" frontier
python3.11 -m purser --db "$db" context AS-retry-limit
```

`frontier` gives the next action, `observe`, for `O-three-attempts`. `context` shows the submitted configuration’s digest and the check the next session needs to verify. Each command reads the same history in a fresh process. [Runnable example](examples/handoff.py)

## Use Purser in a project

To start a workspace, propose a **commitment**, the outcome you want Purser to track. This example proposes checking the links in a user guide. For work you want to keep, choose a persistent directory for the database. [Example proposal](examples/check-links.json)

```sh
workspace=$(mktemp -d)
python3.11 -m purser --db "$workspace/work.db" init
python3.11 -m purser --db "$workspace/work.db" propose examples/check-links.json
python3.11 -m purser --db "$workspace/work.db" why C-user-guide
```

The receipt records the proposal. The task starts in `proposed` state and awaits the owner’s approval to begin. [Proposal format and activation](docs/contracts/kernel-v1.md)

- **Connect an executor.** The Python driver passes the current assignment to your callback and records the candidate it returns. Purser checks independent evidence before closing a work item. The owner accepts the overall result. [Driver](purser/driver.py) · [Work and evidence](docs/contracts/work-v1.md)
- **Handle changed requirements.** When a recorded assumption changes, Purser can reopen affected work and preserve completed work that still holds. [Dependency tracking](docs/contracts/decisions-v1.md)
- **Publish an approved change.** Purser can advance a permitted branch in a dedicated local Git repository. If the process stops during publication, the next driver turn checks whether the update landed before retrying. [Local Git setup and recovery](docs/contracts/git-effects-v1.md#running-a-governed-effect)

## Contributing

From the checkout, run the CLI and storage tests with Python’s standard library.

```sh
python3.11 -m unittest -q tests.test_cli tests.test_store
```

The contributor guide covers the source layout, full test suite and how to reproduce the published experiments. [Contributing](CONTRIBUTING.md) · [Architecture](docs/architecture-v0.5.md) · [Roadmap](docs/delivery.md#current-implementation)

Purser is licensed under the [Apache License 2.0](LICENSE).
