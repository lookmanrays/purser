# Purser implementation plan · v0.5

18 November 2026 · The orchestrator chat owns the project and its architecture. Delivery begins at M0. All nine milestone gates await acceptance.

Build a local research kernel that can explain work without a model, reject a false completion, reopen only affected work and recover an uncertain Git effect. Then test the same kernel with one real coding agent and in daily use. This plan assigns independently verifiable work to chats or agents while keeping one owner for the integrated result. [Architecture v0.5](architecture-v0.5.md)

## How to start a delivery cycle

The inspected starting commit is `bdcbb88` and contains the README, license and ignore file. The two v0.5 Markdown files are the planning inputs for the first delivery base. Before starting work in separate worktrees, the orchestrator includes those files in the shared base or supplies a retrievable content-identical snapshot. Record the actual base SHA, design version and document digests in the first dispatch. A worker must be able to reload its brief after its chat context disappears.

Run M0-A and M0-B concurrently. Their outputs meet at M0-G, where the orchestrator freezes the event/proposal examples and the experimental scorer contract. M1-A and M1-B can then start from the same base. Later gates operate the same way. Preparation and execution of a later milestone begin after the preceding milestone gate. Parallelism is inside each milestone. The calendar constraints are M7’s observed week and M8’s four-week field log. Model budget and human availability determine when their gates can run.

## Ownership and file boundaries

| Role | Owns | Hand-off |
| --- | --- | --- |
| Orchestrator and architect | Scope, architecture decisions, contract versions, work assignment, dependency order, integration branch, acceptance record and go/no-go decisions. | Gives each worker one named package, base and write area. Integrates the candidate, commissions independent review and accepts or returns it against the gate. |
| Kernel worker | Event encoding, SQLite store/transactions, gate, projection and replay in `purser/kernel/`. | Supplies public contracts and fixtures to domain workers. Implements the one gate-side registration when several workers consume it. |
| Work and evidence worker | Work state, typed evidence and deterministic context/frontier/impact/closure in `purser/derive/` and assigned `purser/observe/` modules. | Returns pure rule results and gate integration requirements. Does not open its own write path. |
| Effects worker | Grant and intent execution, Git adapter, independent Git read boundary and recovery in assigned `purser/effects/` and `purser/observe/git*` files. | Supplies a real-repository trace, crash evidence and documented residual uncertainty. |
| Evaluation and assurance worker | Fixture project, baseline and comparison arm, hidden scorer, fault schedule, golden answers and raw-result format in `fixtures/`, `experiments/` and assigned test files. | Freezes external truth before seeing outcomes. The implementer cannot change the pass condition to clear a failure. A different person or agent reviews changes to the scorer itself. |
| Adapter and operations worker | CLI/driver when assigned, model adapter, local MCP transport, isolation, supervisor, approvals and backups in the assigned `adapters/` or CLI paths. | Shows the actual adapter, host boundary and first consumer available at that gate. |
| Independent reviewer or verifier | Reviews the exact integrated candidate and runs the named boundary checks from a clean worktree. | Reports invariant, impact, reliable evidence, candidate SHA and disposition directly to the orchestrator. A chat cannot sign off its own implementation. |

Roles describe responsibilities and may be reassigned between packages. For a small team, start with the orchestrator, two workers with disjoint outputs and an independent reviewer when a candidate is ready. The same human principal may sponsor the project, but only their authenticated runtime action grants permission or accepts an outcome. A confident worker summary, CI status, or orchestrator merge cannot stand in for that act.

The orchestrator owns the shared schema, event/CLI interfaces, fixture manifest, decision log and integration wiring. It may delegate one named editor for each shared surface at a time. Other workers return a contract delta with the invariant, consumers, migration effect and example bytes. Once adopted, the orchestrator publishes the new contract and base, releases the dependent packets and identifies stale evidence. Keep exclusive ownership narrow enough for the other workers to continue.

## Dispatch, integration and recovery across chats

