# Streamlit Community Cloud Deployment (Phase 9)

This document covers the Phase 9 target only: a **stable public demo / internal
pilot demo** environment. It is not an enterprise handover, and the application
is **not production ready**.

## 1. What gets deployed

| Item | Value |
| --- | --- |
| Entry point | `streamlit_app.py` |
| Python version | `runtime.txt` requests `3.11` (major.minor only) |
| Dependencies | `requirements.txt` |
| Theme and runtime settings | `.streamlit/config.toml` |
| Secrets template | `.streamlit/secrets.toml.example` (never real values) |
| Demo data | `Data/synthetic/**` plus in-code synthetic scenarios |

Every runtime dependency is pure Python or ships manylinux wheels. WeasyPrint
and Playwright are deliberately **not** required, because both need system
libraries or a browser download that Streamlit Community Cloud cannot provide;
the PDF renderer falls back to ReportLab.

## 2. How to deploy

1. Push this repository (private is preferred) to GitHub.
2. Sign in to Streamlit Community Cloud and select **Create app**.
3. Choose the repository and branch.
4. Set the main file path to `streamlit_app.py`.
5. Optionally paste secrets into the **Secrets** editor (see section 3).
6. Deploy, then open the hosted URL and expand **Startup status** in the
   sidebar to confirm the version, mode, database mode and agent status.

## 3. Streamlit secrets

**Every secret is optional.** With no secrets the app starts in deterministic
demo mode. Copy the structure from `.streamlit/secrets.toml.example`; keys must
be defined at the top level, not inside a `[section]`.

Only allow-listed names are promoted to environment variables, and an existing
environment variable is never overridden.

Demo safety switches:

```text
DEMO_MODE
SHOW_INTERNAL_COSTS
ENABLE_LLM
PRICING_DATA_MODE
```

Storage:

```text
DEMO_DATABASE_MODE
DATABASE_URL
DATABASE_ECHO
```

Optional AI agents (`n` is 1-4):

```text
AGENTn_PROVIDER
AGENTn_API_KEY
AGENTn_API_KEY_ENV
AGENTn_BASE_URL
AGENTn_MODEL
AGENTn_TIMEOUT_SECONDS
AGENTn_MAX_RETRIES
AGENTn_ORGANISATION
AGENTn_PROJECT
AGENTn_PROMPT_TEMPLATE_VERSION
```

Optional email delivery:

```text
EMAIL_DELIVERY_PROVIDER
EMAIL_SENDER_ADDRESS
EMAIL_INTERNAL_DOMAINS
EMAIL_ALLOW_CUSTOMER_DELIVERY
AUTH_MAX_FAILED_LOGINS
```

No value is listed here on purpose. Never paste a production connection string
or a real API key into a public demo.

## 4. Optional AI API configuration

Agents 1-4 are independent and default to `deterministic`:

| Agent | Role | Default |
| --- | --- | --- |
| Agent 1 | Requirement understanding | deterministic provider |
| Agent 2 | Pricing explanation | deterministic provider |
| Agent 3 | Email drafting | deterministic email template |
| Agent 4 | Document narrative | deterministic document generation |

Supported providers: `deterministic`, `mock`, `http_json`,
`openai_compatible`. A missing key, an invalid provider name, an unreachable
endpoint or a malformed response all fall back to the deterministic result;
none of them fails the workflow.

No AI output can change a cost, a price, a margin, the 35% threshold or the
decision. AI text in the UI is explicitly labelled.

## 5. Demo mode explanation

### Database

Streamlit Community Cloud does not guarantee persistent local storage, so the
demo picks a demo-safe SQLite mode. `DEMO_DATABASE_MODE` controls it:

| Mode | Behaviour |
| --- | --- |
| `auto` (default) | Local `./quotation_ai.db` when the working directory is writable, otherwise a temporary file |
| `temporary_file` | Always a throwaway SQLite file in the OS temp directory |
| `memory` | In-memory SQLite, discarded on every process restart |
| `local_file` | Always `./quotation_ai.db` |

Setting `DATABASE_URL` overrides all of this. The active mode and its
persistence guarantee are always displayed in the sidebar **Startup status**
panel, and the reported target never contains a credential.

### Demo data

All demo material is anonymised and synthetic: `Data/synthetic/pricing_demo.csv`,
`Data/synthetic/quotation_snapshot.json`, the in-code product recommendation and
the margin gate scenarios. There are no real SAP exports, customer names,
company pricing or confidential documents.

### Demo scenarios

Two families are available in the sidebar.

*Product recommendation scenarios* (Scenario A/B/C) drive the single-product
pricing path. *Margin gate scenarios* drive the deterministic quotation-level
gate:

| Scenario | Input | Outcome |
| --- | --- | --- |
| Scenario 1 — PASS | Gross margin 40% (above the 35% threshold) | `PASS` → human approval → PDF and email available |
| Scenario 2 — REVIEW_REQUIRED | Gross margin exactly 35% | `REVIEW_REQUIRED` → override approval with a documented reason |
| Scenario 3 — BLOCKED | Revenue line with no trusted cost basis | `BLOCKED` → cannot be approved, no customer output |

## 6. Customer-facing output protection

Customer PDF, customer email and the customer JSON export are built from a
customer-safe context and are covered by automated leakage tests. They exclude
cost, margin amounts and percentages, the 35% threshold, internal rules, the
policy version, approval reasons, AI prompts and internal audit information.

## 7. Startup checks

The sidebar **Startup status** panel reports, without exposing any secret:

- application version and phase,
- active mode (`demo` or `configured API`),
- pricing data mode,
- database mode and its persistence guarantee,
- Agent 1-4 provider, mode, whether an API key is present, and the fallback.

## 8. Local verification

```bash
python -m pip install -r requirements.txt
python -m pytest -q
streamlit run streamlit_app.py
```

## 9. Known limitations

- Not production ready. No enterprise SSO, no Kubernetes, no Docker production
  deployment, no company infrastructure and no real business data.
- Approval identity in the default demo is a workflow simulation.
- The 35% margin threshold is a **provisional internal-MVP assumption**; the
  formal company standard has not been supplied.
- Demo storage is throwaway. Quotations do not survive a container restart in
  `temporary_file` or `memory` mode.
- No email is actually delivered by the default `console` provider.
- Customer PDF output defaults to Western fonts unless licensed font paths are
  configured on the host.
- `PRICING_DATA_MODE=archived_workbook` is a local, authorised-use-only mode and
  must never be configured on Streamlit Community Cloud.
- Streamlit Community Cloud only accepts major.minor Python versions, so an
  exact patch pin in `runtime.txt` would break the build.
