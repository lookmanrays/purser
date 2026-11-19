# M5-A local executor verification

The model adapter worker exercised source candidate `0d8894dbe1b40fb00628027481781427381823b8` on 19 November 2026 at 05:53 UTC. The accepted M4 ledger, any actual model service and official model registrations were outside this component control. All HTTP requests went to ephemeral synthetic services at literal IPv4 loopback and all ledgers and candidates belonged to disposable fixtures.

The base for integration was `68f4407`. Preflight `360af4b` preceded the first implementation. The first independent review concerned `789fa07`. The review findings, their class audit and the pre-edit remediation decision are in `5a1f604`. The replacement source and regression tests are together in `0d8894d`. [Design and preflight](model-preflight.md) · [Review and remediation record](model-remediation.md)

## Normal foreground execution

A disposable registered executor received a real assignment from `purser.driver.tick` and sent a POST to the selected native listener. Its trusted fixture consumer wrote the proposed UTF-8 source to a private policy artifact and returned that candidate to the kernel. A second fresh fixture received a different reply to the same prepared HTTP body. Each store advanced from revision 14 to 17. Replay returned the exact candidate bytes for both. The service counted one request per fixture. The private commitment statement and sibling record stayed outside the HTTP request.

| Observed value | First reply | Second reply |
| --- | --- | --- |
| Candidate bytes | 86 | 52 |
| Candidate SHA-256 | `7e83c7da1310a482567e04440c2b394e1cdfe4222103d504416d0671e3fe59d4` | `cbdf96a79f3a2dc6b6f1fdf0d319c449bac5341660b474118dcb155e8dc3d1b8` |
| Original HTTP response SHA-256 | `f2a6214cb365f5b51daa3834044ecb22cfd481f1c50f309dce83c1d6369f7c4c` | `89d0751f8ff9df19a2fa501f1bc756e81125ee554eba166afe05e0dd4f4d5cb2` |
| Synthetic service usage, input / output | 696 / 156 | 696 / 121 |
| Local service charge, USD | 0 | 0 |

The common POST body had SHA-256 `5f33bcab1a3653dc7b3d46e10debdca18b5dc9340df756cf59af641bfb1dd78b`. The controlled service used a documented byte-token renderer covering the beginning marker, both role tags, both message bodies, the end markers and the assistant prefix. Each release reserved its full 8,000-token context window. A private result recorded the selected service, runtime digest, provider and model identifiers when supplied, parameters, exact request, original bounded response or prefix, parsed proposal, timing, measured amounts and disclosure gaps. The offline verifier reconstructed the typed result from the original response. Development fixtures were removed after their assertions.

## Recovery, refusal and limits

| Boundary exercised | Observed outcome |
| --- | --- |
| Process killed after the service receives the request and before a response is captured | A new foreground tick reached the assignment again. The original turn stayed unresolved, the two dispatch receipts advanced the ledger from revision 14 to 16, the candidate stayed absent and the physical request count stayed at one. The full 8,000 input and 1,500 output reservations survived with unknown actuals. |
| Process killed with a complete deterministic turn file or with the addressed copy already written, before budget settlement | At each cut the original settlement was absent. A fresh adapter and normal foreground tick verified the original bytes, restored the content-addressed result, settled the observed usage and submitted the matching candidate. The service counted one request at each cut. |
| First prototype's addressed result with a missing turn link; settled result missing its addressed copy | The recovery path found the original by registration, case, ordinal, authority and prepared request. The deterministic source restored a missing addressed copy. Each case returned its original result without a new socket call. |
| Complete HTTP 400 with diagnostic and 17 input / 0 output; truncated body carrying identical parseable bytes; complete malformed HTTP 503 body | All three retained their available response bytes and produced incomplete results. The complete JSON error retained its 17 / 0 disclosure. Both token fields stayed unknown in the other two. Cold replay reproduced each original seal and the server counted each request once. |
| Disabled service, wrong assignment, absent listener, replacement, idle and replay | Disabled and out-of-assignment calls opened no socket or evidence directory. The unavailable route recorded its failure and unknown usage. A scripted replacement used the same driver contract. A fresh driver import left the model adapter unloaded. |
| Slow status/header and body delivery, oversized response, redirect and ambient proxy | The original absolute socket deadline terminated both drips. The oversized response produced a framing failure. A 307 and process-level proxy variables produced zero requests at a second native listener. |
| Typed operands, actual allowance, missing usage and overrun | An unoffered path, duplicate action, excessive UTF-8 source, incorrect model, invalid token field and truncated model decision each yielded no proposal. An undisclosed large-window call retained the full charge against the case token cap. An observed token overrun preserved its amount and inhibited the next unit through the shared authority. |

The focused transport and interruption tests, adjacent budget and cost tests, and existing foreground driver tests ran against the clean committed source. All 28 passed in 1.776 seconds, exit 0. The host was macOS 26.5.2 build 25F84 on arm64, Python 3.11.15, SQLite 3.53.1 and Git 2.50.1. The frozen experiment manifest had SHA-256 `1c51ce44eb1887bc5cf08ee98afd7714faa7b23cf8f9b62c998c128f9ce54b73` and the protocol had SHA-256 `5722774bffd56969b97c1575b2776cd7b9b3536da99dc1b4e49976be4835b2eb`.

```sh
python3.11 -B -m unittest -q tests.test_model_adapter tests.test_model_adapter_restart tests.test_model_budget tests.test_model_costs tests.test_driver
```

The demonstrated boundary is a replaceable, credential-free local executor with synthetic model responses. The study collector owns the 96 public actions and original 900-second case clock, and passes the remaining absolute deadline to the adapter. A genuine service requires its actual approved identity, runtime, enforced context window, output limit and accepted data policy. Live model causality, isolated evaluation of its source, the real study population and the human v1 decision belong to their M5 evidence.
