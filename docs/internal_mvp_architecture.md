# Internal MVP — Target Architecture

Status: Phase 0 proposal. No application code has been changed.

## 1. Architectural style

A **modular monolith** (constraint 11). One deployable Python package, internal
module boundaries enforced by an explicit dependency direction. No
microservices, no message broker.

Dependency direction (a lower layer never imports a higher one):

```
interfaces (streamlit_app, api, cli, jobs)
        │
        ▼
services (use cases / orchestration)
        │
        ├──────────────► agents (provider-neutral AI seams)
        ▼
domain (deterministic business logic + models)
        │
        ▼
infrastructure (persistence, storage, email, config, clock)
```

Rules:

- `domain/` is pure: no I/O, no Streamlit, no SQLAlchemy, no network.
- `agents/` may be called only by `services/`, never by `domain/`.
- `interfaces/` contain no business rules and no workflow sequencing.
- `infrastructure/` is imported through interfaces (protocols) defined in
  `services/` or `domain/`, never concretely from `domain/`.

## 2. Proposed module structure

The existing flat `app/` package is retained as the import root to minimise
churn; new subpackages are added and existing modules are re-homed only where
the move is mechanical.

```
app/
  config/
    settings.py          # typed Settings loaded from env / config file
    policy.py            # commercial policy values (margins, discount limits)
    providers.py         # agent provider selection + credentials from env

  domain/
    models.py            # existing app/models.py
    quotation.py         # existing app/quotation_models.py
    serialization.py     # existing app/serialization.py  (customer/internal split)
    rules/
      rule_engine.py     # existing app/rule_engine.py
      technical.py       # existing app/technical_validation.py
      commercial.py      # existing app/commercial_validation.py
    pricing/
      engine.py          # existing app/pricing_engine.py
      records.py         # existing app/pricing_data.py record model
    recommendation/
      recommender.py     # existing app/recommender.py
    approval/
      state_machine.py   # existing app/approval_workflow.py
      transitions.py     # allowed-transition table, extracted
    audit.py             # audit event vocabulary + builders
    lineitems.py         # NEW: quotation line-item collection + totals

  services/
    quotation_service.py    # create / load / edit / reopen a quotation
    requirement_service.py  # Agent 1 orchestration (conversation + recommend)
    pricing_service.py      # Agent 2 orchestration
    validation_service.py   # technical + commercial + combined decision
    approval_service.py     # submit action with an authenticated principal
    email_service.py        # Agent 3 orchestration + delivery
    document_service.py     # Agent 4 orchestration + document store
    dataset_service.py      # Excel upload → validate → publish → version
    reminder_service.py     # two-day reminder scheduling + processing
    audit_service.py        # append + query audit events
    ports.py               # protocols implemented by infrastructure

  agents/
    base.py              # AgentRequest / AgentResponse / AgentError
    registry.py          # name → provider factory, resolved from config
    schemas.py           # typed Pydantic schemas for every AI output
    guard.py             # parse → validate → accept/reject pipeline
    requirement/         # Agent 1: LocalRequirementAgent (default), protocol
    pricing/             # Agent 2: LocalPricingAgent (default), protocol
    email/               # Agent 3: LocalEmailAgent (default), protocol
    document/            # Agent 4: LocalDocumentPlanAgent (default), protocol

  infrastructure/
    db/
      engine.py          # SQLAlchemy engine/session factory
      models.py          # ORM tables
      migrations/        # Alembic
      repositories.py    # QuotationRepo, ApprovalTaskRepo, AuditRepo, …
    ingestion/
      excel_reader.py    # existing app/pricing_data.py reader
      cleaning.py        # NEW: normalisation + validation report
      dataset_store.py   # NEW: staged/published dataset versions
    email/
      adapter.py         # EmailDeliveryAdapter protocol
      local.py           # default: writes to DB + local outbox, never sends
      smtp.py            # optional, configured entirely by env
    documents/
      pdf_renderer.py    # existing app/document_generator.py
      store.py           # NEW: persisted document blobs + metadata
    auth/
      principal.py       # Principal(user_id, display_name, roles)
      backend.py         # AuthBackend protocol (SSO-extensible)
      local.py           # default: internal account table, hashed passwords
    clock.py             # injectable time source (testability)

  interfaces/
    streamlit/           # existing streamlit_app.py, split into pages
    api/                 # existing app/api.py, extended and authenticated
    cli/                 # existing app/cli.py
    jobs/
      reminder_job.py    # NEW: idempotent reminder processor entry point

tests/
  unit/                  # domain + agents + guards
  integration/           # services against SQLite
  fixtures/              # synthetic, anonymised Excel and JSON fixtures
```

