# Running agents on Purser

Every task is one package and one role. The agent sets up its own branch and worktree, does the work, opens a pull request and stops. The playbooks hold the full instructions, so a launch prompt is one line:

| Role | Playbook | Launch prompt |
| --- | --- | --- |
| Test author | [test-author.md](test-author.md) | `You are the test author for Purser package <ID>. Follow docs/agents/test-author.md exactly.` |
| Builder | [builder.md](builder.md) | `You are the builder for Purser package <ID>. Follow docs/agents/builder.md exactly.` |
| Reviewer | [reviewer.md](reviewer.md) | `You are the reviewer of pull request #<N> for Purser package <ID>. Follow docs/agents/reviewer.md exactly.` |

Start every agent in the main checkout (the repository root). It creates its worktree under `.worktrees/`, which Git and the Go tools ignore.

## Chats

- A new chat for every package and role. Fresh context drifts less.
- The same chat only to fix CI failures or review comments on its own pull request, or to resume after a contradiction is resolved.
- A builder never reviews, and a reviewer never edits code.

## Models

| Tool | Model and effort |
| --- | --- |
| Codex | GPT-6.2 Sol. Medium for P0 and for tests; High for kernel builds and kernel reviews; Extra High only in a new chat when a frozen test resists. Never Astra, Max or Ultra. |
| Antigravity | Gemini 3.8 Flash in Planning mode, highest effort for builds and kernel tests. |

The agent needs network access for `git push` and `gh`. If its sandbox blocks the network, approve those commands when it asks.

## What stays with the human

- Merging pull requests that change `contracts/`, and every size M or L build.
- Freezing: for size M and L, approve the tests pull request; then the tag `frozen/<ID>` is pushed and the tests pull request is closed without merging.
- Removing finished worktrees: `git worktree remove .worktrees/<name>`.
