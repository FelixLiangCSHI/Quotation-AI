# Internal MVP — Deployment guide

## Components

| Component | Process | Purpose |
| --- | --- | --- |
| Streamlit application | `streamlit run streamlit_app.py` | Quotation, approval, email and audit UI |
| FastAPI service | `uvicorn app.api:app` | Recommendation and `/status` endpoints |
| Reminder worker | `python -m worker.reminder_worker` | Two-day approval reminders, outside any UI session |
| Database | PostgreSQL (SQLite for development) | All persistent state |

The reminder worker is deliberately independent of Streamlit. It keeps all of
its state in the database, so a reminder fires whether or not anyone has the
application open.

## Prerequisites

- Python 3.12
- PostgreSQL 14 or later for an internal MVP environment
  (SQLite is supported for local development and automated tests only)

## Install

```bash
python -m venv .venv
. .venv/bin/activate            # Windows: .\.venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-dev.txt      # tests
pip install -r requirements-api.txt      # FastAPI service
```

## Configure

Copy `.env.example` to `.env` and edit it locally. `.env` is git-ignored and no
real credential may ever be committed. Every secret is loaded from the
environment or a secret manager at the point of use, never cached in a config
object and never written to a log or an export.

See "Environment variables" below for the full list.

## Migrate the database

```bash
python -m alembic upgrade head          # from an empty database or an existing one
python -m alembic current               # confirm the revision
python -m alembic downgrade -1          # roll back one revision
```

Migration chain:

| Revision | Description |
| --- | --- |
| `0001_initial` | Initial internal MVP schema |
| `7299ce046f09` | Phase 2 — Excel ingestion |
| `5a8d9fabed0b` | Phase 6 — authenticated approval |
| `e908094448e4` | Phase 7 — email delivery and reminders |
| `a91dba1dbf86` | Phase 8 — document generation metadata |

Upgrade from empty to head and the full downgrade-to-base round trip are both
verified.

## Run

```bash
# Web application
streamlit run streamlit_app.py

# API and operational status
python -m uvicorn app.api:app --host 127.0.0.1 --port 8000

# Reminder worker — one shot, suitable for cron or a container schedule
python -m worker.reminder_worker --run-once

# Reminder worker — resident loop
python -m worker.reminder_worker --interval-seconds 900
```

A cron entry invoking `--run-once` is equivalent to the built-in loop. Running
two workers concurrently is safe: a reminder is claimed atomically, so it is
never sent twice.

## First-run checklist

1. `python -m alembic upgrade head`
2. Create the first administrator account.
3. Sign in as the administrator and import an offline SAP Excel export.
4. Publish the version, then **activate** it explicitly.
5. Create sales, sales-manager and pricing-manager accounts.
6. Confirm `python -m app.operations.cli` reports `ok` for application,
   database, migrations, pricing data and commercial policy.
7. Start the reminder worker.

## Demo accounts

For a demo or a walkthrough, one account per role can be seeded in a single
step instead of creating each account by hand:

```bash
python -m app.auth.demo_accounts
```

This creates `demo.admin`, `demo.sales`, `demo.salesmanager` and
`demo.pricingmanager`. Passwords are taken from `QUOTATION_DEMO_PASSWORD` when
it is set, otherwise a random password per account is generated and printed
once — record it, because passwords are hashed and cannot be shown again.
Re-running is safe: an existing account is left untouched, including its
password. These accounts are for demo and pilot environments only; do not seed
them into a production deployment.

## Health checks

```bash
python -m app.operations.cli              # every component as JSON
python -m app.operations.cli --component database
curl http://localhost:8000/status
curl http://localhost:8000/status/migrations
```

Neither surface exposes a credential or a raw connection string; a database URL
is reduced to a driver, host and database summary, and secret-backed settings
report presence only.

## Environment variables

No value below is a secret. Secrets are supplied out of band; the configuration
only names the variable that holds them.

### Core

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy URL. PostgreSQL for internal MVP, SQLite for development |
| `DATABASE_ECHO` | Echo SQL to stderr. Development troubleshooting only |
| `DEMO_MODE`, `SHOW_INTERNAL_COSTS`, `ENABLE_LLM`, `PRICING_DATA_MODE` | Local demo behaviour |

### Offline Excel ingestion

