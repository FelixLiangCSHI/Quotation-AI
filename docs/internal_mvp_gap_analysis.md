# Internal MVP — Phase 0 Gap Analysis

Status: Phase 0 (inspection only). No application code has been changed.

Scope: upgrade the existing Quotation-AI demo into an internal-user-testable MVP
covering Agents 1–4. Agent 5 and live SAP integration are out of scope.

## 1. Repository baseline

Inspected commit: `a19283d` (branch `copilot/upgrade-to-internal-user-testable-mvp`).

| Area | Files | Lines |
| --- | --- | --- |
| Domain / business logic | `app/` (25 modules) | ~6.5k |
| Frontend | `streamlit_app.py` | 1,217 |
| HTTP API | `app/api.py` | 100 |
| CLI | `app/cli.py` | 91 |
| Synthetic data | `Data/synthetic/` | 2 files |
| Docs | `docs/` | 3 files |
| Tests | none | 0 |
| CI | none | 0 |

Runtime dependencies: `streamlit`, `openpyxl`, `reportlab` (plus optional
`fastapi`, `uvicorn`, `httpx` in `requirements-api.txt`).

There is no `pyproject.toml`, `setup.cfg`, `pytest.ini`, `tox.ini`, or
`.github/workflows/`.

## 2. Current components mapped to the target workflow

| Target workflow step | Current implementation | Verdict |
| --- | --- | --- |
| Input (structured + conversational) | `natural_language.parse_quote_request`, `conversation_agent.RequirementConversationAgent`, `config.REQUIRED_QUOTATION_FIELDS` | Reuse |
| Deterministic rule engine | `rule_engine.QuotationRuleEngine` (compatibility, detector grid, generator tube) | Reuse |
| Agent 1 — requirement understanding & recommendation | `conversation_agent`, `recommender.QuoteRecommender` | Reuse, wrap behind provider interface |
| Offline SAP/Excel ingestion & cleaning | `pricing_data.load_pricing_records` (openpyxl + CSV, `HEADER_ALIASES`, `REQUIRED_COLUMNS`, `Decimal` parsing, `_stable_source_id` provenance) | Reuse core, extend |
| Agent 2 — pricing & quotation analysis | `pricing_engine.PricingEngine.analyse` (comparables, confidence, quantity discount, min price, margin) | Reuse, wrap behind provider interface |
| Logical judgement | `technical_validation.validate_technical_configuration`, `commercial_validation.validate_commercial` / `combine_validation_decision` (`pass`, `pass_with_warnings`, `review_required`, `blocked`) | Reuse |
| Human approval | `approval_workflow` FSM + `quotation_models.ApprovalStatus` / `WorkflowStage` | Reuse, extend |
| Agent 3 — email generation & delivery | `email_generator` (internal, customer, reminder, revision templates + fact protection) | Reuse generation; **delivery adapter missing** |
| Two-day approval reminder | `approval_reminder_due_at`, `approval_reminder_status`, `generate_reminder_email` | Compute-only; **no scheduler/processor** |
| Agent 4 — branded PDF | `document_generator.generate_quotation_pdf` (reportlab, A4, optional logo) | Reuse, wrap behind provider interface |
| Downloadable & auditable outputs | `audit_export.build_internal_audit_export` / `build_customer_quotation_export`, `serialization.to_customer_jsonable`, `output_context` | Reuse; **not durable** |

## 3. Reusable code (keep, do not rewrite)

The following are demonstrably usable and satisfy constraint 2:

- **Domain models** — `models.py`, `quotation_models.py`, `serialization.py`.
  `QuotationWorkflowState` is a plain dataclass graph with `to_dict` / `to_json`
  / `to_customer_dict`. Safe to persist as a versioned JSON document.
- **Customer/internal data separation** — `serialization.to_customer_jsonable`
  plus `config.CUSTOMER_PROHIBITED_FIELDS` and per-field
  `customer_visible=False` dataclass metadata. This is a well-designed
  two-layer defence and must be preserved as-is.
- **Deterministic commercial logic** — `pricing_engine`, `commercial_validation`,
  `technical_validation`, `rule_engine`, and the demo policy constants in
  `config.py`. These carry the minimum price, margin, discount authority, and
  deviation thresholds and must stay deterministic (constraint 8).
