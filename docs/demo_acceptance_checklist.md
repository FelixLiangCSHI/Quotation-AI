# Streamlit Demo Acceptance Checklist

Use synthetic values only. Start the application from the repository root:

```powershell
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py
```

## Scenario A — Straight-through approval

1. In the sidebar, select **Scenario A — Straight-through approval**.
2. Select **Load scenario**.
3. Confirm the quotation ID is new and the draft uses `Example Medical Center`.
4. Confirm the product explanation shows why the configured product was recommended.
5. Select **Analyse quotation**.
6. Confirm pricing shows high confidence, comparable evidence, calculation assumptions, and no source workbook metadata.
7. Select **Run technical and commercial validation**.
8. Confirm all applicable technical categories pass and the logical judgement is `PASS`.
9. In **Human Review**, choose `Demo Approver` and select **Approve**.
10. Confirm the internal and customer email previews appear.
11. Confirm the final quotation preview uses the approved price.
12. Download the quotation PDF, internal audit JSON, and customer JSON.
13. Open the PDF and confirm the quotation ID and approved total match the page.

## Scenario B — Manager review

1. Select **New quotation**.
2. Load **Scenario B — Manager review**.
3. Run pricing analysis.
4. Confirm a one-time synthetic proposed-price deviation is shown.
5. Run validation and confirm the judgement is `REVIEW REQUIRED`.
6. Confirm normal **Approve** is not available.
7. Choose `Pricing Manager`.
8. Enter `Reviewed synthetic demo deviation.` as the reason.
9. Select **Approve with override**.
10. Confirm the status is `APPROVED WITH OVERRIDE`.
11. Confirm customer email, PDF, and both JSON downloads use the final approved price.

## Scenario C — Blocked quotation

1. Select **New quotation**.
2. Load **Scenario C — Blocked quotation**.
3. Run pricing analysis and validation.
4. Confirm the commercial explanation identifies the price-floor block.
5. Confirm the judgement is `BLOCKED`.
6. Confirm neither **Approve** nor **Approve with override** is available.
7. Choose `Sales Manager`.
8. Enter `Raise the proposed price and revalidate.` as the reason.
9. Select **Request revision**.
10. Confirm no customer quotation PDF is offered and the internal audit remains downloadable.

## Rejection

1. Reload **Scenario C — Blocked quotation** and run pricing and validation.
2. Choose `Sales Manager`.
3. Enter `Configuration cannot proceed in its current form.` as the reason.
4. Select **Reject**.
5. Confirm the rejection notification preview appears.
6. Confirm customer email and PDF actions remain unavailable.

## Edit quantity and revalidate

1. Load **Scenario A** and complete pricing and validation.
2. Expand **Edit quotation and revalidate**.
3. Change quantity from `1` to `2`.
4. Select **Save edits and require re-analysis**.
5. Confirm prior pricing, validation, approval, and generated outputs disappear.
6. Run pricing and validation again.
7. Confirm totals and the quantity adjustment reflect quantity `2`.

## Edit price and revalidate

1. In the quotation editor, enable **Use proposed unit-price override**.
2. Enter a positive proposed unit price different from the recommendation.
3. Save the edit.
4. Confirm prior approval and output downloads are invalidated.
5. Rerun pricing and validation.
6. Confirm the commercial rules explain the override, discount, deviation, and floor outcomes.

## Approval and communication

1. Confirm approval controls are hidden before validation.
2. Confirm only actions valid for the current decision are shown.
3. Submit one approval action.
4. Confirm rerunning the page does not create a duplicate approval event.
5. Confirm internal communication may show review context.
6. Confirm customer communication contains no COGS, margin, floor, authority, rule ID, workbook, or internal-comment text.

## PDF and audit downloads

1. Approve Scenario A.
2. Download the PDF twice and confirm each download opens independently.
3. Confirm the PDF contains quotation metadata, customer, product, totals, Incoterm, approval, and disclaimer sections.
4. Confirm the PDF contains no COGS or internal margin labels.
5. Download internal audit JSON and confirm it contains structured workflow events.
6. Download customer JSON and confirm it omits internal pricing and approval details.

## New quotation and session isolation

1. Complete or partially edit a quotation.
2. Select **New quotation**.
3. Confirm a new quotation ID appears and all dependent state is cleared.
4. Open the application in a separate private/incognito browser session.
5. Confirm the second session has a different quotation ID.
6. Load different scenarios in the two sessions.
7. Edit quantity in one session and confirm the other session is unchanged.
