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

1. Find the model slug and check that print mode works in your terminal and from Codex.

   ```bash
   agy models
   agy -p "Reply with exactly: OK" --model gemini-4-hydrogen-high \
     --output-format json --print-timeout 2m
   ```

   Expect `SUCCESS` and a response of `OK` after trimming whitespace. If print mode hangs without output, retry with a pseudo-terminal. On macOS use `script -q /dev/null agy -p ...`. On Linux use `script -qec "agy -p ..." /dev/null`.

2. Use `--mode accept-edits` for workspace edits and keep `--sandbox` enabled for terminal execution. Pass the shell commands and MCP tools required by the task in the session-only `--settings` allowlist. Headless mode soft-denies requests that need interactive approval. The process can still exit 0 and report `SUCCESS`. [Headless mode](https://antigravity.google/docs/cli/headless/) · [Permission rules](https://antigravity.google/docs/permissions/)

3. Use `--dangerously-skip-permissions` only when the human explicitly authorizes it for the run. Add it to the sandboxed command below for that run. On `agy` 1.5.17 it cleared shell and MCP approval prompts. An explicit deny rule still blocked the command it matched, and the terminal sandbox still blocked Git from creating `.git/index.lock`. [Terminal sandbox](https://antigravity.google/docs/sandbox/)

## Each task

From the build worktree, create a separate worktree and branch for the delegated task. This example uses the P1-03 proposal parser.

```bash
package_id=P1-03
task=proposal
delegated_branch="agy/$package_id/$task"
delegated_worktree="../agy-$package_id-$task"
prompt_file="/tmp/agy-$package_id-$task.md"
git worktree add "$delegated_worktree" -b "$delegated_branch" HEAD
```

Write the prompt to `$prompt_file`. Name the package and brief, the files Antigravity may write, the reference file and function to port, and the test command that proves the result. Include these lines.

```text
Do not edit *_test.go, testdata, contracts/ or docs/.
Leave edits for the builder to review and commit. Do not stage, commit or push.
Reply with what you changed, the commands and results, and every reference rule you could not match.
```

Run from the delegated worktree. Adjust the allowlist to the task's commands.

```bash
trace="/tmp/agy-$package_id-$task.ndjson"
stderr_log="/tmp/agy-$package_id-$task.err"
settings='{"permissions":{"allow":["command(go test)","command(go vet)","command(gofmt)","command(git diff)","command(git status)"]}}'

(
  cd "$delegated_worktree" || exit
  agy -p "$(cat "$prompt_file")" --model gemini-4-hydrogen-high \
    --mode accept-edits --disable-slash-commands --sandbox \
    --settings "$settings" --output-format stream-json --print-timeout 30m
) > "$trace" 2> "$stderr_log"
agy_exit=$?
printf 'agy exit = %s\n' "$agy_exit"
jq -r 'select(.event == "result") | .result | .status, .conversation_id, .response' "$trace"
jq -c 'select(.event == "step_update" and .step_update.state == "ERROR") | .step_update.tool_info.error' "$trace"
cat "$stderr_log"
```

Check the exit code, terminal result, tool errors, and stderr. Verify the expected files and test output before treating the task as successful. A permission denial can leave `SUCCESS` with an empty response. [Streaming JSON events](https://antigravity.google/docs/cli/headless/#streaming-json)

When a call needs approval, use its exact command or MCP server/tool name from the trace. A P1-03 diagnostic on `agy` 1.5.17 verified both grants below with the sandbox enabled.

```text
command(ls -la contracts/ internal/kernel/codec/ internal/kernel/gate/)
mcp(serena/initial_instructions)
```

Add the needed rule to `permissions.allow` and rerun with `--conversation <conversation_id>`, a follow-up prompt, and fresh trace paths. Serena must be configured in Antigravity for its MCP calls to work. [MCP configuration](https://antigravity.google/docs/cli/mcp/) · [Permission rule syntax](https://antigravity.google/docs/permissions/)

## Review and integrate

Back in the build worktree, review the delegated worktree's tracked diff and open any new files before staging.

```bash
git -C "$delegated_worktree" status --short
git -C "$delegated_worktree" diff HEAD
git -C "$delegated_worktree" ls-files --others --exclude-standard
```

Check the diff against the reference and rerun the focused tests yourself. Stage only reviewed files and commit with the builder's Git from the delegated worktree. Cherry-pick, edit or rewrite the result in the build worktree. Keep at most three delegated tasks active. After integration or discard, remove the delegated worktree when clean and delete the local branch.

```bash
git worktree remove "$delegated_worktree" && git branch -D "$delegated_branch"
```

## Record

Add a **Delegation log** to the pull request body:

| Task | Conversation ID | Minutes | Files | Outcome | What you had to fix |
| --- | --- | --- | --- | --- | --- |

Outcome is one of: taken as is, edited, rewritten, discarded. End with a short verdict: did delegation save time, where did Antigravity drift, and what would you delegate next time.
