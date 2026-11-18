# M0 · Design accepted

18 November 2026. The orchestrator accepts the design and preregistration at integrated candidate `4460381e44f3dd42d22848bf6d82fea9a817a833` on `lookman/purser-development`. Planning base `122b729bb5e549383dec72d2065b242cfbe5d9d5`. M0-A author candidate `3bbda326ce3f8a76098b9c7a120a8435d773faef`, M0-B author candidate `2a1e59a3101a5d5cf45e48b3c5d48543b45c77f8`. The independent review worker used a separate detached checkout and returned GO on the combined SHA. The orchestrator adopted the v0.5 working assumptions for this profile with [D-30](../../decisions/0030-principal-approval.md). Provider access, budget, runner containment, backup destination and the field domain are decisions at their named later gates.

## Contract and recovery evidence

The public [wire contract](../../contracts/kernel-v1.md), [canonical vectors](../../contracts/wire-v1.json) and [semantic trace](../../contracts/lifecycle-v1.json) are the M1–M4 implementation inputs. On Python 3.11.15, `python docs/contracts/check_vectors.py` passed all three reference checks. Independent folding reproduced revisions 0, 2, 4 and 8, the original legacy bytes and projection, proposal dispositions, five outcome digests and the protected Git receipt identity. The latest primary head was `10aa8c962d2966b2fc22d4f8e131268d0ee727a87cf11f2027ce182e5316eae5`. The legacy chain ended at `5892629d4ed7c1a89b777f7b4656fbe291401eb3ed06d6cbc44998a719111fa0`.

The independent trace kept W1 and W3 valid while A1, D-original and W2 were affected. A late pass for A did not close B. Root acceptance waited for the exact outcome. Both revocation orders, a quiescent unlanded writer, an advanced branch with a valid receipt, and residual uncertainty were distinguishable at the public contract.

## Experiment evidence

The [preregistration](../../../experiments/manifests/v1/protocol.md) preserves all twelve original intervention, measure and threshold fields. There are 20 paired seeds per experiment and three separately identified arms, giving 180 planned primary run keys. Clean and stress tracks have their own identities.

The reviewer regenerated the fixture starting commit `55686bd5152e10a53dfdc2c003974ea405a3a075`, tree `2afea0fdd3478d29447a40bde8007266c468c72b`, and the prepared E3 commit identified in the manifest. Each of the two candidate fixtures passed all 28 public tests. Independent checks exercised all 20 parameter sets through the real notification/scheduler path and reproduced the E1/1003, E2/2001 and E3/3002 hand scores. The reviewer compiled the raw schema and checked 18 positive/negative hand-row cases. The orchestrator independently rebuilt the start tree and commit from the listed bytes, checked the twelve original fields and ran the public compatibility suite.

| Frozen input | SHA-256 |
| --- | --- |
| Experiment manifest | `1c51ce44eb1887bc5cf08ee98afd7714faa7b23cf8f9b62c998c128f9ce54b73` |
| Protocol | `5722774bffd56969b97c1575b2776cd7b9b3536da99dc1b4e49976be4835b2eb` |
| Raw-result schema | `7886a6f9c2027281d51dd72ee21e093a4d06a7c3479f07e0efe55d0e2465eb1b` |
| Constructed scoring examples | `69399778226f413e97a804fb9d38b9767ccaaacdaeb236a9d024badcde16d45b` |

M0 evidence qualifies the design, fixture and protocol. Product code, experimental results, human runtime acceptance and actual provider/host isolation are exercised at later gates.
