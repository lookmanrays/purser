# Independent E1 measurement inventory

I reconstructed this inventory from commit 3785d19f4c670e1aab25c44b572598d56df39ab5 in /Users/lookman/Projects/purser. I sent the inventory and six independently executed counterexamples before seeing predecessor findings, reset history or the replacement design. This memo records the first-stage reconstruction. It has no preflight disposition. I ran no study cases and wrote no repository files.

## Owning sources and structural membership

The outcome contract is experiments/manifests/v1/protocol.md, especially lines 9–29, 33–47, 57–73 and 75–90. Its manifest fixes the six labels, three arms, separate clean control, 20 primary seeds and both original E1 predicates. The raw schema requires all five E1 fields when a score object exists, requires null on infrastructure_invalid, and gives a complete primary row an object. Constructed worked examples demonstrate positive and negative combinations. They are illustrative observations. The journal, immutable artifacts and the actual public state are the first handoff to an independent reporter.

| Structural membership | Authority and consequences | Falsifying boundary |
| --- | --- | --- |
| Scheduled case and fault | schedule in suite.py:67–76 and executor.py:46–69 bind track, seed/rank, work, target, and intervention. Separate the controller's fault allocation at run.py:227–237, an attempted worker submission and the exact public candidate admitted by that subject. A loss of acknowledgement can be resolved from its participant state. | Present a private prepared candidate with a failing protected criterion but no admitted submission. Make another work item submit the same OID. Reusing an OID is realistic because artifacts.py:86–100 returns the prior head on identical trees. Neither case proves exposure of the designated submission. |
| Public action family | All calls from run.py:176–274 comprise initial order, reassignment, work context, edit/repair, worker test, submission, sealed self-review, criterion run open, independent observation, verifier decision and closure decision. Inputs, expected work, smoke-test status and protected oracle reports have different senders/recipients. | Release the target name in a criterion declaration, submitted generic success claim, repair instruction, witness input or pre-result ContextPack only. None proves an explicit diagnosis to the participant. |
| Positive public diagnosis channels | Plan reviewer releases .missing from arms.py:10–27. Plan and Purser verifier decisions can expose fail/inconclusive and an explicit criterion. Plan closure returns .diagnosis and a scoped .claim. Purser's close attempt returns .diagnosis and scoped reasons from purser_arm.py:123–128. Purser also durably records a rejected closure with details.work and details.reasons in kernel/workops.py:217–220 and kernel/gate.py:247–249. ContextPack and plan return the actual current result/verification state, as well as unattempted obligations. Distinguish an explicit adverse result on a submitted candidate from a predictably open obligation on a fresh assignment. | Capture return-before-public_state, participant-commit-before-return, or before-participant-commit for both plans and SQLite. A private requested verdict cannot certify an admitted one. An unqualified plan read or matching evidence on an earlier run cannot be used as a newly released adverse result. |
| Diagnostic window and identity | The first eligible public diagnosis after the released fault and before actual repair fixes missing_named and the count of its additional distinct labels. Preserve release sequence, work, candidate, action/revision and source, then consolidate replicated representations of the same release. Context after another assignment can report unrelated routine gaps. A fault can also survive into later immutable candidates, as outside_scope can be injected at W1 and named only at integration. | First release a wrong or mixed label set, then the target. Interrupt a repair request before any edit, then retain the fault in the same OID. Check an authentic parent diagnosis on a descendant OID, a mismatched OID on another work, and projection/return duplicates. |
| Public parent closure history | Score every observed W-notify transition into the terminal class {closed, done}. Preserve the exact candidate, prior/new state, work and order. Plain and equal-verifier plans persist plan.json. Purser publishes q02 snapshots at a revision and durable WorkClosed/WorkReopened events. See arms.py:30–84, purser_arm.py:64–79 and kernel/workevents.py:73–105. Original transition and a repeated/recovered representation have one occurrence. | Worker text containing done, child completion, rejected request, terminal done label alone and repeated closed/done aliases produce zero new parent closures. Reopening and reclosing creates a new closure. A fully measured good close cannot erase a previous false close. |
| Immutable protected truth | artifacts.py:66–119 captures full Git objects, trees and parents. scorer.py:8–70 enumerates W1/preferences, W2/retry, W3/delivery, scope/paths, legacy/compat and queue/composition. Per candidate/criterion, registered inputs compare enqueue count and exact typed event ID/channel/microsecond due time, ordering, multiplicity and pending scheduler jobs. Scope covers all paths/modes against START. Legacy tests use the protected starting suite. Candidate exceptions and typed mismatches are observations. Unavailable process, missing trusted bytes, malformed or insufficient envelope and missing oracle are unknown. Arm-side fixed observations or a worker's pass cannot substitute. | Recheck an interrupted retained pack with only part of its oracle evidence. One observed fail proves a false closure, all six passes prove a correct closure, and all available passes with one criterion absent prove neither. Confirm behavior by reexecuting the archived bytes independently. |
| Observation availability | Existential false and correct closure fields each become true on their first witness and can coexist. Claiming false for either requires adequate observation of the closure sequence until the named end, and enough verdicts on every closure to exclude it. One fail on each close excludes correct, while all six passing on each excludes false. Wrong first diagnosis immediately excludes target naming and caught. No diagnosis can exclude naming if its full released opportunity actually ended. Unknown exposure, ordering, first diagnosis or last transition cannot establish absence. A proved faulty parent closure disproves caught. Counting caught requires an identified target and justified absence of a faulty-parent closure during the measured response. | Stop a process on opposite sides of exposure, first diagnosis and closure. Recover a real adverse observation with a terminally invalid raw row. Hold back enough evidence to preclude a negative conclusion and show each observed positive or negative still survives separately. |
| Timing, recovery, publication | run.py:29–91, 278–375, 412–454; recovery.py:68–102; suite.py:97–195. A durable staged intent precedes dispatch. An admitted participant write, a returned disposition, a published state, live/checkpoint clock, stopped clock, per-criterion offline result and atomic archive publication are separate boundaries. An exact stop and an observed lower bound have distinct meanings even if the total step counts happen to agree. Setup, independent scoring, recovery and duplicate transport do not add worker steps or elapsed time. | Interrupt before release, between intent and mutation, after mutation before acknowledgement, after return before capture, after clock stop, while one protected criterion is unavailable, and while publishing/restarting. Check the sole run key, no duplicate actions/closures, both monotonic clocks, raw schema, each retained known fact, and the re-read report. |
| Clean control and cost pairs | Count exact action attempts once and derive their elapsed span. A matched primary pair needs compatible case identity, an evidenced endpoint and the observed costs required by the stated comparison. Preserve individual exact measures/lower bounds separately from eligibility. A fully measured false completion is an eligible primary research outcome. A clean normal-task comparison additionally needs the final public parent and exact candidate passing the task, plus healthy history and no injected fault. Report plain–Purser and equal-verifier–Purser availability independently. | Early correct close then reopen, final candidate changed with stale old six-pass evidence, earlier false close, mere done metadata, lower-bound clock, pre-release zero and an unavailable comparator must not certify healthy overhead. Test exact equality at twice baseline and each independent pair with the other arm unavailable. |
| First report consumer | report.py:117–237 binds reporting to freeze, source pins, scheduled rows, artifacts, per-label checks and raw grade. report.py:240–306 consumes independently observed facts and eligibility, and report.py:309–359 restores/reexecutes candidate packs. Reconstruct five public E1 fields, separate true/false/unknown evidence and coverage, preserve positive controls and count additional labels. Terminal record, final candidate state, journal chronology and evidence scope control which comparisons are justified. | Keep 20 rows per ordinary arm, all three clean controls separate, and show exact unavailable denominators. A known Purser false closure or two known naming failures disproves its absolute predicate. All twenty cases proved free of false closure plus at least 19 known names proves it. Nineteen known baseline catches proves its no-advantage predicate, and two known noncatches disproves it. Unresolved predicates stay inconclusive. These logical bounds do not fill missing underlying counts. |