- **Approval FSM** — `approval_workflow.py`. It already enforces
  decision-gated actions (`ALLOWED_ACTIONS_BY_DECISION`), approver roles
  (`APPROVER_ROLES`), mandatory reasons for override/reject/revision,
  idempotency by `action_id` (`DuplicateApprovalActionError`), and a
  price-change guard.
- **Staleness invalidation** — `workflow_validation.invalidate_validation_outputs`
  resets approval to `NOT_READY` whenever an editable field changes. This is a
  key safety property and must survive persistence.
- **Email fact protection** — `email_generator._common_protected_values` and the
  `WordingRewriter` Protocol, already gated on `ENABLE_LLM`. This is the correct
  seam for Agent 3.
- **PDF generation** — `document_generator.py` (verified to produce ~8 KB PDFs).
- **Offline Excel ingestion** — `pricing_data.py`.
- **Deterministic NL parsing** — `natural_language.py` (EN + zh), no LLM.

## 4. Missing components (must be built)

| # | Gap | MVP requirement |
| --- | --- | --- |
| G1 | **No persistence at all.** Everything lives in `st.session_state` and is lost when the process or browser session ends. | 10, 12 |
| G2 | **No database.** No PostgreSQL, no SQLite, no ORM, no migrations. | 13 |
| G3 | **No authentication or user identity.** Approver name/role are free-text strings typed into the UI; anyone can self-approve. | Login (1), named approver (10) |
| G4 | **No Excel upload / validation / publish flow.** `pricing_data.py` reads a fixed path on disk chosen by an env var; there is no upload, no staging, no validation report, no dataset version, no publish/rollback. | 2, 3 |
| G5 | **Single line item only.** `PricingEngine.analyse` takes one draft and `analyse_workflow_pricing` uses `selected_product_ids[0]`; there is no line-item collection, no per-line pricing, no quotation total. | 6 |
| G6 | **No email delivery adapter.** Emails are rendered to text and displayed; there is no SMTP/provider adapter, no local simulation sink, no delivery record. | 14 |
| G7 | **No reminder scheduler.** The two-day due date is computed on demand from `created_at`; nothing schedules, persists, dispatches, or de-duplicates a reminder. | 15 |
| G8 | **No provider-neutral agent interfaces.** Only `WordingRewriter` exists (Agent 3, unimplemented). Agents 1, 2 and 4 have no interface, no registry, no config-driven selection. | 4, 5, 6 |
| G9 | **No AI output validation boundary.** There is no typed-schema parse → validate → accept/reject pipeline; the `WordingRewriter` Protocol returns an `EmailOutput` that would be trusted directly. | 7, 9 |
| G10 | **No durable audit trail.** `state.audit_events` is an in-memory list; `audit_export` only serialises it on demand. | 10, 18 |
| G11 | **No document store.** Generated PDFs exist only as in-memory bytes for one download click; they cannot be re-downloaded or audited. | 10, 17 |
| G12 | **No tests, no CI, no lint config.** | 14 |
| G13 | **Stage sequencing lives in Streamlit.** `_current_step` and the gating in `streamlit_app.py` encode workflow ordering outside `app/`. | 12 |
| G14 | **No approval task/inbox.** An approver cannot find quotations awaiting their decision; approval is only reachable inside the originating browser session. | 10, 11 |
| G15 | **Snapshot loader is unvalidated.** `data_loader.load_snapshot` uses `.get()` defaults with no schema check or version field. | Robustness |

## 5. Unsafe assumptions in the current code

