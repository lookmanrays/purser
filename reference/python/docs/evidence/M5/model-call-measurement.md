# M5 model-service request measurement

## Preflight

The independent E3 control at `62d812335c1803f46c3cc88900b6f8aa4b229650` kept a loopback port bound without listening. The public journal recorded context and one decision attempt, the adapter sealed a `not_sent` receipt, and the cold raw reader counted one model call. Its original evidence file has SHA-256 `a70b04902f33570c810ffa29f665c5d16030c377a14bf101a4d4fa2d79fc01ba`. The E3 component starts from `38f48ccff834572e9e45df924575d8283176cd01`.

The R2 invariant is that each ordinal contributes the number of complete outgoing requests its original transport can establish. A `not_sent` receipt contributes zero calls, tokens and service spend. A `sent` receipt contributes one call, including a returned HTTP error. Only the verified usage evidence supplies token counts and service charges for a possible send. An interrupted send or an unresolved release contributes a lower bound of zero calls. Known counts from other ordinals survive. The public journal owns its decision step, the reservation owns its authorization, and the accepted model decision requires its own original evidence.

The structural audit follows the three states of `Exchange.post`, the pre-transport refusals, the missing or recovered result paths, and the cold readers of the registered raw cost. Both E3 and the common study reader at `6306e58d` increment every sealed receipt. The common reader can also lose uncertainty for a missing original when another ordinal has known usage. E3 already checks the actual public decisions and the reservation token envelope independently. Scripted records carry their registered zeros. The report renders field-specific exact totals and lower bounds. Both study readers can call one pure projection in `experiments.model_costs`; the study worker owns its call site.

This is the first finding in the measurement cluster. The earlier adapter remediation concerned original byte capture and cold recovery. Here the surviving original already proves the defect in its consumers. The existing adapter and budget evidence suffice, and the packet requires no change to their persisted contracts. Operating feasibility passes on the local Python 3.11 and Git host with disposable loopback services and temporary study databases. The contract, module ownership and cold-read lenses apply.

Exercise a bound unlistened port, a successful request, a returned HTTP error, an interrupted or unresolved transfer, a mixed ordinal sequence and an offline read. Compare raw counts, per-field bounds, public decision attempts, accepted decisions and retained reservation measurements. The study lead owns the joined collector check. All component controls use canned local responses.

## Local qualification

Source `e4dda848f6729bc54ce79b3e243e0f0c721a80bc` passed 45 tests across model costs, E3, the model adapter, restart, budget and the E3 report under Python 3.11.15. A fresh Python process reopened four private E3 archives using their registered service and authority. It checked the raw schema, reconstructed the public steps and checked each complete outgoing body digest against the loopback server's received body. The cold read recorded zero network operations.

| Canned service control | Public steps | Sealed receipts | Outgoing requests | Accepted choices | Input tokens | Output tokens |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Bound port without a listener | 2 | 1 | 0 | 0 | 0 | 0 |
| Four successful replies | 7 | 4 | 4 | 4 | 124 | 68 |
| First reply HTTP 422 | 2 | 1 | 1 | 0 | 7 | Unknown |
| HTTP 200 followed by HTTP 422 | 4 | 2 | 2 | 1 | 38 | At least 17 |

The two public steps for the unlistened port were context and decide. Its original reservation retained the 4,096-input and 512-output token ceilings with null settlement counters. The source projected zero spend for that impossible send. Each received HTTP reply supplied the enrolled local service's zero charge. A separate native request completed its send and timed out before an HTTP reply. Its receipt established one outgoing request with all usage and charge fields unknown.

A test socket split the client's native write at 1,024 wire bytes. The loopback peer read 863 of the 60,658 body bytes and reset the connection. The adapter sealed `send_started`. The cold reader retained a zero lower bound on calls and unknown usage. The process-loss control in the same test suite retained an earlier confirmed request when the next ordinal's original was missing.

The older independent case belongs to a trace format that predates the separately retained public decision request. Its pinned `62d8123` reader reproduced the published one-call result. The current verifier and shared measurement read the same sealed transport bytes and established zero calls, tokens and spend. The older raw and receipt retained their original digests. A current-format raw with its zero changed to one failed the full E3 cold check.

The private archives, local server witnesses and result are under `~/.local/state/purser/development/m5/call-measurement-e4dda84/`. The SHA-256 of `qualified/cold.json` is `8cea18dcc25e429de0a64c323cbd78c85196c6fad33b1dd0505b99d1a62ee36d`. These controls qualify the shared projection and the local E3 reader.
