# Phase 5 — Multi-line pricing analysis and the deterministic margin gate

Phase 5 extends the pricing and validation workflow into a multi-line
quotation analysis with one explicit, deterministic commercial judgement.

## Scope

| Concern | Owner |
| --- | --- |
| Comparable selection, cost aggregation, totals, price floors, margin, compatibility, missing-data checks, final judgement | Deterministic code |
| Plain-language explanation of an already-calculated result | Agent 2 (optional) |

Agent 2 may never change a cost, price, margin, rule evaluation, policy
threshold, decision status or approval requirement.

## Modules

| File | Purpose |
| --- | --- |
| `app/commercial_policy.py` | Versioned `CommercialPolicyVersion`, the policy registry and the provisional MVP policy. The `35.0` threshold literal exists only here. |
| `app/quotation_pricing.py` | Line, bundle/category and quotation-level pricing analysis. |
| `app/margin_gate.py` | The eight ordered deterministic decision steps and the rule trace. |
| `app/pricing_explanation.py` | Agent 2 explanation with protected-fact validation and deterministic fallback. |
| `app/workflow_orchestrator.py` | `analyse_quotation_lines` and `judge_quotation` entry points. |

## The provisional rule

```
Gross margin greater than 35%          -> PASS
Gross margin equal to or below 35%     -> REVIEW_REQUIRED
```

Boundary behaviour is exact:

| Margin | Result |
| --- | --- |
| 40% | PASS |
| 35.0001% | PASS |
| 35.0% | REVIEW_REQUIRED |
| 34.9999% | REVIEW_REQUIRED |
| 20% | REVIEW_REQUIRED |

A PASS is **not** an autonomous customer send. It only means the quotation
cleared the provisional commercial margin gate and can proceed to an
authorised human confirmation step. A REVIEW_REQUIRED result creates a human
approval task. A BLOCKED result cannot be approved at all; it must be
corrected and pricing and validation must be rerun.

## Policy version record

| Field | Value |
| --- | --- |
| `policy_id` | `POL-MARGIN-MVP-001` |
| `policy_name` | Internal MVP Provisional Margin Policy |
| `version` | `1.0.0` |
| `effective_from` | 2024-01-01 |
| `effective_to` | (open) |
| `pass_margin_threshold_percent` | `35.0` |
| `comparison_operator` | `greater_than` |
| `missing_cost_policy` | `block_on_missing_revenue_line_cost` |
| `currency_policy` | `single_normalised_currency_required` |
| `status` | `active` |
| `created_by` | `internal-mvp` |
| `approved_by` | *not available* |

Every decision stores `policy_version_id` (`POL-MARGIN-MVP-001@1.0.0`), the
evaluated margin, the threshold, the comparison operator and the full rule
trace. Registering a newer active policy never mutates a decision that was
already recorded.

## Calculation

Quotation-level gross margin is calculated from quotation totals only:

```
gross_margin_amount  = total_quotation_revenue - total_valid_estimated_cost
gross_margin_percent = gross_margin_amount / total_quotation_revenue * 100
```

It is never the arithmetic average of line-item margin percentages.

### Precision and rounding

* Internal money precision: 4 decimal places, `ROUND_HALF_UP`.
* Internal percentage precision: 6 decimal places, `ROUND_HALF_UP`.
* Money and percentage values are persisted as exact decimal strings.
* Display rounding (2 decimal places) is applied only when rendering.
* The rule is always evaluated on the full internal decimal value. A margin of
  `34.9999%` displays as `35.00%` and still returns REVIEW_REQUIRED.

## Deterministic data handling

