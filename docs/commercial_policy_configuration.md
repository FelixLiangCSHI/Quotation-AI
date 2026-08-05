# Commercial policy configuration

The commercial gate is deterministic. No AI model decides whether a quotation
passes, and no AI model may change any value in this document.

## The active policy version

| Field | Value |
| --- | --- |
| Policy version ID | `POL-MARGIN-MVP-001@1.0.0` |
| Policy name | Internal MVP Provisional Margin Policy |
| Status | `active` |
| Pass-margin threshold | `35.0` percent |
| Comparison operator | `greater_than` |
| Missing-cost policy | `block_on_missing_revenue_line_cost` |

Defined in `app/commercial_policy.py` as `INTERNAL_MVP_PROVISIONAL_POLICY`.
Nothing else in the codebase hard-codes the number 35; every decision, screen,
test and status endpoint reads it from the policy object.

> **The 35% threshold is provisional.**
> It is an active internal-MVP assumption chosen so the workflow can be tested
> end to end. It has **not** been confirmed as a permanent company rule, and it
> must never be described to a customer, a partner or an auditor as approved
> company policy. It never appears in any customer-facing output.

## The decision table

Margin is calculated at **quotation level**: total revenue against total cost
across every non-optional revenue line. It is never the average of the
individual line margins.

| Condition | Deterministic decision | What a person may then do |
| --- | --- | --- |
| Quotation gross margin **> 35%** | `PASS` | Normal human approval |
| Quotation gross margin **exactly 35%** | `REVIEW_REQUIRED` | Approve **with override** only |
| Quotation gross margin **< 35%** | `REVIEW_REQUIRED` | Approve **with override** only |
| Technical configuration incompatible | `BLOCKED` | Request revision only |
| Trusted material cost unavailable | `BLOCKED` | Request revision only |

Because the operator is `greater_than`, a margin of exactly 35.00% does **not**
pass. The normal `approve` action is not offered at all in that case.

`PASS` is a *decision*, not an approval. A quotation is only approved when a
person with the right permission records an approval action. A `PASS`
quotation with no human approval produces no customer PDF and no customer
email.

## Missing cost is never zero

When any non-optional revenue line has no trusted cost, the quotation margin is
reported as unavailable and the decision is `BLOCKED`. The system does not
substitute zero, does not interpolate and does not fall back to a list price.
A blocked quotation cannot be approved by any route, including override.

## Changing the policy

1. Only an administrator or a pricing manager holds `manage_policy_versions`.
2. A change is a **new policy version**, never an edit of the active one.
   History must remain reconstructable for every past approval.
3. Every decision record stores the `policy_version_id` that produced it. An
   approval task raised under an older policy version becomes stale and must be
   re-evaluated before it can be approved.
4. After changing the policy, confirm the new values on `/status` or via
   `python -m app.operations.cli` and re-run the margin-gate tests.

## Where the threshold may and may not appear

| Surface | Threshold visible? |
| --- | --- |
| Approval inbox for a pricing manager | Yes |
| Approval-request email to a pricing manager | Yes |
| Approval inbox for a sales manager | No (margin detail withheld) |
| Audit trail and internal exports | Yes |
| `/status` operational endpoint | Yes |
| Customer PDF | **Never** |
| Customer email | **Never** |
| Customer JSON export | **Never** |

The customer boundary is enforced in code, not by convention: see
`app/documents/plan.py` (`CUSTOMER_FORBIDDEN_TERMS`),
`app/documents/renderer.py` (`_assert_customer_safe`) and
`app/config.py` (`CUSTOMER_PROHIBITED_FIELDS`).
