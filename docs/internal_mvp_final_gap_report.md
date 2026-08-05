# Internal MVP — Final gap report and acceptance

Phase 8 close-out. Evidence-based, conservatively scored.

> **The 35% pass-margin threshold is provisional.** It is an active internal-MVP
> policy version (`POL-MARGIN-MVP-001@1.0.0`) adopted so the workflow can be
> exercised end to end. It has **not** been confirmed as a permanent company
> rule and it never appears in any customer-facing output.

---

## 1. Final completion score

### 1.1 Scoring method

| Completion level | Credit |
| --- | --- |
| Fully implemented and tested | Full module weight |
| Implemented with material limitations | Partial weight |
| Simulated only | No more than 30% of module weight |
| UI label or documentation only | No credit |
| Not implemented | Zero |

### 1.2 Calculation

| Module | Weight | Awarded | Basis |
| --- | ---: | ---: | --- |
| Offline Excel ingestion and data quality | 12.0 | **11.0** | Full pipeline implemented and tested; deducted because only synthetic workbooks have been exercised — real SAP export variants are unverified |
| Persistent database | 10.0 | **10.0** | PostgreSQL/SQLite via SQLAlchemy, five Alembic revisions, empty-database upgrade and full downgrade round trip verified, restart persistence proven (Scenario I) |
| Agent 1 and API slot | 8.0 | **6.0** | Provider slot, timeout, retry, circuit breaker and deterministic fallback implemented and tested; deducted because no live LLM provider has been exercised |
| Multi-line quotation and recommendation | 10.0 | **9.5** | Multi-line quotations with per-line quantity and price, compatibility checking, labelled recommendations; deducted for a narrow synthetic product catalogue |
| Agent 2 and pricing analysis | 10.0 | **8.5** | Quotation-level revenue/cost analysis is deterministic and tested; AI commentary is guard-railed and validated; deducted for no live provider and a limited comparables dataset |
| Deterministic margin judgement | 12.0 | **12.0** | `>35%` → PASS, `=35%` → REVIEW_REQUIRED, `<35%` → REVIEW_REQUIRED, incompatible or missing cost → BLOCKED. Boundary tested explicitly; quotation-level, never an average; missing cost never zero |
| Human approval | 12.0 | **12.0** | Authenticated, permission-controlled, persistent; PASS is not an approval; override requires justification and acknowledgement; BLOCKED cannot be approved; staleness and concurrency both enforced and tested |
| Agent 3 and email | 8.0 | **6.0** | Provider abstraction (console, SMTP, Microsoft Graph), persistent records, idempotency, retry, human draft review, recipient allowlisting; deducted because only the console provider has been exercised against a real transport |
| Two-day reminder | 5.0 | **5.0** | Executes in a separate process outside Streamlit, database-backed due time, atomic claim, exactly-once and idempotent under repeat and concurrent runs |
| Agent 4 and PDF | 7.0 | **5.5** | Strict DocumentPlan, sanitisation, branded Jinja2 template, customer-safe charts, controlled assets, font fallback, full metadata and invalidation; deducted because WeasyPrint and Playwright are not installed in the verification environment, so only the ReportLab engine is proven end to end |
| Security and customer/internal isolation | 3.0 | **2.75** | Upload, authentication, authorisation, secret and rendering hardening implemented and tested; deducted for the per-process lockout counter and for cookie/TLS being a deployment responsibility |
| Tests, documentation and operating guidance | 3.0 | **3.0** | 509 automated tests green; unit, integration, end-to-end and migration suites; eight Phase 8 documents; operational status CLI and endpoints |
| **Total** | **100.0** | **91.25** | |

```
11.00 + 10.00 + 6.00 + 9.50 + 8.50 + 12.00
     + 12.00 +  6.00 + 5.00 + 5.50 +  2.75 + 3.00
= 91.25
```

### **Final completion: 91.25%**

### 1.3 Why the ≥85% claim is admissible

| Required condition | Evidence |
| --- | --- |
| End-to-end evidence exists | Scenarios A–J automated in `tests/integration/test_phase8_end_to_end.py` |
| Data is persistent | Scenario I restarts every service object against the same database and reloads the quotation, task, documents and emails |
| Email has a real provider abstraction | `app/emailing/providers` — console, SMTP and Microsoft Graph behind one interface, with persistent records |
| Reminder executes outside Streamlit | `python -m worker.reminder_worker`, database-backed, no UI dependency |
| Approval is authenticated and persistent | `ApprovalService` with PBKDF2 credentials, database sessions and persistent tasks |
| Agent 1–4 slots are implemented | `app/agents/` — four configurable providers with deterministic fallback |
| The 35% decision boundary is tested | Scenario B and `tests/unit/test_phase5_margin_gate.py` |
| Customer-safe boundaries are verified | Scenario H asserts the absence of every prohibited token in the PDF, the email and the JSON export |