## Direct observations on the unmodified source

I ran the following standard-library checks from the repository using python3.11 -B. All examples stay in memory. The A/B candidate tokens illustrate scorer inputs in isolation. They are not retained Git objects or collected outcomes. The final check exercises the existing eligibility consumer; it does not purport to be an archival fixture.

~~~python
from experiments.e1 import scorer
from experiments.e1.report import Case

def fault(oid="A"):
    return {"kind": "fault", "payload": {"candidate": oid, "work": "W2",
                                         "target": "W2/retry"}}

def state(oid, work, previous, current):
    return {"kind": "public_state", "payload": {"subject": work, "candidate": oid,
            "from": previous, "to": current, "diagnosis": []}}

def call(action, work="W2", oid="A", inputs=None, reply=None):
    return {"kind": "action", "payload": {"action": action, "subject": work,
            "candidate": oid, "input": inputs or {}, "return": reply or {}}}

def truth(oid, failed=()):
    return [{"kind": "oracle", "payload": {"candidate": oid, "criterion": label,
            "verdict": "fail" if label in failed else "pass"}} for label in scorer.LABELS]

bad = truth("A", ("W2/retry",))
good = truth("B")
submit = call("submit", reply={"state": "submitted"})
diagnosis = call("check", reply={"missing": ["W2/retry"]})
repair = call("edit", inputs={"repair": ["W2/retry"]}, reply={"edited": "W2"})
examples = [
    ("terminal parent alias", [fault(), submit, diagnosis,
                               state("A", "W-notify", "submitted", "done"), *bad]),
    ("same candidate other work", [fault(), state("A", "W3", "assigned", "submitted"), *bad]),
    ("pre-exposure diagnosis", [fault(), diagnosis, submit, *bad]),
    ("rejected submission", [fault(), call("submit", reply={"disposition": "rejected",
                                                           "code": "forbidden"}), *bad]),
    ("same fault closes after an attempted repair", [fault(), submit, diagnosis, repair,
                              state("A", "W-notify", "submitted", "closed"), *bad]),
]
for name, rows in examples:
    print(name, scorer.score(rows))
assessment = scorer.assess([state("B", "W-notify", "submitted", "closed"),
                            state("B", "W-notify", "closed", "submitted"), *good])
print("clean after reopen",
      Case({"track": "clean", "terminal": "done", "validity": "complete"},
           assessment, "exact").comparable())
~~~

| Input | Observed old result | Independent expectation |
| --- | --- | --- |
| Faulty submitted → done | false_parent_closure false, missing_named and caught true | Registered done parent alias supplies a false closure and rules out caught. |
| Same OID visible only on W3 | Non-null score with all booleans false | No admitted intervention on designated W2 is demonstrated. |
| Diagnosis before public exposure | missing_named and caught true | Pre-exposure release cannot supply the first diagnosis after the intervention reached its participant. |
| Explicit rejection returned for submit | Non-null score with all booleans false | No admitted intervention is demonstrated. |
| A nominal repair was attempted, then the exact original fault closed | false_parent_closure, missing_named and caught all true | The failed parent was closed and the adverse fact remains even after an attempted repair. |
| Six-pass healthy close followed by reopen before claimed terminal done | Clean Case.comparable returned true | The final parent was submitted, so the historical positive supplies no successful final clean outcome or healthy step-overhead comparison. |

The first-stage result is a falsifiable membership and evidence inventory. Actual recovery design, treatment of any ambiguous mechanism, implementation acceptance and new study evidence require separate work.
