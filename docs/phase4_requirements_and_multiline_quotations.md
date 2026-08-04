# Phase 4 — Requirement collection, recommendation and multi-line quotations

This phase upgrades the requirement conversation and the single-main-product
workflow into an internal MVP that supports structured requirement extraction
and multiple quotation line items.

## Requirement collection

Three entry paths exist and all three converge on the same domain model:

| Path | Module | Notes |
| --- | --- | --- |
| Deterministic parser | `app/natural_language.py` | Unchanged; still the baseline. |
| Agent 1 (optional) | `app/agents/agents.py` | Provider-neutral, off by default. |
| Structured form | `RequirementConversationAgent.apply_structured_form` | Explicit user edits. |

### Field specification

`app/requirement_fields.py` declares, for every requirement field, its type,
its question, its validator and — where one exists — its allowed value set:

- customer name, region, product request, quantity, currency, Incoterm,
  delivery location, intended use, budget notes, target price,
- requested accessories, requested services, free-text constraints.

Currency and Incoterm are closed sets. Quantity is a bounded positive integer.
List fields are split, trimmed, de-duplicated and length-checked.

`AGENT_EXTRACTABLE_FIELDS` is the allowlist of fields Agent 1 may propose.
Prices, margins, rule outcomes and approval status are deliberately absent, so
a provider cannot create commercial state.

### Candidate merge

`app/requirement_intake.py` applies the merge rules deterministically:

1. **Validate.** A candidate that fails its field spec is rejected and recorded
   as a `RejectedCandidate`. It never reaches the draft.
2. **Confirm when confidence is low.** A candidate below
   `CONFIDENCE_CONFIRMATION_THRESHOLD` (0.7) is parked as a
   `PendingConfirmation` on the draft and surfaced as an explicit question.
   Nothing is written until the user confirms.
3. **Correct explicitly.** An empty field is filled. A field that already holds
   a value is only replaced on an explicit correction, or when the caller is the
   structured form (an explicit user edit).

Agent 1 failure of any kind — missing configuration, timeout, invalid JSON,
schema violation, business-rule violation — falls back to deterministic output
via `app/agents/pipeline.py`. Agent 1 is optional at all times.

## Multi-line quotations

`app/line_items.py` models a quotation as a collection of `QuotationLineItem`
values. A single-product quotation is a one-element collection, so existing
single-item behaviour is preserved. `selected_product_ids` is kept in step with
the main-product lines, so pricing, validation and document generation continue
to operate unchanged.

Categories: `main_product`, `accessory`, `installation`, `warranty`, `service`,
`commercial_addition`.

### Compatibility check

`check_line_item_compatibility` runs the rule engine over the existing product
lines plus the candidate *before* the line can be added, and returns one of:

| Status | Meaning |
| --- | --- |
| `required` | A main product that passes every check. |
| `recommended` | A compatible accessory. |
| `optional` | A compatible alternative the user may add. |
| `incompatible` | A blocking rule error. The line cannot be added. |
| `not_evaluated` | No catalogue product, or not enough input (for example, no region). |

`add_line_item` raises `LineItemError` on an incompatible candidate, on an
unknown product, and on a duplicate product line.

## Material-change invalidation

Adding, editing or removing a line item is a material change. It calls
`invalidate_validation_outputs`, which clears the pricing run, the technical and
commercial validation runs, the combined decision, the approval record and the
generated internal and customer emails, then writes an audit event. A no-op
edit changes nothing and invalidates nothing.

## Save, resume, duplicate and clone

`QuotationService` gained:

- `save_draft` — creates on first save, updates afterwards, and persists the
  line items in the same transaction.
- `resume_draft` — reloads a quotation and rebuilds the deterministic state.
- `duplicate_quotation` — copies requirements and line items into a new draft.
  Pricing, validation, approval and documents are deliberately dropped: a copy
  must be re-priced and re-approved on its own merits.
- `clone_as_new_version` — as above, but the source audit trail is carried over
  and the source quotation id is recorded, so the lineage stays auditable.

## State transitions

```
draft
  └─ requirement turns / form submission ──► collecting_requirements
        └─ all required fields present and a main product line added
              ──► ready_for_analysis
                    └─ pricing run ──► analysed
                          └─ validation ──► review_required / approved
```

Any material line-item edit returns the quotation to
`collecting_requirements` or `ready_for_analysis` and resets the approval to
`not_ready`.

## Tests

`tests/unit/test_requirements_and_line_items.py` and
`tests/integration/test_quotation_save_and_resume.py` cover multi-turn
retention, explicit correction, structured form/conversation consistency,
multi-line quotations, incompatible item rejection, material-change
invalidation, save and resume, duplicate, clone, and Agent 1 fallback.
Catalogue fixtures in `tests/fixtures/catalog_fixtures.py` are entirely
synthetic.