No credit anywhere in this report rests on a UI placeholder or a mock-only
workflow.

---

## 2. Acceptance report

| Module | Target behaviour | Implementation evidence | Automated test | Manual test | Status | Remaining risk |
| --- | --- | --- | --- | --- | --- | --- |
| Excel ingestion | Upload, validate, version, activate | `app/ingestion/` | `test_ingestion_*.py`, `test_pricing_data_versions.py` | UAT A.1–A.3 | Complete | Real SAP export variants unverified |
| Data quality | Normalise, validate, quarantine, report | `app/ingestion/validation.py`, `report.py` | `test_ingestion_validation.py` | UAT A.2 | Complete | Synthetic data only |
| Persistence | Survive restart | `app/db/`, `migrations/` | `test_migrations.py`, Scenario I | UAT I | Complete | None material |
| Agent 1 | Requirement extraction with a provider slot | `app/agents/agents.py` | `test_agent_providers.py`, Scenario F | UAT F | Complete with limits | No live provider verified |
| Multi-line quotation | Multiple lines, quantities, prices, compatibility | `app/line_items.py`, `app/quotation_models.py` | `test_requirements_and_line_items.py` | UAT A.4 | Complete | Narrow catalogue |
| Agent 2 | Pricing analysis and commentary | `app/pricing/`, `app/agents/` | `test_phase5_quotation_workflow.py` | UAT A.5 | Complete with limits | No live provider; limited comparables |
| Margin judgement | Quotation-level, `>35%` PASS, `=35%` and `<35%` REVIEW_REQUIRED, BLOCKED cases | `app/commercial_policy.py`, `app/margin_gate.py` | `test_phase5_margin_gate.py`, Scenarios B, C, D, E | UAT B, C, D, E | Complete | Threshold is provisional |
| Human approval | Authenticated, permission-controlled, persistent, override-gated | `app/services/approval_service.py` | `test_phase6_*.py`, Scenarios A, B, C, D, J | UAT A, B, J | Complete | None material |
| Agent 3 and email | Composition, provider abstraction, human review, persistence | `app/emailing/` | `test_phase7_email_workflow.py`, Scenarios A, B, H | UAT A, H | Complete with limits | SMTP and Graph unverified live |
| Reminder | Two days, exactly once, out of process | `worker/reminder_worker.py`, `app/emailing/reminders.py` | `test_phase7_reminders.py`, Scenario G | UAT G | Complete | None material |
| Agent 4 and PDF | Plan, branded template, customer-safe render | `app/documents/`, `app/services/document_service.py` | `test_phase8_document_*.py`, Scenarios A, B, F, H | UAT A, B, H | Complete with limits | Only the ReportLab engine proven here |
| Customer isolation | No cost, margin, threshold, rule, policy or override in customer output | `app/documents/plan.py`, `renderer.py`, `app/config.py` | Scenario H, `test_customer_safe_serialization.py` | UAT H | Complete | None material |
| Security | Upload, auth, authz, secrets, AI, rendering | See the security review | `test_phase8_security_hardening.py` | UAT entry checks | Complete with limits | See open risks |
| Audit | All material actions recorded | `app/services/audit_view.py`, audit repository | `test_phase6_audit_trail.py`, Scenario A | UAT A.12 | Complete | None material |
| Operations | Health and configuration status | `app/operations/`, `app/api.py` | Status CLI verified | UAT entry checks | Complete | No metrics or alerting |

---

## 3. Workflow coverage matrix

