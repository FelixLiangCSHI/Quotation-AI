# Internal MVP — Approver guide

This guide is for a **Sales Manager** or **Pricing Manager** who approves
quotations.

## The provisional margin rule

- Quotation-level gross margin **greater than 35%** passes the margin gate.
- Margin **equal to or below 35%** requires human approval with an override.
- **The 35% threshold is a provisional internal-MVP assumption**, not an
  approved permanent company rule. It is never shown to a customer.

Margin is calculated across the whole quotation — total revenue against total
cost — not as an average of the line margins.

## What a decision means

| Decision | Actions available to you |
| --- | --- |
| `PASS` | `approve`, `request_revision`, `reject` |
| `REVIEW_REQUIRED` | `approve_with_override`, `request_revision`, `reject` |
| `BLOCKED` | `request_revision`, `reject` |

Three things follow from this table:

1. **PASS is not an approval.** The deterministic gate found no objection; your
   confirmation is still required, and nothing goes to the customer without it.
2. **At exactly 35% the normal approve action does not exist.** It is not
   hidden or discouraged — the system will refuse it. Use approve-with-override.
3. **A blocked quotation cannot be approved.** Neither approve nor
   approve-with-override is available. Request a revision instead.

## What you can see

Your inbox shows the quotation reference and version, the customer, the full
line-item breakdown, the currency, the technical validation status, any data
quality flags, the deterministic decision, the triggered rule IDs, the policy
version and the AI explanation with its provenance label.

Commercial detail — total cost, gross margin and the policy threshold — is
shown only to a **Pricing Manager**, who holds `view_commercial_detail`. A
Sales Manager sees the decision and the line items but not the margin figures.
The approval-request email follows the same rule.

## Approving with an override

When you approve with an override the system requires two things and will
refuse the action without them:

1. A **written justification**. A token word is not enough; the reason is
   stored permanently in the audit trail and attributed to you.
2. An explicit **acknowledgement** that the quotation margin is at or below the
   configured policy threshold.

The override justification is internal. It never appears in the customer PDF or
the customer email.

## Requesting a revision

A revision request needs a written reason, which is shown to the sales user.
The quotation returns to the sales user, the approval is dropped and pricing
and validation must be re-run. Your original task remains in the audit trail.

## Stale tasks

An approval task becomes stale and cannot be actioned when:

- the quotation was materially edited after submission;
- the quotation version has moved on;
- the pricing or validation run behind the decision is no longer current;
- the commercial policy version changed after the decision was taken.

If you open a stale task the system explains why and offers no approval action.
Ask the sales user to re-run pricing and resubmit.

## Concurrent approval

If two people open the same task and both act, only the first action succeeds.
The second receives a clear "this task is already completed" error and no
second approval is recorded. The same applies to the same person acting twice
from two browser sessions.

## Reminders

If a task stays pending for two calendar days you receive exactly one reminder
email. The reminder worker runs outside the web application, so it fires even
if nobody has the app open, and a repeated worker run does not send a duplicate.

## After you approve

The sales user can then generate the customer PDF and send the customer email.
Both are built from the exact quotation version you approved and are linked to
your approval action in the document register. If the quotation is edited
afterwards, the document is superseded automatically and can no longer be sent.

## Audit

Every action you take — approve, approve with override, request revision,
reject — is recorded with your identity, your role, the timestamp, the
quotation version, the decision and the policy version. Document generation,
document download and document supersession are recorded too.
