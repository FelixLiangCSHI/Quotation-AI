# Internal MVP — Implementation Plan

Status: Phase 0 complete. Phases 1–10 await approval.

Branch: `feature/internal-mvp-85`.

Every phase follows the same protocol (constraint 15):

1. inspect the current implementation,
2. describe the proposed change,
3. list files to create or modify,
4. implement,
5. run tests,
6. report unresolved risks,
7. **stop and wait for approval before the next phase.**

## Phase 0 — Inspection and planning (complete)

Delivered:

- `docs/internal_mvp_gap_analysis.md`
- `docs/internal_mvp_architecture.md`
- `docs/internal_mvp_implementation_plan.md`

Findings: no tests, no CI, no persistence, no auth, no email delivery, no
reminder scheduler, no agent interfaces, single-line-item only. All
deterministic commercial logic, the approval FSM, the customer/internal
separation, Excel ingestion, and PDF generation are reusable as-is.

Baseline: `pytest` collects nothing; the in-memory happy path works end to end
for all three demo scenarios (PDF 8,163 bytes, 6 audit events each).

**No application code changed in Phase 0.**

## Phase 1 — Test harness, tooling and CI baseline

Goal: make every later phase verifiable. Nothing else changes behaviour.

- Add `pyproject.toml` (project metadata, pytest and coverage config).
- Add `requirements-dev.txt` (`pytest`, `pytest-cov`).
- Add `tests/` with characterisation tests that pin **current** behaviour:
  pricing engine, rule engine, technical and commercial validation, the
  approval transition table, customer/internal serialisation, PDF generation,
  email fact protection, and the three demo scenarios.
- Add `.github/workflows/ci.yml` running the suite.
- Make `app/api.py` importable without `fastapi` installed, or exclude it from
  default collection (baseline observation 4).
- Add `Data/SAP_archived/` to `.gitignore` (risk U5, constraint 16).

Exit criteria: green suite that fails if any existing behaviour regresses.

## Phase 2 — Configuration and policy externalisation

- Replace import-time global config with an injectable `Settings` object;
  keep the existing module-level constants as deprecated shims (risk U3).
- Move commercial policy constants into `config/policy.py`, loadable from
  environment or a config file, with the current demo values as defaults
  (risk U4).
- Add an injectable clock for deterministic time in tests (risk U9).
- Add `DATABASE_URL` and agent provider variables to `.env.example` with no
  real values.

Exit criteria: no behaviour change; policy values overridable in tests.

## Phase 3 — Persistence layer

- SQLAlchemy models, Alembic migrations, and repositories for `quotations`,
  `quotation_line_items`, `approval_tasks`, `audit_events`, `documents`,
  `email_messages`, and `reminders`.
- Serialise `QuotationWorkflowState` into `quotations.state_document` with a
  `schema_version`, plus denormalised columns for the approval inbox.
- Optimistic concurrency via `quotations.version` (risk R1).
- PostgreSQL for deployment, SQLite for tests (constraint 13).

Exit criteria: create → save → reopen → continue works with an identical state
document round trip.

## Phase 4 — Authentication and identity

- `users` table, `LocalAuthBackend`, `Principal`, Streamlit login gate.
- Thread `Principal` through every state-changing service call, replacing
  free-text `actor_role` (risk U2, R2).
- Enforce segregation of duties on approval; audit every denied attempt.
- Seed synthetic internal accounts for the pilot; no real credentials in the
  repository.

Exit criteria: unauthenticated users see only the login screen; a user cannot
approve their own quotation without an override role.

## Phase 5 — Offline SAP Excel ingestion, validation and publishing

- Upload page, cleaning step, validation report, staged/published dataset
  versions, publish and rollback.
- Quotations record the dataset version used for pricing.
- Synthetic, anonymised fixture workbooks under `tests/fixtures/`
  (constraints 16, 17). No live SAP connection anywhere (constraint 1).

Exit criteria: a user can upload an Excel export, see errors and warnings,
publish a clean version, and price against it.

## Phase 6 — Multi-line-item quotations

- `domain/lineitems.py`, per-line pricing, quotation totals, aggregate margin
  and minimum-price checks.
- Single-item flows continue to work as a one-element collection
  (constraint 18, risk R3).
- Decide `Decimal` vs `float` at the line-item boundary (risk U10).

Exit criteria: multi-line quotations price, validate, and render correctly in
the PDF and emails; existing single-item tests still pass.