| Situation | Behaviour |
| --- | --- |
| Zero or negative quotation revenue | BLOCKED (`zero_quotation_revenue`) |
| Negative price on any line | BLOCKED (`negative_price`) |
| Quantity below 1 | BLOCKED (`invalid_quantity`) |
| Missing unit price | BLOCKED (`missing_price`) |
| Missing cost on a material revenue line | Margin status UNAVAILABLE, decision BLOCKED. A missing cost is never treated as zero. |
| Optional additive component with no cost | Zero cost is used **only** for categories the policy explicitly permits (currently `commercial_addition`). |
| Missing freight, duty, tariff or installation cost inside a comparable cost record | Treated as additive zero inside the existing demo cost policy; the base COGS itself is still mandatory. |
| Service line with no cost basis | BLOCKED — services are not on the zero-cost allow-list. |
| Transfer price present but COGS missing | Never substituted. `allow_transfer_price_as_cogs` is `false`. |
| Mixed currencies | BLOCKED (`mixed_currency_basis`). No live FX exists in this phase. |

## Evaluation order

1. Validate quotation completeness and monetary values (`DATA-001`).
2. Run technical compatibility validation (`TECH-001`).
3. Validate the cost and currency basis (`DATA-002`, `DATA-003`).
4. Calculate line and quotation totals.
5. Calculate quotation-level gross margin.
6. Load the active `CommercialPolicyVersion`.
7. Apply the exact margin threshold (`COMM-MARGIN-001` / `COMM-MARGIN-002`).
8. Persist the complete rule trace and result.

## Rule IDs

| Rule | Meaning |
| --- | --- |
| `TECH-001` | Deterministic technical incompatibility. |
| `DATA-001` | Required quotation data incomplete. |
| `DATA-002` | Trusted cost basis unavailable. |
| `DATA-003` | Invalid or mixed currency basis. |
| `COMM-MARGIN-001` | Gross margin greater than the configured pass threshold. |
| `COMM-MARGIN-002` | Gross margin at or below the configured pass threshold; human approval required. |

## Agent 2

Agent 2 receives the trusted calculated result as input and may produce a
plain-language pricing summary, the main revenue and cost contributors, an
explanation of the result, a summary of missing data and a summary for the
approver.

Every output is labelled:

> AI-generated explanation — not part of the commercial decision.

The output is validated against the protected facts: quotation ID, total
revenue, total cost, gross margin, threshold, decision status and currency.
Output is discarded and replaced with the deterministic explanation when the
provider is absent, times out, fails, returns invalid JSON, drops a protected
value, states a contradicting decision status or states a percentage that is
not a trusted value.

## Staleness

Any material quotation edit (adding, editing or removing a line item, or
editing a quotation field) clears the pricing run, the quotation pricing
analysis, the commercial decision and the AI explanation, and marks the
workflow stale.

## Unresolved commercial-policy assumptions

These must be confirmed by the business before this feature leaves internal
MVP use:

1. **The 35% threshold is provisional.** The formal company approval standard
   has not been supplied. The threshold, its comparison operator and its
   effective dates must be replaced by an approved policy version.
2. **No approver is recorded.** `approved_by` is empty on the provisional
   policy because no authorised person has signed it off.
3. **Margin basis.** The policy assumes gross margin against estimated cost of
   goods including additive installation, warranty, freight, duty and tariff
   components. Whether the company standard uses the same cost base is
   unconfirmed.
4. **Transfer price.** Substituting transfer price for COGS is disabled. If a
   company policy permits the substitution, a new versioned policy must enable
   it explicitly.
5. **Zero-cost allow-list.** Only `commercial_addition` lines may carry a zero
   cost. Whether discounts, trade-ins or goodwill lines belong in this list is
   unconfirmed.
6. **Currency.** Quotations must already be normalised into one currency using
   an approved, versioned conversion source. No such source is configured, so
   any mixed-currency quotation is blocked.
7. **Bundle-level thresholds.** Bundle and line margins are reported for
   visibility only. No company standard exists for bundle-level gates, so none
   is enforced.
8. **Rounding for reporting.** Display rounding is two decimal places. The
   official reporting precision has not been confirmed.
