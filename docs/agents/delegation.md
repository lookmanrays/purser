# Experiment: a Codex builder delegates to Antigravity

A Codex builder may hand bounded tasks to Antigravity through its headless CLI (`agy -p`) and integrate the results. This is a small version of what Purser's worker adapters (P4-09) will do, so the log you keep is part of the result.

The experiment runs on **P1-03 only**. Afterwards we keep, change or drop it.

## Rules

- Delegate only when your launch prompt allows it.
- You stay the builder. You own the design (files, types, the order of checks, the transaction), every integration, the reference-parity check and the pull request. Antigravity only gets bounded tasks.
- Good tasks: one operator, one validation table ported from a named reference function, one helper type. Each names its files, the reference to port and the tests that prove it.
- Never delegate frozen tests, contracts, briefs or the pull request. Never push an `agy/*` branch.
- Review every delegated diff line by line against the reference before you take it. You answer for it as if you wrote it.
- Frozen tests, scope, budget and CI are unchanged. A package Antigravity helped build is not reviewed by Antigravity: its brief's `reviewer` becomes `you`, which means Claude's reference-parity review plus the human's approval.

## Set up once

1. In a terminal outside Codex, find the model slug and check that print mode works:

   ```bash
   agy models                                   # note the Gemini 4 Hydrogen slug
   agy -p "Reply with exactly: OK" --output-format json --print-timeout 2m
   ```

   Expect `"status":"SUCCESS"` and `"response":"OK"`.
2. Permissions. Print mode has no approval prompt: a tool action that needs approval is soft-denied, the run continues and still exits 0, with a notice on stderr. Antigravity needs to edit files and run `go`, `gofmt` and `git`. Either add allow rules under `permissions.allow` in `~/.gemini/antigravity-cli/settings.json` (for example `"command(git)"`, `"command(go)"`, plus whatever rule the CLI documents for file edits), or pass `--dangerously-skip-permissions`, which approves every tool call, including commands outside the worktree. That choice is the human's.
3. From inside Codex, run the same smoke test. If it hangs or prints nothing (non-TTY bugs have been reported), give it a pseudo-terminal: on macOS `script -q /dev/null agy -p ...`, on Linux `script -qec "agy -p ..." /dev/null`. `agy` writes its logs under `~/.gemini/antigravity-cli/` and needs the network; if your sandbox blocks that, ask to run it outside the sandbox.

## Each task

From your build worktree (`.worktrees/build-<ID>`), give the task its own worktree and branch, so two agents never edit the same checkout:

```bash
TASK=<short-name>
git worktree add ../agy-<ID>-$TASK -b agy/<ID>/$TASK HEAD
```

Write the prompt to `/tmp/agy-<ID>-$TASK.md`. It states the package ID and brief path, the task, the files Antigravity may write, the reference to port (file and function), the test command that proves it, and these lines:

```text
Do not edit *_test.go, testdata, contracts/ or docs/. Do not push.
Commit on the current branch and stop.
Reply with what you changed and every reference rule you could not match.
```

Run it, and treat anything other than `SUCCESS`, or a soft-deny notice on stderr, as a failed task:

```bash
cd ../agy-<ID>-$TASK
agy -p "$(cat /tmp/agy-<ID>-$TASK.md)" --model <slug> --effort high \
  --output-format json --print-timeout 30m \
  > /tmp/agy-<ID>-$TASK.json 2> /tmp/agy-<ID>-$TASK.err
jq -r '.status, .conversation_id, .response' /tmp/agy-<ID>-$TASK.json
```

Then, back in your build worktree, read `git diff HEAD...agy/<ID>/$TASK`, and cherry-pick, edit or rewrite. For a follow-up in the same Antigravity session, add `--conversation <conversation_id>`. At most three tasks at once. When done:

```bash
git worktree remove ../agy-<ID>-$TASK && git branch -D agy/<ID>/$TASK
```

## Record

Add a **Delegation log** to the pull request body:

| Task | Conversation ID | Minutes | Files | Outcome | What you had to fix |
| --- | --- | --- | --- | --- | --- |

Outcome is one of: taken as is, edited, rewritten, discarded. End with a short verdict: did delegation save time, where did Antigravity drift, and what would you delegate next time.