1. **Assign.** Record package ID, invariant IDs, accepted prerequisites, exact base/contract, writable paths, read-only dependencies, allowed effects, required independent evidence and return destination. Each worker gets an isolated worktree or checkout and a short-lived branch, for example `lookman/m1-a-event-store`. Two workers may consume one fixture contract concurrently. Exactly one owns any given fixture’s expected answers.
2. **Preflight.** For shared or system-impacting changes, the worker records the risk, invariant, owning layer, operating feasibility, affected callers, concurrency/failure surfaces and planned real-boundary check before editing. Use the applicable engineering-change or design workflow. A discovered account, host, trust or topology change goes back to the orchestrator for a scoped decision. Other unblocked packets continue.
3. **Build and return.** A worker tests its smallest coherent change through its public entry point and returns a commit or immutable patch, its base, contract version, commands, observed outputs and residual limits. Mark status with the actual boundary achieved. Before M2, a small repository delivery record can hold this information. From M2, use Purser for its own commitments and link the same candidate and evidence. Do not maintain two independent acceptance ledgers.
4. **Integrate.** The orchestrator orders prerequisites, applies the changes to the integration candidate and records the resulting SHA. Tests that used fakes establish a component result. The integrated gate must exercise the real CLI, repository, runner, channel or restore boundary named below. A merge that changes behavior, dependencies or the tested candidate requires new evidence at the affected boundary.
5. **Review and accept.** Give an independent reviewer the design, candidate and expected outcome before exposing the worker’s conclusions when feasible. For a substantive finding identify the violated invariant, affected siblings and root cause. Use review-remediation before another edit for a repeated cluster. The owning worker returns a new candidate. The orchestrator accepts only the candidate actually reviewed and verified. A passing gate does not itself publish software, send messages or deploy.
6. **Resume or unwind.** A replacement chat reads the package record, exact base, decisions, candidate and outstanding evidence. It can resume without the previous transcript. If an accepted prerequisite changes, mark affected packets for revalidation, preserve useful independent work and reassign the owner. Use an ordinary revert or forward fix for a bad integrated change. Recover persisted user state from a validated restore or forward correction with an explicit principal decision where data would be lost. Historical event bytes remain intact.

Record package state as planned, assigned, candidate submitted, changes requested, blocked on a named dependency, or accepted at a named boundary. Record who can resolve a blocker. CI supplements the local witness. Keep raw evidence free of credentials and real-user payloads.

## Dependency map

Each `M*-G` is the orchestrator’s integration and acceptance step. The immediately preceding gate is a prerequisite for every package in the next milestone. The table gives the additional dependencies inside the milestone.

| Gate | Workers that can start together | Joins before the gate | First meaningful consumer |
| --- | --- | --- | --- |
| M0-G | M0-A contracts, M0-B experimental protocol | Both agree on the worked trace and independent oracle | Worker in a fresh chat can reconstruct the brief |
| M1-G | M1-A store, M1-B CLI and replay consumer | M1-C admission after M1-A. Then integrate M1-B | Fresh CLI process and cold SQLite projections |
| M2-G | M2-A work/evidence, M2-B baseline and E1 | M2-C driver, partial continuity and real dogfood after both | Independent fixture observer and human CLI |
| M3-G | M3-A dependency semantics, M3-B E2 and goldens | Join both at the gate | Frontier, closure and `why` on affected and unaffected work |
| M4-G | M4-A authority, M4-B real Git adapter | M4-C recovery and full continuity after both | Git refs observed after a killed broker and fresh CLI |
| M5-G | M5-A model adapter, M5-B reproducible run/report harness | Authorize spending, run frozen arms, then decide on v1 | Reproducible report and human go/no-go input |
| M6-G | M6-A enforced isolation, M6-B MCP transport | M6-C end-to-end coding milestone after both | Real agent, independent observer and human root acceptance |
| M7-G | M7-A supervisor, M7-B approvals, M7-C backup/restore | Integrate before the measured week | Authenticated remote reply, idle daemon and restored workspace |
| M8-G | M8-A second domain, M8-B field measures and old-log rehearsal | Collect complete qualifying field evidence | Owner decides whether to continue |

## M0 · Freeze the contracts that workers share

