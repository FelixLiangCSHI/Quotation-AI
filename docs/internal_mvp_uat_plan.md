# Internal MVP — UAT plan

## Scope and objective

Verify that the internal quotation MVP is testable end to end by internal
users: offline Excel ingestion, multi-line quotation, deterministic margin
judgement, human approval, customer email, branded customer PDF, reminders,
audit and the customer/internal data boundary.

## Entry criteria

- `python -m alembic upgrade head` succeeds from an empty database.
- `python -m app.operations.cli` reports `ok` for application, database,
  migrations and commercial policy.
- An administrator account and one account per role exist.
- An offline synthetic SAP Excel export is available. **No real company data
  and no real customer data may be used.**

## The rule under test

- Quotation-level gross margin **> 35%** → `PASS`
- Margin **exactly 35%** → `REVIEW_REQUIRED`
- Margin **< 35%** → `REVIEW_REQUIRED`
- Incompatible configuration or unavailable trusted margin → `BLOCKED`

The 35% threshold is a **provisional** active policy version, not a confirmed
company rule.

## Automated coverage

Every scenario below has an automated counterpart:

| Suite | File |
| --- | --- |
| Scenarios A–J | `tests/integration/test_phase8_end_to_end.py` |
| Document plan and Agent 4 boundaries | `tests/unit/test_phase8_document_plan.py` |
| Branded rendering, assets, fonts, charts | `tests/unit/test_phase8_document_rendering.py` |
| Security hardening | `tests/unit/test_phase8_security_hardening.py` |
| Margin gate | `tests/unit/test_phase5_margin_gate.py` |
| Approval workflow | `tests/integration/test_phase6_approval_workflow.py` |
| Email and reminders | `tests/integration/test_phase7_*.py` |
| Migrations | `tests/integration/test_migrations.py` |

Run everything with `python -m pytest -q`.

---

## Scenario A — Margin above the threshold

**Objective.** The full happy path completes and produces a customer PDF and a
customer email.

| # | Step | Expected result |
| --- | --- | --- |
| 1 | Administrator signs in and uploads the synthetic Excel export | File passes validation; hash shown |
| 2 | Map, review, publish | New pricing data version, status `published`, not active |
| 3 | Activate the version | Exactly one active version; audit event recorded |
| 4 | Sales User creates a multi-line quotation | At least three lines with quantities and prices |
| 5 | Run pricing and validation | Quotation-level margin **> 35%** |
| 6 | Read the decision | `PASS` |
| 7 | Attempt to generate the customer PDF | **Refused** — PASS is not an approval |
| 8 | Submit for approval | Persistent task created; reminder scheduled |
| 9 | Manager performs a normal approve | Status `approved` |
| 10 | Generate the customer PDF | Branded PDF produced from the approved version |
| 11 | Draft and send the customer email | Draft held for review, then delivered or simulated |
| 12 | Open the audit trail | Creation, submission, approval and document generation all present |

**Pass criteria.** Steps 7 and 9 behave exactly as stated; the PDF references
the approved quotation version and carries the approval action ID.

---

## Scenario B — Exactly at the threshold

**Objective.** A margin of exactly 35% cannot be approved normally.

| # | Step | Expected result |
| --- | --- | --- |
| 1 | Build a quotation whose quotation-level margin is exactly 35.00% | Margin displayed as 35.00% |
| 2 | Read the decision | `REVIEW_REQUIRED` |
| 3 | Open the approval task | The normal `approve` action is **not offered** |
| 4 | Attempt approve-with-override without a justification | Refused |
| 5 | Attempt approve-with-override without the threshold acknowledgement | Refused |
| 6 | Approve with a written justification and the acknowledgement | Status `approved_with_override` |
| 7 | Generate the PDF and send the email | Both succeed |
| 8 | Inspect the customer PDF and email | No margin, no threshold, no justification |

---

## Scenario C — Below the threshold, revision cycle

| # | Step | Expected result |
| --- | --- | --- |
| 1 | Build a quotation with a margin below 35% | Margin displayed |
| 2 | Read the decision | `REVIEW_REQUIRED` |
| 3 | Approver requests a revision with a written reason | Approval dropped; reason visible to the sales user |
| 4 | Attempt to generate a customer PDF | Refused |
| 5 | Sales user edits the quotation materially | Version incremented |
| 6 | Check the original approval task | Cancelled as **stale**; cannot be actioned |
| 7 | Re-run pricing and validation | New margin **> 35%** |
| 8 | Submit again | A **new** task, not the old one |
| 9 | Manager approves normally | Status `approved` |
| 10 | Generate the PDF | Built from the new quotation version |