## Phase 7 — Provider-neutral agent layer (Agents 1–4)

- `agents/base.py`, `registry.py`, `schemas.py`, `guard.py`.
- Local deterministic default providers for all four agents wrapping the
  existing recommender, pricing engine, email generator, and PDF generator
  (constraints 4, 5).
- Strict schema parsing and the accept/reject guard (constraint 7); rejections
  fall back to deterministic output and are audited.
- Architecture test asserting `domain/` never imports `agents/`
  (constraint 8).
- Provider selection and credentials from configuration only (constraint 6).

Exit criteria: the application runs identically with no external AI configured;
a stub non-local provider returning hostile output is rejected by the guard.

## Phase 8 — Approval inbox, email delivery and two-day reminders

- Approval task inbox listing quotations awaiting the signed-in approver (G14).
- `EmailDeliveryAdapter` with a local simulation default and an optional
  SMTP adapter; every attempt recorded in `email_messages` (constraint 14 of
  the MVP definition).
- Reminder scheduling on submission and an idempotent reminder job entry point
  with exactly-once dispatch (risk U9).

Exit criteria: submit → approver sees the task → reminder is scheduled → the
job dispatches it exactly once when the clock advances two days.

## Phase 9 — Documents, downloads and audit trail

- Persist generated PDFs and exports in `documents`; re-downloadable.
- Persist audit events in the database; an audit trail view per quotation.
- Customer-safe vs internal output separation enforced by regression tests that
  assert no internal token appears in any customer artefact (risk U8).

Exit criteria: a user can reopen an old quotation, re-download its PDF and
internal audit export, and read the full audit trail.

## Phase 10 — Streamlit refactor, pilot hardening and documentation

- Move stage sequencing out of `streamlit_app.py` into the domain (G13).
- Split the Streamlit app into pages; session state holds only the principal,
  the current quotation id, and transient form values (constraint 12).
- Clearly label demo policy values as unapproved (risk U4).
- Pilot runbook, seed script, and an internal-user test script covering all 18
  MVP acceptance items.

Exit criteria: a real internal user can complete all 18 MVP steps end to end.

## Traceability — MVP acceptance items to phases

| # | MVP capability | Phase |
| --- | --- | --- |
| 1 | Log in | 4 |
| 2 | Upload offline SAP Excel export | 5 |
| 3 | Validate and publish a clean pricing-data version | 5 |
| 4 | Create a quotation (structured or conversational) | 3, 7 |
| 5 | Receive recommendations | 7 |
| 6 | Select multiple line items | 6 |
| 7 | Run pricing analysis | 6, 7 |
| 8 | Technical and commercial validation | 6 |
| 9 | Deterministic logical decision | 6 |
| 10 | Submit to a named internal approver | 4, 8 |
| 11 | Approve / override / reject / request revision | 4, 8 |
| 12 | Persist and reopen | 3 |
| 13 | Generate internal and customer emails | 7, 8 |
| 14 | Send via adapter or simulate locally | 8 |
| 15 | Two-day pending-approval reminder | 8 |
| 16 | Branded customer PDF | 7, 9 |
| 17 | Download customer-safe and internal audit output | 9 |
| 18 | Review the audit trail | 9 |

## Unresolved risks after Phase 0

| Risk | Description | Planned mitigation |
| --- | --- | --- |
| R1 | Divergence between the persisted state document and the live dataclass graph | Single write path through repositories, optimistic concurrency (Phase 3) |
| R2 | Adding identity touches every approval call site | Explicit `Principal` parameter (Phase 4) |
| R3 | Multi-line support touches pricing, validation, PDF and email at once | Single item modelled as a one-element collection (Phase 6) |
| R4 | `float` money will drift once line totals are aggregated | Decide `Decimal` at the line-item boundary (Phase 6) |
| R5 | Demo policy constants are not approved commercial policy | Externalise and label in the UI (Phases 2, 10) |
| R6 | Denylist-based customer filtering is fail-open for new fields | Allowlist plus leakage regression tests (Phases 7, 9) |
| R7 | Streamlit remains the only frontend and is single-user per session | Business logic moved behind services so an API frontend stays possible (Phase 10) |
| R8 | No production deployment target has been agreed for PostgreSQL | Raise with the pilot owner before Phase 3 |

## Approval gate

Phase 0 is complete. **Implementation has not started.** Please review the gap
analysis, the architecture, and this plan, and confirm before Phase 1 begins.