| # | Workflow step | Implemented | Persistent | Automated | Coverage |
| --- | --- | :-: | :-: | :-: | :-: |
| 1 | Administrator signs in | Yes | Yes | Yes | Full |
| 2 | Offline Excel upload and validation | Yes | Yes | Yes | Full |
| 3 | Column mapping and normalisation | Yes | Yes | Yes | Full |
| 4 | Row validation and quarantine | Yes | Yes | Yes | Full |
| 5 | Version publish | Yes | Yes | Yes | Full |
| 6 | Version activation (administrator only) | Yes | Yes | Yes | Full |
| 7 | Sales user signs in | Yes | Yes | Yes | Full |
| 8 | Requirement capture (Agent 1 slot) | Yes | Yes | Yes | Full |
| 9 | Multi-line quotation build | Yes | Yes | Yes | Full |
| 10 | Compatibility validation | Yes | Yes | Yes | Full |
| 11 | Pricing run (Agent 2 slot) | Yes | Yes | Yes | Full |
| 12 | Quotation-level margin analysis | Yes | Yes | Yes | Full |
| 13 | Deterministic decision (PASS / REVIEW_REQUIRED / BLOCKED) | Yes | Yes | Yes | Full |
| 14 | Submission and approver assignment | Yes | Yes | Yes | Full |
| 15 | Approval-request email | Yes | Yes | Yes | Full |
| 16 | Normal human approval | Yes | Yes | Yes | Full |
| 17 | Approval with override and justification | Yes | Yes | Yes | Full |
| 18 | Revision request and rework | Yes | Yes | Yes | Full |
| 19 | Rejection | Yes | Yes | Yes | Full |
| 20 | Stale-task detection | Yes | Yes | Yes | Full |
| 21 | Concurrent-approval protection | Yes | Yes | Yes | Full |
| 22 | Two-day reminder, out of process | Yes | Yes | Yes | Full |
| 23 | Customer email draft (Agent 3) | Yes | Yes | Yes | Full |
| 24 | Human draft review and send | Yes | Yes | Yes | Full |
| 25 | Document plan (Agent 4) | Yes | Yes | Yes | Full |
| 26 | Branded customer PDF | Yes | Yes | Yes | Full |
| 27 | Customer-safe charts | Yes | Yes | Yes | Full |
| 28 | Document metadata register | Yes | Yes | Yes | Full |
| 29 | Document invalidation on material edit | Yes | Yes | Yes | Full |
| 30 | Historical document retention | Yes | Yes | Yes | Full |
| 31 | Customer-safe download | Yes | Yes | Yes | Full |
| 32 | Restricted internal audit export | Yes | Yes | Yes | Full |
| 33 | Audit trail | Yes | Yes | Yes | Full |
| 34 | Operational status | Yes | n/a | Partial | Partial |
| 35 | Deterministic fallback for every agent | Yes | Yes | Yes | Full |
| 36 | Restart persistence | Yes | Yes | Yes | Full |

**34 of 36 steps at full coverage; 2 partial. Workflow coverage ≈ 97%,
comfortably above the 85% objective.** The completion score of 91.25% is lower
than the coverage figure because it also penalises unverified live providers
and the single proven PDF engine.

---

## 4. Commands required to run the MVP

```bash
# Install
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt      # tests
pip install -r requirements-api.txt      # FastAPI service

# Migrate
python -m alembic upgrade head
python -m alembic current

# Run
streamlit run streamlit_app.py
python -m uvicorn app.api:app --host 127.0.0.1 --port 8000
python -m worker.reminder_worker --run-once
python -m worker.reminder_worker --interval-seconds 900

# Operate
python -m app.operations.cli
curl http://localhost:8000/status

# Verify
python -m pytest -q
python -m compileall -q app worker pages tests migrations
python -m pip_audit -r requirements.txt
```

## 5. Environment variables

Names only; no value here is a secret. The full table is in
`docs/internal_mvp_deployment.md`.

