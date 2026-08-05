# Internal MVP — Sales user guide

This guide covers the day-to-day quotation workflow for a **Sales User**.

## The provisional margin rule

Before anything else, understand the commercial gate:

> **Current provisional margin rule**
>
> - A quotation-level gross margin **greater than 35%** passes the margin gate.
> - A margin **equal to or below 35%** requires human approval.
> - **This is an MVP assumption pending formal business confirmation.** It is
>   not an approved permanent company rule.

Two further points matter in practice:

- **PASS is not an approval.** A PASS decision means the deterministic gate
  found no commercial objection. Your quotation still has to be approved by a
  person before anything reaches the customer.
- **Exactly 35% does not pass.** At exactly 35% the normal approve button is
  not available to your approver; they must use approve-with-override and write
  a justification.

## Signing in

Open the application and sign in with the account an administrator created for
you. You cannot choose your own role. Your permissions as a Sales User are:
create a quotation, edit your own draft, run pricing, run validation, submit
for approval, respond to a revision request and view your own quotations.

After five failed sign-in attempts in fifteen minutes the account is
temporarily locked. Wait for the window to pass or ask an administrator.

## Building a quotation

1. **Describe the requirement.** Use the conversational entry or the structured
   requirement form. Both feed the same validation and merge logic. If Agent 1
   extracts a field with low confidence you must confirm it explicitly.
2. **Add line items.** A quotation supports multiple lines: main products,
   accessories, installation, warranty, service and other commercial lines.
   Each line has its own quantity and price. Every product line passes a
   compatibility check before it can be added; incompatible combinations are
   labelled and cannot be quoted.
3. **Run pricing.** Pricing reads the *active* pricing data version, which was
   imported from an offline SAP Excel export by an administrator. If a line has
   no trusted cost, pricing will say so rather than assume a value.
4. **Review the analysis.** Agent 2 may add commentary, but the numbers are
   deterministic. Agent commentary can never change a price, a cost or a
   decision.

## Understanding the decision

| Decision | What it means | What happens next |
| --- | --- | --- |
| `PASS` | Margin above 35%, configuration valid | Submit for normal approval |
| `REVIEW_REQUIRED` | Margin at or below 35% | Submit; approver must override |
| `BLOCKED` | Incompatible configuration, or no trusted cost | Fix the quotation; it cannot be approved |

A `BLOCKED` quotation cannot be approved by anyone through any route. Correct
the configuration or ask an administrator about the missing cost data.

## Submitting for approval

Choose an approver from the list of people who hold approval permission, then
submit. Submission:

- creates a persistent approval task that survives an application restart;
- records an audit event;
- schedules a reminder for the approver two days later;
- optionally sends the approval-request email immediately (configurable).

You cannot approve your own quotation.

## Material edits after submission

If you change anything material — a product, a quantity, a price, the currency,
the Incoterm or the delivery location — the system will:

1. increment the quotation version;
2. cancel the open approval task as **stale**;
3. drop the approval;
4. clear the pricing run, the margin analysis and the decision;
5. **supersede any customer document already generated**, so an out-of-date PDF
   can no longer be sent.

You must then re-run pricing and validation and submit again. This is the
correct behaviour: it prevents an approved number and a sent document from
drifting apart.

## Responding to a revision request

When an approver requests a revision you will see their written reason. Edit
the quotation, re-run pricing and validation, and submit a new approval task.
The old task stays in the audit trail as revision-requested.

## After approval

Once a person has approved the quotation (normally, or with override) you can:

- **generate the customer PDF** — a branded, customer-safe document built from
  the approved quotation version;
- **draft the customer email** — composed by Agent 3 within strict guard rails,
  then held for your review;
- **send the customer email** — only after you explicitly confirm the draft.

The customer PDF and email deliberately contain no cost, no margin, no
threshold, no policy version, no rule ID and no override justification. If you
need those figures, use the internal audit view instead.

If you generate the PDF twice you get the same document back. Use the explicit
regenerate action if you really want a fresh render.

## What you will never see in a customer document

- Estimated costs and gross margin
- The 35% threshold and any internal policy version or rule ID
- Override justification, rejection or revision notes
- Workbook paths, data-source cells or internal comparable prices
- Internal AI prompts or logs

## Getting help

- Commercial rule questions: `docs/commercial_policy_configuration.md`
- Approver behaviour: `docs/internal_mvp_approver_guide.md`
- Data imports and system settings: `docs/internal_mvp_admin_guide.md`