Backward compatibility: the current top-level module names
(`app.pricing_engine`, `app.approval_workflow`, …) are kept as thin re-export
shims so the existing Streamlit app and demo scenarios keep working during the
migration (constraint 18).

## 3. Agent architecture (constraints 4–9)

### 3.1 Provider-neutral interface

Every agent implements the same shape:

- a request dataclass containing only the data the agent is allowed to see,
- a `run()` method returning a raw provider payload,
- a typed output schema,
- a deterministic guard that decides whether the output is accepted.

Four agents, four protocols:

| Agent | Purpose | Default provider |
| --- | --- | --- |
| Agent 1 | Requirement extraction, product-request interpretation, recommendation rationale | `LocalRequirementAgent` wrapping `natural_language` + `conversation_agent` + `recommender` |
| Agent 2 | Pricing explanation and analysis narrative | `LocalPricingAgent` wrapping `pricing_engine` output formatting |
| Agent 3 | Email wording | `LocalEmailAgent` wrapping the existing `email_generator` templates |
| Agent 4 | PDF content planning (section ordering, headings, summary text) | `LocalDocumentPlanAgent` producing the current fixed layout |

The default provider is `local` for all four (constraint 5). With no
configuration and no network, the application behaves exactly as it does today.

### 3.2 Configuration

Provider selection and credentials come from environment variables or a config
file only (constraint 6). Nothing is hardcoded:

```
AGENT_REQUIREMENT_PROVIDER   # default: local
AGENT_PRICING_PROVIDER       # default: local
AGENT_EMAIL_PROVIDER         # default: local
AGENT_DOCUMENT_PROVIDER      # default: local
AGENT_<NAME>_MODEL           # optional, no default
AGENT_<NAME>_ENDPOINT        # optional, no default
AGENT_<NAME>_API_KEY         # optional, read only at call time, never logged
AGENT_<NAME>_TIMEOUT_SECONDS # optional
```

Unknown provider names fail closed by falling back to `local` and emitting an
audit event. Secrets are never written to the audit log, the database, the
generated documents, or the UI.

### 3.3 Trust boundary (constraint 7)

```
AI provider
   → raw text/JSON
   → schema parse (typed, strict, unknown fields rejected)
   → guard validation:
        • no protected numeric fact altered
        • no internal-only field present in customer-facing output
        • no commercial value (price, margin, discount, approval) proposed
        • length / language / placeholder checks
   → accept  → merged into workflow state by deterministic code
   → reject  → deterministic fallback used, rejection recorded as an audit event
```

AI output never assigns to workflow state directly. Only `services/` writes
state, and only from an accepted, validated schema instance. Every acceptance
and every rejection is auditable.

The existing `email_generator._common_protected_values` fact-protection check is
the prototype for this guard and is generalised into `agents/guard.py`.

### 3.4 Deterministic-only decisions (constraint 8)

The following remain entirely in `domain/` and are unreachable from `agents/`:
minimum price, gross-margin checks, discount authority, compatibility checks,
approval routing, and customer/internal data separation. This is enforced
structurally — `domain/` does not import `agents/` — and by a test that asserts
the import direction.

## 4. Data model

Persisted entities (constraint 10):

| Entity | Key fields |
| --- | --- |
| `users` | id, username, display_name, roles, password_hash, active |
| `quotations` | id, quotation_number, owner_user_id, stage, approval_status, state_document (JSONB), dataset_version_id, version, created_at, updated_at |
| `quotation_line_items` | id, quotation_id, position, product_id, quantity, unit_price, discount_percent, currency |
| `approval_tasks` | id, quotation_id, assigned_approver_user_id, status, due_at, decided_at, decision, reason |
| `audit_events` | id, quotation_id, event_type, actor_user_id, before_state, after_state, changed_fields, details (JSONB), occurred_at |
| `documents` | id, quotation_id, kind (customer_pdf / internal_audit / customer_export), filename, mime_type, content, checksum, created_at |
| `email_messages` | id, quotation_id, kind, recipient, subject, body, provider, status, error, queued_at, sent_at |
| `pricing_datasets` | id, label, status (staged / published / superseded), source_filename, checksum, row_count, uploaded_by, published_at |
| `pricing_dataset_rows` | id, dataset_id, product_id, currency, list_price, cost, source_sheet, source_row |
| `dataset_validation_issues` | id, dataset_id, severity, row, column, message |
| `reminders` | id, quotation_id, kind, due_at, sent_at, attempts, last_error |