---

## Scenario D — Blocked technical configuration

| # | Step | Expected result |
| --- | --- | --- |
| 1 | Select an incompatible product/accessory combination | Incompatibility reported |
| 2 | Read the decision | `BLOCKED` |
| 3 | Open the approval task | Neither `approve` nor `approve_with_override` is offered |
| 4 | Attempt both actions directly | Both refused |
| 5 | Attempt to generate a customer PDF | Refused |
| 6 | Request a revision | Permitted |

---

## Scenario E — Missing trusted cost

| # | Step | Expected result |
| --- | --- | --- |
| 1 | Quote a line whose trusted material cost is unavailable | Cost reported as unavailable |
| 2 | Inspect the margin | **Unavailable** — not zero, not interpolated |
| 3 | Read the decision | `BLOCKED` |
| 4 | Attempt approval and override | Both refused |
| 5 | Attempt to generate a customer PDF | Refused |

**Pass criteria.** The margin is never computed as if the cost were zero, and
no default or list price is silently substituted.

---

## Scenario F — AI provider failure

| # | Step | Expected result |
| --- | --- | --- |
| 1 | Configure an agent whose provider times out | Timeout is caught |
| 2 | Run the affected step | Deterministic fallback completes the allowed workflow |
| 3 | Configure an agent that returns schema-invalid output | Rejected; fallback used |
| 4 | Configure Agent 4 to return a price or an approval status | Rejected as a schema violation |
| 5 | Configure Agent 4 to propose an unknown chart or section | Rejected as a business-rule violation |
| 6 | Inspect the logs | Fallback recorded; **no secret and no API key logged** |
| 7 | Generate a customer PDF while Agent 4 is failing | Document still produced from the deterministic plan |

---

## Scenario G — Two-day reminder

| # | Step | Expected result |
| --- | --- | --- |
| 1 | Submit a quotation and leave it pending | Reminder due time persisted |
| 2 | Run the worker before the due time | No reminder sent |
| 3 | Advance to two days and run the worker | **Exactly one** reminder sent |
| 4 | Run the worker again | No duplicate |
| 5 | Run two workers concurrently | Still exactly one reminder |
| 6 | Confirm the worker ran outside Streamlit | `python -m worker.reminder_worker --run-once` with no UI open |

---

## Scenario H — Customer-data isolation

Inspect the customer PDF, the customer email and the customer JSON export and
confirm **none** contains:

- estimated cost or gross margin
- the 35% threshold or any policy version
- an internal rule ID
- override justification, rejection or revision notes
- workbook paths, data-source cells or internal comparable prices
- internal AI prompts or logs

Repeat for a quotation approved with an override, where the justification text
exists and must still be absent.

---

## Scenario I — Persistence

| # | Step | Expected result |
| --- | --- | --- |
| 1 | Create, submit, approve, generate a PDF and send an email | All succeed |
| 2 | Restart the application and the worker | Clean restart |
| 3 | Reload the quotation | Approval status intact |
| 4 | Reopen the approval task | Present with its full history |
| 5 | Check reminder state | Preserved; no duplicate reminder after restart |
| 6 | Download the generated document | Byte-identical to the original |
| 7 | List the email records | Present with their delivery status |

---

## Scenario J — Concurrent approval

| # | Step | Expected result |
| --- | --- | --- |
| 1 | Open the same task in two sessions | Both show it as pending |
| 2 | Approve in session one | Succeeds |
| 3 | Approve in session two | Fails with a safe "already completed" error |
| 4 | Inspect the audit trail | Exactly one approval action recorded |

---

## Exit criteria

- Every scenario passes manually and its automated counterpart is green.
- `python -m pytest -q` is fully green.
- Migrations upgrade from empty and round-trip.
- The dependency audit reports no known vulnerability.
- No secret is present in the repository or in any log.
- All remaining limitations are recorded in
  `docs/internal_mvp_final_gap_report.md`.

## Defect severity

| Severity | Definition |
| --- | --- |
| Critical | Internal data reaches a customer artefact; a blocked or unapproved quotation produces a customer document; margin computed with a missing cost treated as zero |
| High | Approval can be bypassed; a normal approve is possible at exactly 35%; data loss on restart; duplicate reminder |
| Medium | Deterministic fallback fails to complete an allowed workflow; audit event missing |
| Low | Wording, layout or usability |