`DATABASE_URL`, `DATABASE_ECHO`, `DEMO_MODE`, `SHOW_INTERNAL_COSTS`,
`ENABLE_LLM`, `PRICING_DATA_MODE`,
`INGESTION_STORAGE_ROOT`, `INGESTION_SUPPORTED_CURRENCIES`,
`INGESTION_MAX_UPLOAD_BYTES`, `INGESTION_REGION_PATTERN`,
`INGESTION_MAX_UNCOMPRESSED_BYTES`, `INGESTION_MAX_COMPRESSION_RATIO`,
`INGESTION_MAX_ARCHIVE_ENTRIES`, `INGESTION_MAX_SHEET_ROWS`,
`AUTH_MAX_FAILED_LOGINS`,
`AGENT1..4_PROVIDER`, `AGENT1..4_BASE_URL`, `AGENT1..4_MODEL`,
`AGENT1..4_API_KEY_ENV`, `AGENT1..4_TIMEOUT_SECONDS`,
`AGENT1..4_MAX_RETRIES`, `AGENT1..4_PROMPT_TEMPLATE_VERSION`,
`EMAIL_DELIVERY_PROVIDER`, `EMAIL_SENDER_ADDRESS`, `EMAIL_INTERNAL_DOMAINS`,
`EMAIL_ALLOW_CUSTOMER_DELIVERY`, `EMAIL_AUTO_SEND_APPROVAL_REQUEST`,
`EMAIL_BODY_STORAGE`, `EMAIL_MAX_DELIVERY_ATTEMPTS`, `EMAIL_TEMPLATE_VERSION`,
`APPROVAL_REMINDER_DELAY_HOURS`, `APPROVAL_REMINDER_MAX_COUNT`,
`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD_ENV`,
`SMTP_USE_TLS`, `SMTP_TIMEOUT_SECONDS`,
`GRAPH_ENABLED`, `GRAPH_TENANT_ID_ENV`, `GRAPH_CLIENT_ID_ENV`,
`GRAPH_CLIENT_SECRET_ENV`, `GRAPH_SENDER_USER_ID`, `GRAPH_BASE_URL`,
`DOCUMENT_ASSET_ROOT`, `DOCUMENT_LOGO_ASSET`, `DOCUMENT_FONT_FAMILY`,
`DOCUMENT_FONT_REGULAR_PATH`, `DOCUMENT_FONT_BOLD_PATH`.

## 6. Database migrations

| Revision | Down revision | Description |
| --- | --- | --- |
| `0001_initial` | — | Initial internal MVP schema |
| `7299ce046f09` | `0001_initial` | Phase 2 — Excel ingestion |
| `5a8d9fabed0b` | `7299ce046f09` | Phase 6 — authenticated approval |
| `e908094448e4` | `5a8d9fabed0b` | Phase 7 — email delivery and reminders |
| `a91dba1dbf86` | `e908094448e4` | Phase 8 — document generation metadata |

Verified: upgrade from an empty database to head, full downgrade to base and
re-upgrade to head.

## 7. Open risks by severity

### High

*None identified.*

### Medium

| Risk | Impact | Mitigation |
| --- | --- | --- |
| No live LLM provider has been exercised | Real-provider latency, cost and output quality are unknown | Every agent falls back deterministically; pilot one agent against a real endpoint before wider rollout |
| SMTP and Microsoft Graph adapters unverified against a live server | Delivery may fail in a real environment | Console provider is proven; run a delivery smoke test in the target environment |
| Only the ReportLab PDF engine is proven | Branded fidelity is lower than the HTML template intends | Install WeasyPrint or Playwright and re-run the document tests |
| Login lockout is per-process | Throttling weakens under horizontal scaling | Move the counter to the database or a shared cache before scaling out |
| Cookie security attributes and HTTPS not enforced by the application | Session interception if deployed carelessly | Terminate TLS at a proxy and set the attributes there |
| Only synthetic Excel workbooks tested | Real SAP exports may need mapping work | Run a real export through the import wizard during pilot |

### Low

| Risk | Mitigation |
| --- | --- |
| No secret-manager integration | Acceptable for an internal MVP |
| `manage_users` and `configure_system` not enforced in code | Restrict administrative access by process |
| No session idle timeout or rotation on a role change | Keep the 12-hour lifetime |
| Password policy is length-only | Set expectations administratively |
| Agent `error_detail` logged verbatim | Run at WARNING in shared environments |
| No operational metrics or alerting | Poll `/status` from external monitoring |
| Narrow synthetic product catalogue | Extend during pilot |

## 8. Explicit limitations

1. **The 35% threshold is provisional.** It is an active internal-MVP policy
   version, not a confirmed company rule, and it requires formal business
   confirmation before this system is used commercially.
2. There is **no live SAP connection**. Offline Excel export is the only input.
3. No real company data, no real customer data and no proprietary font file is
   present in this repository.
4. Agent behaviour has been validated against deterministic, mock and failing
   providers, not against a production LLM.
5. This is an internal-user-testable MVP. It is not production-hardened for
   external customer traffic.

## 9. Statement

The evidence-supported completion score is **91.25%**, above the 85%
objective. The score rests on automated end-to-end scenarios against a
persistent database, not on UI placeholders or mocked-only workflows. All
remaining limitations are recorded above.

**The 35% pass-margin threshold is provisional and must not be presented as an
approved permanent company rule.**