| Packet and writer | Deliverable | Acceptance and hand-off |
| --- | --- | --- |
| M0-A · Architect with kernel input | A small contract fixture for admission, receipt/deduplication, event bytes/hash versions, work generation, verifier run and closure/coverage. Specify error codes, canonical serialization, gate read dependencies, logical effect and attempt IDs, clock evaluation and CLI JSON used by the continuity test. Cover both revocation orders and a lost Git acknowledgment in the example trace. | A fresh reviewer can hand-derive the expected work, grant state, receipt and allowed next action at each checkpoint. An unrelated update and a conflicting dependency have distinguishable outcomes. Publish a versioned contract for M1-A/B/C and M2-A. |
| M0-B · Evaluation and assurance | Pin starting fixture, three-part task, exactly 20 paired seed IDs per original experiment, interventions, budgets, outcome definitions and independent truth. Define the additional equal-verifier arm, clean-success controls, step and time accounting, real-model cost fields and field-log denominators. Freeze the scoring schema and access boundary. | A reviewer manually scores a false submission, one valid sibling and an uncertain Git effect without consulting Purser’s output. Retain the v0.4 absolute and comparative thresholds verbatim. Publish scorer/fixture IDs for all experiment workers. |

**M0-G.** The orchestrator resolves differences between the contracts and oracle, names the supported host profile, records the local capability scope, root acceptance and line-count scope, and signs the design disposition. Later provider, account, remote channel and distribution choices have named owners and decision points. The deliverable is a shared implementation base with versioned examples. It is possible to complete this gate offline.

## M1 · Build the replayable single write path

| Packet and writer | Deliverable | Acceptance and hand-off |
| --- | --- | --- |
| M1-A · Kernel | Implement schema initialization, versioned event bytes, hash verification, SQLite write transactions, event batch append, projected revision and clean rebuild. Retain original event bytes. Start with the minimum record subset required by the public examples. | Use a real SQLite database. Interrupt append/project at a controlled point and reopen. Reader sees an old or complete new batch. Rebuild from the log and match the committed state. An unknown version or broken hash stops replay with its location. Supply store API and old-format fixture to M1-C. |
| M1-B · CLI/assurance | Build the module CLI, init/status/replay command contracts, read-only inspection and an independent projection digest comparator. Develop against the M0 interface while M1-A owns the store. | A fresh process initializes an empty workspace, reads a populated one and compares cold rebuild to live projections in stable order. Use the real M1 store/gate in the integrated check. Return CLI invocation and golden output. |
| M1-C · Kernel, after M1-A | Implement ordered multi-op admission, derived read-dependency checks, authenticated proposal identity, canonical request digest, pending/final dispositions, durable rejection receipts and atomic writes. Bind one editor to gate/registry wiring. Domain-specific operators arrive in later packets. | Exercise same-ID replay after response loss, mismatched payload for one ID, a stale dependency, an unrelated intervening revision and a partially invalid batch. A fresh client sees one batch and the recorded disposition. Pending requests survive cold rebuild. |

**M1-G.** Independent verifier uses the CLI and two SQLite connections/processes, checks I1/I2, and records the final revision and live versus replay digest. Document which guarantees rely on a trusted local process at M1. Qualify untrusted-host containment at M6.

## M2 · Close work from evidence and establish E1

| Packet and writer | Deliverable | Acceptance and hand-off |
| --- | --- | --- |
| M2-A · Work and evidence | Implement commitment, work, scoped assignment, generation, obligation/criterion and run identity. Register result, observation, verdict, waiver, principal acceptance, leaf/parent coverage and closure operators through the gate. Integrate gate changes through its assigned owner. | Current B refuses A’s old verdict. Reassignment preserves provenance. Conflicting verdicts reopen the criterion for recheck. Exercise all/any/k-of-n, empty leaves, explicit waivers and a root that waits for human acceptance. Return a real fixture work/closure trace. |
| M2-B · Evaluation and assurance | Build a controlled coding fixture with independent tests/artifact checks, a plain baseline, an equal-verifier comparator and E1 fault injection. Keep the scorer outside writable candidate inputs. Count false closures and honest correct closures. | Both original arms receive identical starting trees, seeds and budgets. Run all 20 E1 pairs, include skipped work and clean controls, and report missing-obligation identification, false closures, happy-path costs and the separately labeled comparator. |
| M2-C · CLI/driver after M2-A/B | Implement assignment ContextPacks, deterministic Frontier, driver dispatch/idle, explanations and the M2 subset of continuity. Record one actual Purser development change as a commitment through the CLI. | Kill the normal process with current work closed, blocked or submitted. Disable models and reproduce the supported answers, including why the root awaits acceptance. The dogfood record points to the actual candidate, independent observer and owner act. |