| Variable | Purpose |
| --- | --- |
| `INGESTION_STORAGE_ROOT` | Where uploaded workbooks are stored. Must be outside the repository |
| `INGESTION_SUPPORTED_CURRENCIES` | Currency allowlist |
| `INGESTION_MAX_UPLOAD_BYTES` | Upload size ceiling |
| `INGESTION_REGION_PATTERN` | Region-code pattern |
| `INGESTION_MAX_UNCOMPRESSED_BYTES` | Decompression-bomb guard: total expanded size |
| `INGESTION_MAX_COMPRESSION_RATIO` | Decompression-bomb guard: expansion ratio |
| `INGESTION_MAX_ARCHIVE_ENTRIES` | Decompression-bomb guard: archive entry count |
| `INGESTION_MAX_SHEET_ROWS` | Row cap for a single sheet |

### Authentication

| Variable | Purpose |
| --- | --- |
| `AUTH_MAX_FAILED_LOGINS` | Failed sign-ins tolerated in a 15-minute window before lockout |

### AI agents (`n` = 1..4)

| Variable | Purpose |
| --- | --- |
| `AGENTn_PROVIDER` | `deterministic`, `mock`, `http_json` or `openai_compatible` |
| `AGENTn_BASE_URL`, `AGENTn_MODEL` | Endpoint and model |
| `AGENTn_API_KEY_ENV` | **Name** of the variable holding the key, never the key |
| `AGENTn_TIMEOUT_SECONDS`, `AGENTn_MAX_RETRIES` | Timeout and retry budget |
| `AGENTn_PROMPT_TEMPLATE_VERSION` | Prompt version for audit |

All four agents default to `deterministic`: no API key, no external call.

### Email and reminders

| Variable | Purpose |
| --- | --- |
| `EMAIL_DELIVERY_PROVIDER` | `console`, `smtp` or `graph` |
| `EMAIL_SENDER_ADDRESS`, `EMAIL_INTERNAL_DOMAINS` | Sender and internal domain allowlist |
| `EMAIL_ALLOW_CUSTOMER_DELIVERY` | Customer delivery must be enabled deliberately |
| `EMAIL_AUTO_SEND_APPROVAL_REQUEST` | Send the approval request on submission |
| `EMAIL_BODY_STORAGE` | `hash`, `redacted` or `full` |
| `EMAIL_MAX_DELIVERY_ATTEMPTS`, `EMAIL_TEMPLATE_VERSION` | Retry budget and template version |
| `APPROVAL_REMINDER_DELAY_HOURS` | Default 48 — two calendar days |
| `APPROVAL_REMINDER_MAX_COUNT` | Default 1 |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_USE_TLS`, `SMTP_TIMEOUT_SECONDS` | SMTP transport |
| `SMTP_PASSWORD_ENV` | **Name** of the variable holding the SMTP password |
| `GRAPH_ENABLED`, `GRAPH_SENDER_USER_ID`, `GRAPH_BASE_URL` | Microsoft Graph transport |
| `GRAPH_TENANT_ID_ENV`, `GRAPH_CLIENT_ID_ENV`, `GRAPH_CLIENT_SECRET_ENV` | **Names** of the variables holding Graph credentials |

### Document generation

| Variable | Purpose |
| --- | --- |
| `DOCUMENT_ASSET_ROOT` | Approved asset repository for logos and figures |
| `DOCUMENT_LOGO_ASSET` | Bare file name of the approved logo inside that root |
| `DOCUMENT_FONT_FAMILY` | Font family name for the branded template |
| `DOCUMENT_FONT_REGULAR_PATH`, `DOCUMENT_FONT_BOLD_PATH` | Paths to licensed fonts on the host |

**No font file is committed to this repository and none may be added.** When a
font path is unset or unreadable the renderer falls back to a built-in face,
records a warning and still produces the document, including for non-Western
text.

## PDF rendering engines

The renderer tries WeasyPrint, then Playwright, then ReportLab, and records a
warning for each engine that is unavailable. ReportLab is a pure-Python
deterministic fallback and is always present, so document generation never
depends on a system library being installed. Install WeasyPrint or Playwright
for the highest-fidelity branded output.

## Security notes for the deployment

- Serve over HTTPS. Behind a reverse proxy, set secure, `HttpOnly` and
  `SameSite` cookie attributes at the proxy.
- Keep `INGESTION_STORAGE_ROOT` and `DOCUMENT_ASSET_ROOT` outside the
  repository and outside any web-served directory.
- Restrict database access to the application and worker identities.
- Do not enable `DATABASE_ECHO` in an internal MVP environment.
- Never commit `.env`.

See `docs/internal_mvp_security_review.md` for the full review.