Notes:

- `quotations.state_document` stores the serialised `QuotationWorkflowState` so
  the existing dataclass graph is reused verbatim, with a `schema_version`
  field and forward-compatible loading. Query-relevant fields are additionally
  denormalised into columns for the approval inbox.
- `quotations.version` supports optimistic concurrency; a stale write is
  rejected rather than silently overwriting a concurrent approval.
- `reminders` has a unique constraint on `(quotation_id, kind, due_at)` so the
  two-day reminder is dispatched exactly once even if the job runs repeatedly.
- No real business data, customer information, credentials, internal pricing, or
  proprietary workbooks are committed (constraints 16, 17). `Data/SAP_archived/`
  is git-ignored; all fixtures are synthetic and anonymised.

## 5. Persistence (constraint 13)

- SQLAlchemy 2.x + Alembic.
- **PostgreSQL** for the internal MVP deployment.
- **SQLite** permitted for local development and the automated test suite only.
- `DATABASE_URL` is the single configuration point; no credentials in code.
- JSON columns use `JSONB` on PostgreSQL and `JSON` on SQLite via a portable
  type decorator.
- Document and email bodies are stored in the database for the MVP; a
  filesystem/object-store adapter can be swapped in behind the same port later.

## 6. Authentication

- `AuthBackend` protocol with a default `LocalAuthBackend` backed by the `users`
  table with salted password hashing.
- Login yields a `Principal(user_id, display_name, roles)`.
- Every state-changing service call takes an explicit `Principal`. Role checks
  move out of free-text strings into `Principal.roles`, closing risk U2.
- Segregation of duties: the quotation owner cannot approve their own quotation
  unless holding an explicit override role, and the attempt is audited.
- The protocol is the extension point for future SSO; enterprise SSO itself is
  out of scope.

## 7. Frontend (constraint 12)

Streamlit is kept. It becomes a **presentation layer only**:

- `st.session_state` holds the authenticated principal, the current quotation
  id, and transient UI form values — never authoritative business state.
- Every action reloads the quotation from the repository, calls a service, and
  re-renders from the returned state.
- Workflow ordering and gating move from `streamlit_app._current_step` into a
  domain-level stage descriptor, so the same sequencing is exercised by tests
  and by the API without Streamlit.

## 8. Offline data ingestion (constraint 1)

Upload → parse → clean → validate → stage → review → publish.

- No live SAP connection is implemented or configured anywhere.
- Uploaded workbooks are parsed by the existing `openpyxl` reader with
  `HEADER_ALIASES` / `REQUIRED_COLUMNS`, extended with a cleaning step
  (whitespace, currency normalisation, `Decimal` money, duplicate detection).
- Validation produces a report of blocking errors and warnings.
- A dataset can only be published if it has no blocking errors.
- Publishing supersedes the previous version; quotations record the dataset
  version used, so pricing is reproducible and auditable.

## 9. Email and reminders

- `EmailDeliveryAdapter` protocol; default `LocalEmailAdapter` records the
  message with status `simulated` and never opens a network connection.
- An SMTP adapter is available and configured entirely from environment
  variables.
- Every send attempt creates an `email_messages` row, so delivery is auditable.
- The reminder processor is an idempotent job entry point (`interfaces/jobs`)
  runnable from cron or a container schedule. It selects reminders where
  `due_at <= now` and `sent_at is null` for quotations still pending approval,
  renders the existing reminder email, delivers it via the adapter, and marks
  the reminder sent inside the same transaction.

## 10. Testing (constraint 14)

- `pytest` with SQLite for integration tests.
- Unit tests for every deterministic domain rule, the approval transition
  table, the customer/internal separation, and the agent guard.
- Integration tests for the full workflow: login → upload → publish → create →
  recommend → line items → price → validate → decide → submit → approve →
  email → reminder → PDF → download → audit.
- Regression tests asserting that no internal token ever appears in customer
  JSON, customer email, or the customer PDF.
- An architecture test asserting `domain/` does not import `agents/`,
  `infrastructure/`, or `streamlit`.
- Backward-compatibility tests for the three existing demo scenarios.
- A CI workflow running the suite on every push.

## 11. Explicitly out of scope

Agent 5, live SAP integration, learning from approvals, autonomous approval,
multi-country tax engines, real-time FX, enterprise SSO beyond the adapter,
multi-level approval hierarchies, and public internet deployment.