**M2-G.** Review I3’s M2 subset and I4/I5/I6/I11/I12 at their available boundaries. Publish E1 raw results even when the baseline performs equally well. Retain one real M2 log and its evidence artifacts for later upgrade recovery.

## M3 · Reconsider decisions and protect unaffected work

| Packet and writer | Deliverable | Acceptance and hand-off |
| --- | --- | --- |
| M3-A · Work and kernel owners | Implement supported assumptions, decision triggers, cause references, dependency-only recursive traversal and same-revision effects on Frontier/closure. Recompute parental coverage separately. Expose an explanation showing the invalidation path and unaffected basis. | Test mixed parent/dependency edges, cycles, a decision opinion without a trigger, and a close racing invalidation in both orders. Restart after invalidation. Stale support cannot authorize a later dispatch or an old-generation close. |
| M3-B · Evaluation and assurance | Inject the changed premise at the exact frozen checkpoint. The two unaffected subparts have closed, and subpart 2 already performed work relying on the premise. Score sibling retention, erroneous retained closures, rework, final pass rate and both raw step counts. Add an already-closed affected item in a separately labeled stress case. | Run 20 paired E2 variants against the original arms and report the diagnostic comparator separately. Rebuild with models off and compare decision, support, staleness and next-action answers. |

**M3-G.** A cold CLI explains why W9 reopened and W10 preserved its valid evidence. I8/I9 and the expanded continuity subset pass on the integrated candidate. Preserve the mixed-edge regression and raw E2 results.

## M4 · Qualify authority, Git effects and full continuity

| Packet and writer | Deliverable | Acceptance and hand-off |
| --- | --- | --- |
| M4-A · Kernel/authority worker | Reuse M2’s principal-action binding for grant issuance and revocation. Validate grantee, commitment, assignment scope, allowed branch/paths, expiry and live generation. Implement immutable logical intent, distinct attempts, unresolved-state exclusion and serialized dispatch authorization versus revocation. Use the existing log and gate. | Test expired, revoked, wrong-resource and narrowed grants, and both orders of revocation versus dispatch cutoff. A previously authorized but abandoned attempt cannot be replayed as a fresh permission. Repeat a human response after restart and verify at most one authorized transition. |
| M4-B · Effects worker | Use a disposable real Git repo to qualify the expected-old branch update plus protected receipt creation. Build independent read-only observation of exact intent/target, stale preconditions, later branch advance and inconsistent observations. Keep credentialed Git access within the broker boundary. | Count actual branch effects. The original transaction succeeds, a conflicting repeat has no second effect, a valid receipt remains attributable after a later ref advance, and a mismatched or unreadable receipt cannot settle the operation. Document quiescence and the precise absence condition. |
| M4-C · Integration and assurance after M4-A/B | Connect durable intent, broker and observer through public entry points. Add deterministic crash boundaries before dispatch, after dispatch admission, after Git writes but before acknowledgment, and after reconciliation before reply. Exercise both E3 outcomes, the 10-question golden file and fixed-time/live-expiry variants. | Kill and restart separate processes. A landed effect settles exactly once. A known absent effect retries only after fresh authorization and a safe precondition. An unverifiable outcome is explicitly pending. Run all 20 E3 pairs. The separate irreducibly ambiguous case stays visible without corrupting the original E3 measure. |

**M4-G.** An independent verifier starts from the integrated candidate, a copy of the event log and the referenced observable fixture. Rebuild projections with all model adapters disabled and compare every continuity answer. Then separately reconcile through Git, confirm one effective branch mutation and rerun all twelve invariant checks. Record the time at which authorization wins or loses. A lucky final branch tree alone cannot establish correct retry or revocation behavior.

## M5 · Measure real models and decide whether to build v1

