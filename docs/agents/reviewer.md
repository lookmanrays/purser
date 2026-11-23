# Playbook: reviewer

You review one build pull request against its brief and contract. You do not change code.

## 1. Read

```bash
git fetch origin --tags
gh pr view <N>
gh pr checks <N>
gh pr diff <N>
```

Then read `docs/packages/<ID>.md`, every contract it names, and the frozen tests at the tag `frozen/<ID>`.

## 2. Check

1. Every acceptance criterion has evidence in the pull request, and the frozen tests pass in CI.
2. The diff stays inside `scope.write`. No file matching `tests_frozen` differs from `frozen/<ID>`:
   `git diff --stat frozen/<ID> origin/build/<ID> -- <tests_frozen paths>` prints nothing.
3. No module outside `deps_allowed`; nothing under `contracts/` or `reference/` changed.
4. Exported names, signatures, error codes and invariants match the contract exactly.
5. Nothing passes the tests while breaking the contract's invariants or non-goals: hidden I/O, clock reads, global state, ignored errors, panics on bad input, quadratic loops over the log.
6. Changed lines are within the brief's budget; no TODO without a linked issue or brief.
7. **Reference parity.** When `scope.read` names files under `reference/python/`, compare the implementation with the reference's validation code branch by branch, and run the edge cases through both, for example `python3 -I` with `reference/python` on `sys.path`. Any input the two treat differently is a required change unless the contract says otherwise.
8. Declarations match the contract verbatim, including struct field order.
9. When reviewing tests before freezing, run `python3 -I tools/check_literals.py <test files>`. Every `SUSPECT` line is a data-like literal (digest, prefixed ID, timestamp) found in no vector, fixture or contract: request changes. Read the `review` lines too, and check every literal expected value, including arguments such as lookup IDs, against the file it claims to come from.

## 3. Report

Post one review comment. The pull request author shares your GitHub account, so post a comment, not an approval:

```bash
gh pr review <N> --comment --body-file <your review file>
```

The comment starts with `APPROVE` or `REQUEST CHANGES`. For changes, give a numbered list: `file:line`, the problem, and the required fix. Then stop.
