# D-30 · Principal approval and remote delivery

Adopted for M0 on 18 November 2026 by the project orchestrator following the principal’s clarification in the project chat. This is amendment 1 to architecture and implementation plan v0.5 at `122b729bb5e549383dec72d2065b242cfbe5d9d5`.

A Purser approval request survives a restart. Only an authenticated human principal may approve the exact current subject, scope and version. An approval can be given through the local principal channel. An expired request, stale version, mismatched identity or repeated response cannot authorize a second action.

M7-B qualifies that behavior with a real human round trip on the selected channel, including a pending request across restart. The M7 gate still requires a measured week of unattended operation and an independently verified off-machine restore. Remote notification and response are an optional transport. If the principal later chooses a remote channel, qualify its identity, delivery, redelivery and response binding before enabling it. A local-only installation can qualify the mandatory approval behavior.

This decision supersedes the remote-delivery requirement in v0.5’s scope table, M7 runtime and milestone rows, and M7-B/M7-G plan. It applies only to the transport. The principal’s authority and the narrowly scoped requirements for any action remain in force.