| Packet and writer | Deliverable | Acceptance and hand-off |
| --- | --- | --- |
| M5-A · Model adapter worker | Implement one replaceable model adapter behind the existing executor contract. Bound time, attempts, token/spend ceilings, fixture exposure and permitted network destination. Capture provider/model version, parameters, prompts or hashes and request outcome. | First exercise the adapter with a canned response and with model access disabled. Before a paid run, verify current access, approved ceiling and useful observability. An unavailable provider produces a visible incomplete run. It cannot manufacture an experiment result. |
| M5-B · Evaluation and assurance | Build a repeatable command that loads frozen manifests, runs both original arms plus the diagnostic arm, preserves raw per-seed files and writes the paired comparison. Keep test fixtures, fault schedule, valid-work scoring and missing-run accounting consistent with scripted runs. | Run an offline rebuild of the report from raw files and reproduce its tables. Then collect the approved real-model trials with identical budgets per arm. Record actual completion counts, model variation, cost, wall time and errors. Any changed scored condition gets a new experiment version. |

**M5-G.** Publish all absolute thresholds and comparative results, including ties, failures and happy-path cost above twice baseline steps. Keep scripted, real-model and dogfood evidence identifiable. A negative result is a publishable v0 outcome. The orchestrator records go, revise or stop for v1, with rationale, the exact candidate, and the human’s operating/budget decision. An unavailable real-model run leaves the real-model criterion open.

## M6 · Admit one real coding agent through MCP

| Packet and writer | Deliverable | Acceptance and hand-off |
| --- | --- | --- |
| M6-A · Isolation/host worker | Choose a feasible host runner and demonstrate it isolates an assigned workspace, resources, network, canonical Git/receipt refs, SQLite/WAL, credentials and trusted tests. Use a separate observer context and lock the candidate bytes under verification. | An allowed edit succeeds. Boundary checks show the process cannot mutate protected state and cannot substitute its own trusted verifier. Clean up processes and workspace after cancel, crash or timeout. If the host cannot enforce this, return its evidence and narrower feasible options to the orchestrator. |
| M6-B · MCP/transport worker | Expose the four architecture tools through an assignment-bound bridge to one foreground gate owner. Define launch/session identity, private local transport, reconnect, budget enforcement, duplicate submission and wrong-assignment handling. | Start the real client adapter and gate as separate processes. Disconnect after an admitted proposal, reconnect and get the original outcome. Read-only context is scoped. Neither a second bridge nor requested identity opens another authority or raw storage route. |
| M6-C · Orchestrator and independent verifier after M6-A/B | Choose a real Purser development milestone small enough to observe and grant narrowly. Use one real isolated agent, fixed trusted criteria, the canonical branch effect and the project owner’s normal human acceptance surface. | Capture the agent’s actual MCP proposals and submission, independent observation of the candidate, derived work closure, reconciled branch effect and human root acceptance. A rejected or interrupted attempt remains explainable across sessions. |

**M6-G.** Confirm the real milestone closed through the planned mechanism, with proof of both isolation and the first real consumer. A scripted client passing its contract test earns an adapter result only. A second coding agent is optional after the first path is qualified.

## M7 · Operate, obtain approvals and recover

| Packet and writer | Deliverable | Acceptance and hand-off |
| --- | --- | --- |
| M7-A · Runtime worker | Supervise the same local owner, add wake-on-event/timer and bounded retry/backoff for wake delivery. Re-derive eligible work after every restart. Define stop/start, process ownership, idle counters, and health/diagnostics without content leakage. | Model-call counters stay flat during idle. Duplicate wakes and restart during a wait neither lose work nor duplicate effects. Operators can distinguish waiting for a human, an observer, or provider recovery. |
| M7-B · Approvals worker | Select a feasible, human-authorized remote channel. Send the principal the exact proposed act, scope, expiry and evidence reference. Bind authenticated replies to the durable request. Treat notification delivery separately from approval. | Exercise remote round-trip, loss of reply, duplicate reply, wrong actor, stale digest and expiry. Rebuild from log while approval is pending. A loopback-only test leaves remote acceptance open. |
| M7-C · Storage/operations worker | Back up live SQLite with the online backup API, copy exactly the digest-addressed evidence retained at that revision, verify a manifest and prepare restore into a separate environment. Confirm the selected off-machine location and retention with the human. | Back up while a committed revision remains in WAL. Restore from the off-machine snapshot, verify a known head and artifact hashes, rebuild empty projections and rerun continuity. Include a missing/corrupt artifact case with an explicit incomplete restore. |

