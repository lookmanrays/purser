# Contract format v1 (draft)

Status: draft input to P0-04, which turns it into `contract-v1.schema.json` and a validator. Every file under `contracts/kernel/` and `contracts/graph/` uses this format and must validate once P0-04 is done.

## Top-level keys

| Key | Required | Meaning |
| --- | --- | --- |
| `contract` | yes | Unique dotted ID, for example `kernel.gate`. |
| `kind` | yes | `module`, `function`, `data`, `endpoint`, `event`, `ui` or `cli`. |
| `version` | yes | Positive integer. A change to an approved contract increments it. |
| `status` | yes | `draft`, `approved` or `superseded`. |
| `package` | module and function | Go import path relative to the module root, for example `internal/kernel/gate`. |
| `layer` | module | `kernel`, `effects`, `graph`, `plan`, `run`, `adapter`, `cli` or `tools`. |
| `allowed_imports` | module | Internal packages this package may import. depguard enforces the list. |
| `owner` | yes | The package brief that implements it, for example `P1-03`. |
| `summary` | yes | One or two sentences. |
| `spec_refs` | no | Frozen reference documents with anchors. |
| `vectors` | no | Vector files the tests must pass. |
| `interface` | module and function | Exact Go declarations under the key `go`. |
| `functions` | no | Behaviour per exported function: `name`, `pre`, `post`, `errors`, `examples`. |
| `invariants` | no | Properties that hold across all calls. |
| `errors` | no | Typed error codes and when each is returned. |
| `data` | data | Field table: `name`, `type`, `required`, `rule`. |
| `request`, `responses` | endpoint and cli | Shapes, or a reference to an OpenAPI or JSON Schema file. |
| `states` | ui | Required component states. |
| `reuse` | no | Existing symbols the implementation must call instead of re-implementing. |
| `non_goals` | no | Behaviour that must not appear. |

## Rules

- Function contracts list at least one example or one vector reference.
- `errors` codes are lowercase snake case and stable across versions.
- Contract digest is the SHA-256 of the file's exact bytes. The kernel records it when a contract is registered (P3-03).
- A contract with `status: draft` may change during its planning batch. Approved contracts change only through a new version.

## Example

```yaml
contract: billing.ApplyCredit
kind: function
version: 1
status: draft
package: internal/billing
owner: P9-01
summary: Applies one credit to an invoice without letting the total go below zero.
interface:
  go: |
    func ApplyCredit(ctx context.Context, inv Invoice, c Credit) (Invoice, error)
functions:
  - name: ApplyCredit
    pre: ["c.Currency == inv.Currency", "c.ExpiresAt > now"]
    post: ["result.Total == max(0, inv.Total - c.Amount)", "c.ID in result.AppliedCredits"]
    errors: [credit_expired, currency_mismatch]
    examples:
      - {given: {inv_total: "100.00 EUR", credit: "30.00 EUR"}, then: {total: "70.00 EUR"}}
      - {given: {inv_total: "20.00 EUR", credit: "30.00 EUR"}, then: {total: "0.00 EUR"}}
reuse: [money.Sub]
```