| # | Assumption | Risk |
| --- | --- | --- |
| U1 | Session state is a safe system of record. | Total data loss; violates MVP items 12 and 18. |
| U2 | The person operating the browser is the approver. `submit_approval_action` accepts `actor_role` as an argument and only checks membership in a four-item tuple. | Any user can self-approve as "Sales Manager". Segregation of duties is unenforced. |
| U3 | `config.py` reads the environment once at import (`CONFIG = load_config()`) and re-exports module-level constants (`DEMO_MODE`, `ENABLE_LLM`, …). | Config is effectively global and untestable; per-request/per-tenant config is impossible. |
| U4 | The demo pricing policy constants (`DEMO_MIN_GROSS_MARGIN_PERCENT`, `DEMO_*_DISCOUNT_LIMIT_PERCENT`, `DEMO_QUANTITY_DISCOUNT_POLICY`) are hardcoded and explicitly labelled "not approved commercial policies". | Internal pilot users will treat outputs as real commercial decisions. Must be externalised to configuration and clearly labelled in the UI. |
| U5 | `PRICING_DATA_MODE=archived_workbook` points at a hardcoded workbook path under `Data/SAP_archived/`. | Encourages committing a proprietary workbook, violating constraint 16. The directory must be git-ignored and the path must be configuration-driven. |
| U6 | `pricing_data` caching is `lru_cache` keyed on path + mtime_ns. | Correct for a single process, unsafe for a multi-worker deployment and unaware of dataset versions. |
| U7 | `data_loader.load_snapshot` trusts arbitrary JSON. | Malformed/hostile snapshot silently yields empty defaults. |
| U8 | Customer-safe filtering is a denylist (`CUSTOMER_PROHIBITED_FIELDS`) applied by field name. | A newly added internal field is customer-visible by default. Needs an allowlist test and a regression test asserting no internal token leaks into customer JSON/PDF/email. |
| U9 | `approval_reminder_due_at` derives the due time from `created_at` in memory. | Reminders cannot survive a restart and can be dispatched repeatedly or never. |
| U10 | Money is `float` in `quotation_models` / `pricing_engine` while `pricing_data` parses `Decimal`. | Rounding drift on margin and minimum-price comparisons. |
| U11 | Idempotency guard compares only against the single last `action_id`. | Replay of an older action id is not detected once a newer action is recorded. |
| U12 | `api.py` exposes `/recommend` with CORS for localhost and no authentication. | Unauthenticated recommendation endpoint; must be secured or kept out of the MVP deployment. |

## 6. Baseline test results

There are **no automated tests** in the repository, so there is no baseline
suite to run.

```
$ python -m pytest -q
no tests ran in 0.01s
```

To establish a functional baseline, a manual end-to-end smoke run was executed
against the three built-in demo scenarios (`straight_through`,
`manager_review`, `blocked`) using `PricingEngine`, `QuotationRuleEngine` and
the synthetic snapshot:

| Scenario | Combined decision | Approval status | Stage | PDF bytes | Emails | Audit events |
| --- | --- | --- | --- | --- | --- | --- |
| `straight_through` | `pass` | `approved` | `approved` | 8,163 | ok | 6 |
| `manager_review` | `pass` | `approved` | `approved` | 8,163 | ok | 6 |
| `blocked` | `pass` | `approved` | `approved` | 8,163 | ok | 6 |

Baseline observations:

1. `python -m compileall app streamlit_app.py` succeeds.
2. The happy path — scenario load → pricing → validation → approval → PDF →
   customer/internal email → audit export — works end to end in memory.
3. All three scenarios returned `pass`. The differentiating behaviour lives in
   `demo_scenarios.apply_demo_price_profile`, which the Streamlit layer applies
   separately from `load_demo_scenario`. Scenario differentiation is therefore
   **not** reproducible outside Streamlit today, which is direct evidence for
   gap G13.
4. `app/api.py` cannot be imported without `fastapi`, which is not in
   `requirements.txt`. Any test collection over `app/` will fail unless the
   optional dependency is installed or the import is made lazy.
5. `data_loader.default_snapshot_path()` resolves to a repository-root file that
   does not exist; only `synthetic_snapshot_path()` works out of the box.

These five items are the concrete baseline that Phase 1 onwards must not
regress.

## 7. Open risks carried into Phase 1

- R1: Introducing persistence while `QuotationWorkflowState` remains a mutable
  in-memory dataclass graph risks divergence between the stored document and
  live object. Mitigation: single write path through a repository layer with
  optimistic concurrency.
- R2: Adding authentication changes every approval call site. Mitigation:
  thread an explicit actor/principal object through `submit_approval_action`
  rather than free-text role strings.
- R3: Multi-line-item support touches the pricing engine, validation, PDF, and
  emails simultaneously. Mitigation: keep the single-item path working as a
  one-element collection for backward compatibility (constraint 18).
- R4: `float` money will produce off-by-cents differences once totals are
  aggregated across lines. Mitigation: decide on `Decimal` at the line-item
  boundary in Phase 3.