**M7-G.** Observe seven days of the integrated daemon on the agreed host, including remote principal approval, restart and a successful separate restore. Record interruption/availability, unresolved effects and idle model calls. Record the actual observation dates. The separate restore supplies the backup acceptance evidence.

## M8 · Evaluate a second domain and decide on continued use

| Packet and writer | Deliverable | Acceptance and hand-off |
| --- | --- | --- |
| M8-A · Domain/observability worker | Agree one real second domain with checkable outcomes. Candidate default is a documentation corpus with offline source and link/anchor checks. Define admissible observations, explicit independence and scope using the same commitment and closure rules. | Put real work from that domain through the normal admission, evidence and acceptance path. Record at least one trace the owner can inspect and the domain’s genuine limitations. The proposed corpus can be evaluated from read-only local source snapshots. |
| M8-B · Assurance/operations worker | Maintain daily measures with denominators and reasons for bypasses. Rehearse an upgrade using the retained real M2 log and a current backup, original bytes/hash chain, supported upcaster, unavailable-version handling and rollback/forward recovery. | A cold restore and upgrade explain the same historical decisions at their original revisions. Field reports cover eligible/submitted tasks, caught false completions, avoided rework, approvals, overhead and bypasses. Disclose missing days or domains. |

**M8-G.** Assemble at least four weeks of complete field evidence spanning Purser development and the second domain. The human assesses usefulness and the orchestrator records continue, revise or stop with evidence and trade-offs. If distribution is requested later, resolve the package and command identity, install from the exact release artifact, and qualify upgrade/rollback as a separate release boundary.

## Invariant-to-gate index

| Invariant | Earliest meaningful proof | Later qualification |
| --- | --- | --- |
| I1 · Single write path | M1-C/M1-G, authenticated CLI through gate and projector | M6-A/B, OS isolation and MCP client |
| I2 · Replayable state | M1-A/B/G, cold SQLite rebuild | M4-C whole domain, M7-C restore, M8-B old history |
| I3 · No-model continuity | M2-C and M3-B subsets | M4-C/G all ten questions, M7-C restored answers |
| I4 · Claims and evidence | M2-A/B, unsupported claim and stale-candidate verdict | M6-C on a real agent’s submitted candidate |
| I5 · Independent observation | M2-A, verified actor, subject and trusted criterion | M6-A/C, actual isolated executor and observer |
| I6 · Derived closure | M2-A/C, parent coverage and version-bound root acceptance | M3-A invalidation races, M6-C real commitment |
| I7 · Explicit authority | M4-A/C, both dispatch/revocation orders and live expiry | M6-B session scope, M7-B human response provenance |
| I8 · Stable decisions | M3-A/B, opinion versus valid trigger | M4-G cold explanation, M8-B historical replay |
| I9 · Local invalidation | M3-A/B, mixed edges and unaffected sibling | M4-G restart from admitted history |
| I10 · No blind retry | M4-A/B/C, guarded Git plus interrupted broker | M6-C real branch, M7-A restart during uncertainty |
| I11 · Replaceable models | M2-C scripted adapter swap | M5-A provider boundary and M6-B/C fresh session |
| I12 · Stateless driver | M2-C restart and no-model import/call checks | M4-C mid-effect and M7-A idle/wakeup measurements |

## Research scorecard and evidence to retain

| Run | Published comparison | Independent ground truth |
| --- | --- | --- |
| E1 · False success | Purser has zero false closures in 20 and names the missing obligation in at least 19. If the original baseline catches at least 19, report no comparative advantage. | Inspect required changes and test outcomes at the exact candidate. Include true successful outcomes so always refusing closure is visible. |
| E2 · Changed premise | Unaffected subparts stay closed in at least 18 of 20. Purser’s rework is at most half the baseline’s and final pass rate is at least the baseline’s. No advantage if rework is not lower or final pass rate is lower. | Observe the two siblings, the genuinely affected work and the final fixture. Preserve counts for zero baseline rework and label an undefined ratio. |
| E3 · Ambiguous effect | Zero duplicate commits and 20 of 20 correct reconciliations. Any duplicate or a stuck state needing manual repair fails the original comparison criterion. | Independently inspect the dedicated fixture’s Git refs, receipt identity and recorded external ref transitions. Include both landed and unlanded outcomes. Report separately the stress case where evidence genuinely cannot establish what happened. |

Record steps and wall time for every arm. At M5 record calls, tokens and spend. Flag a happy path whose step count exceeds twice the plain baseline’s. The original comparative result and each absolute safety criterion get separate fields. The diagnostic equal-verifier arm never replaces the original baseline.

For each accepted gate keep one compact manifest containing the package/gate, identities of the implementer and independent verifier, design version, Git base and candidate, relevant OS/Python/SQLite/Git/adapter versions, fixture and scorer digests, seed or scenario, evaluation time, command, exit status, observed end state, retained artifact digests, known exclusions and final orchestrator disposition. Store worker evidence with its candidate. Large raw runs can live in a retained artifact directory referenced by hash. Never put a model credential, raw approval token or live secret into an event, prompt, fixture or review artifact.

At M0 establish stable evidence locations in the repository, for example `docs/decisions/`, `docs/evidence/M4/` for a gate manifest and `experiments/manifests/` for frozen inputs. Keep a compact package/dependency index until Purser can own the real work records at M2. Adopt actual project commands in each packet once the entry point exists. The intended baseline checks are Python’s unittest discovery, CLI init/status/why/replay against a disposable workspace, and a harness command accepting experiment, manifest, arm and output path. A proposed command becomes evidence only when the verifier records its invocation and output.

## Operating decisions that belong to later gates

| Deadline | Owner and decision | Safe progress available beforehand |
| --- | --- | --- |
| M0-G | Orchestrator selects the reference profile and scoped local rules, records known trust assumptions, confirms the package interfaces and assigns the single editor for shared contracts. | Both M0 packets use repository data, local fixtures and the v0.5 design. |
| Before M5 paid trials | Human authorizes provider, host, budget and permitted data. Orchestrator records model and run envelope. | Scripted E1–E3, adapter contract and report regeneration. |
| Before M6 qualification | Orchestrator chooses one real coding client and a runner that can enforce the stated boundary on the actual host. Human approves any additional access required. | Isolated local client/transport checks and contained fixture qualification. |
| Before M7 remote operation | Human selects reachable private approvals and an off-machine backup destination with suitable access and retention. Operations worker demonstrates restore. | Local pending-action semantics, backup API and separate local restore. |
| Before M8 study or public packaging | Human confirms a second real domain and access. Orchestrator chooses a collision-free distribution identity if a public release is intended. | Local module invocation and field instrumentation. The running study can finish independently of public distribution. |

## Copyable first assignments

**Orchestrator dispatch to the M0-A chat**

Read the v0.5 architecture and this plan from the exact base or supplied snapshots. Own M0-A. Return versioned proposal/event examples, clock and identity rules, and a worked trace covering late evidence and lost Git acknowledgment. Mark each field as authoritative or derived. Coordinate changes to externally scored outputs with M0-B through the orchestrator. Deliver this as a design packet for M0-G. Return the candidate, what a fresh verifier can derive and any operating decision that changes scope.

**Orchestrator dispatch to the M0-B chat**

Own M0-B from the same design version. Prepare the frozen experiment manifest, fault schedule, independent scorer contract and raw result shape for E1–E3. Preserve all published thresholds and the original baseline. Specify the equal-verifier arm, zero-work denominators, clean controls and cost accounting. Give the orchestrator three manually scored examples and their known answers. Keep scorer ownership independent of runtime implementation.

**Orchestrator brief for independent M0 review**

Use the contract and scorer candidates named in the assignment. Trace one correct closure, a stale pass, one unaffected sibling and a lost Git acknowledgment using only their durable inputs and independently observable state. Report each ambiguity with its invariant and downstream consumer. State which work may start and give the exact candidate for that judgment. The orchestrator owns the gate decision.

**Reusable worker return**

```text
Package and design version
Base, accepted prerequisite candidates and assigned write scope
Returned candidate or patch, and affected public contracts
Invariant, boundary, command and observed outcome
Independent evidence or remaining verification owner
Limitations, material decision, or next eligible dependent package
```

Start M0-A and M0-B when the orchestrator initiates delivery. Milestone status changes when the orchestrator has the corresponding implementation or design evidence.
