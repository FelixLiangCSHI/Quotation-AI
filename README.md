# Quotation Bot

Quotation Bot is a rule-backed quotation assistant prototype for DRX-Compass configuration support. It combines structured product data, deterministic validation rules, and a lightweight web frontend to recommend quotation items from a natural-language request.

The current MVP can parse keyword-based requests, suggest a main model plus compatible options, and validate selected items against the implemented rule engine. The Streamlit demo defaults to deterministic local mode and does not require an LLM or external service.

## Current Capabilities

- Natural-language keyword matching for quotation requests.
- Main model and accessory recommendation from `quotation_snapshot.json`.
- FastAPI backend for health checks and recommendation requests.
- Static HTML/CSS/JavaScript frontend for local demos.
- Streamlit chatbot-style prototype.
- Deterministic multi-turn requirement collection that retains earlier answers
  and supports explicit corrections.
- Session-isolated quotation drafts with ordered missing-field questions.
- Main-product recommendation and explicit product selection before analysis.
- Deterministic rule checks for:
  - product region limits,
  - system combination compatibility,
  - detector/grid support,
  - generator/tube specification lookup.
- Rule review assets for candidate rules that still need SME confirmation.
- Demo output generation scripts for client-facing and internal audit samples.

## Project Structure

```text
app/                 Core Python application code
frontend/            Static web frontend
rules/               Confirmed, merged, normalized, and review-needed rules
docs/                Project documentation and meeting/supporting materials
scripts/             Demo, export, normalization, and presentation scripts
tests/               Unit tests for API, recommender, and rule engine
quotation_snapshot.json  Source snapshot used by the MVP
requirements.txt     Streamlit runtime dependencies
requirements-api.txt Optional FastAPI backend dependencies
runtime.txt          Streamlit Community Cloud Python version
streamlit_app.py     Streamlit prototype entry point
```

## Requirements

- Python 3.11 or newer
- pip

Install the Streamlit application dependencies:

```powershell
python -m pip install -r requirements.txt
```

`requirements.txt` contains only what the Streamlit app needs. The optional
FastAPI backend has its own file:

```powershell
python -m pip install -r requirements-api.txt
```

## Run the FastAPI Backend

From the repository root (requires `requirements-api.txt`):

```powershell
python -m uvicorn app.api:app --host 127.0.0.1 --port 8000
```

Health check:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/health"
```

Expected response:

```json
{
  "status": "ok"
}
```

## Legacy Local-Only Web Frontend

In a second terminal:

```powershell
cd frontend
python -m http.server 5173 --bind 127.0.0.1
```

Open:

```text
http://127.0.0.1:5173
```

The static frontend calls the backend at `http://127.0.0.1:8000`. It is retained
only for legacy local testing and is not part of the deployed quotation
workflow. Streamlit does not call localhost FastAPI and does not use the legacy
browser-side PDF path.

## Run the Streamlit Demo

```powershell
python -m pip install -r requirements.txt
streamlit run streamlit_app.py
```

The application uses these safe defaults:

```text
DEMO_MODE=true
SHOW_INTERNAL_COSTS=false
ENABLE_LLM=false
PRICING_DATA_MODE=synthetic
```

Copy `.env.example` only when local overrides are needed. Environment files and
Streamlit secret files are ignored by Git. The current application does not load
`.env` automatically or require credentials.

`streamlit_app.py` is the only authoritative Streamlit Community Cloud entry
point.

## Integrated Demo Workflow

The Streamlit application presents one eight-step workflow:

1. start quotation,
2. conversational requirement collection,
3. product recommendation and selection,
4. deterministic pricing analysis,
5. technical and commercial validation,
6. human review, edit, approval, revision, or rejection,
7. internal/customer communication previews,
8. in-memory PDF and redacted audit downloads.

Later-stage controls remain hidden until their prerequisites are current.
Editing product, quantity, proposed price, currency, Incoterm, or delivery
location clears pricing, validation, approval, and generated communication
state. Workbook and catalog resources are cached read-only; quotation state
remains isolated in each Streamlit session.

Three synthetic scenarios are available in the sidebar:

- **Scenario A — Straight-through approval:** exact match, high confidence,
  complete cost basis, and no price override.
- **Scenario B — Manager review:** applies a controlled 16% positive deviation
  so documented override review is required.
- **Scenario C — Blocked quotation:** applies a price below the available demo
  floor and permits revision or rejection, never direct approval.

Scenario price profiles are applied once. After initial analysis, users can edit
and revalidate normally.

## Quotation Foundation

`app/quotation_models.py` defines the typed quotation draft, pricing,
validation, approval, output, audit, and workflow-state structures.
`app/workflow_state.py` provides session-safe initialization, audit append, and
reset helpers designed for `st.session_state`. No mutable quotation state is
stored globally.

Use customer-facing serialization for any payload rendered or exported for a
customer. It excludes cost, margin, minimum-price, approval, provenance, rule
artifact, and internal-evidence fields. Internal serialization must remain
restricted to authorized internal workflows.

## Requirement and Conversation Agent

`app/conversation_agent.py` implements deterministic requirement collection. Required
fields are configured centrally in `app/config.py` and collected in this order:

1. customer name,
2. sales region,
3. product request,
4. quantity,
5. currency,
6. Incoterm,
7. delivery location.

The agent merges confidently extracted values across turns and preserves prior
answers unless correction language such as `change`, `should be`, or `actually`
targets that field. A quote becomes ready for pricing only
after every required field is confirmed and a recommended product is explicitly
selected.

The Streamlit interface shows the draft summary, missing fields, current stage,
and safe recommendation choices. It does not render catalog source dictionaries
or workbook provenance.

### Interface design

The layout follows the `ui-ux-pro-max` design intelligence guidance for
enterprise SaaS/dashboard products: a Swiss-minimal structure, a trust-blue
palette (`#2563EB` primary on a `#F8FAFC` surface), and Material symbols instead
of emoji. The design is expressed entirely through the declarative theme in
`.streamlit/config.toml` and native Streamlit primitives (bordered containers,
columns, tabs, metrics, progress). No custom CSS or JavaScript is injected, so
the app stays compatible with Streamlit Community Cloud.

## Deterministic Pricing Intelligence

`PRICING_DATA_MODE=synthetic` is the deployment default. The committed
synthetic CSV preserves the normalized pricing schema and contains only fake
identifiers and commercial values. It supports exact/multiple comparables,
safe, review, blocked, and missing-data demonstrations.

An `archived_workbook` mode remains available only for authorized private local
use. The archived file and internal catalog/rule artifacts are excluded from
the public deployment package. Never enable archived mode in a public
repository or Streamlit Community Cloud app.

`SAP_BASE_CURRENCY = "USD"` remains a centralized demo assumption. No currency
conversion is performed.

The demo estimated unit-cost policy is:

```text
COGS + Installation COGS + Warranty COGS + Freight + Duty + Tariff
```

Missing additive components count as zero. Missing COGS makes estimated cost
unavailable; transfer price is never substituted for COGS.

The deterministic starting price is median strong-comparable net price, then
median comparable list price, then an available normalized decision-tree list
price. The configured demo quantity adjustment is 0% for one unit, 2% for 2–4,
4% for 5–9, and 6% for 10 or more. Configured demo floors prevent a result below
strong-comparable minimum pricing or the price needed for the 15% demo minimum
gross margin. These settings are demonstration assumptions, not approved
company policies.

Streamlit displays only safe comparable columns. Estimated cost is available
only in the clearly labelled restricted section when
`SHOW_INTERNAL_COSTS=true`; workbook paths, cells, sheet codes, and raw source
metadata are never displayed.

## Validation and Logical Judgement

Phase 4 preserves `QuotationRuleEngine` and adds a normalization layer that
passes all confidently available product, accessory, region, system,
detector/grid, and generator/tube inputs. Technical results distinguish passed,
warning, error, and not-evaluated checks. Missing technical details are not
silently treated as passed.

Commercial validation implements these deterministic demo checks:

- `CV-001`: required fields complete,
- `CV-002`: recommended price available,
- `CV-003`: proposed price is not below the safe floor,
- `CV-004`: effective gross margin meets the demo threshold,
- `CV-005`: discount is within demo authority limits,
- `CV-006`: deviation from comparable median,
- `CV-007`: pricing confidence,
- `CV-008`: cost basis completeness,
- `CV-009`: material user price override,
- `CV-010`: customer target price is not below the safe floor.

The configured demonstration thresholds are 15% minimum gross margin, 20%
margin review threshold, 10% automatic discount authority, 20% manager discount
limit, 15% deviation review, and 30% deviation block. They are not approved
company policies.

The combined decision is one of `pass`, `pass_with_warnings`,
`review_required`, or `blocked`. Technical errors and commercial safety failures
block; technical warnings/incomplete evaluation and material commercial risks
require review; only non-critical warnings produce `pass_with_warnings`.

Editing product, quantity, proposed unit price, currency, Incoterm, or delivery
location clears prior pricing and validation and appends an audit event. The
quotation cannot retain an earlier pass without re-analysis and revalidation.

## Human Approval and Structured Feedback

Phase 5 adds the explicit approval states `not_ready`, `pending_review`,
`approved`, `approved_with_override`, `rejected`, and `revision_requested`.
Available actions are derived from the logical judgement:

- `pass` can be approved,
- `pass_with_warnings` can be approved or returned for revision,
- `review_required` can be approved with documented override, returned for
  revision, or rejected,
- `blocked` can only be returned for revision or rejected.

Override, revision, and rejection actions require a reason. A blocked quotation
cannot be directly approved, and completed actions cannot be submitted twice.
Any material edit clears approval, pricing, and validation and returns the
quotation to re-analysis.

Each feedback event records quotation ID, timestamp, event type, actor,
before/after state, changed fields, reason, and triggered rule IDs. This is a
session-scoped audit trail only; it does not train a model or alter future
pricing rules.

The demo provides separate internal-audit and customer-data JSON downloads.
Both use explicit field whitelists and omit raw workbook rows, workbook paths,
sheet/cell provenance, and secrets. Customer data additionally excludes
internal cost, margin, commercial-rule, approver, reason, and override details.

Pending reviews show a simulated reminder timestamp two days after quotation
creation. No worker, scheduler, or email is created; reminder email generation
is a preview only.

## Deterministic Emails and Quotation PDF

Phase 6 adds deterministic templates for internal approval requests, simulated
two-day reminders, customer quotation emails, and rejection/revision
notifications. No email is sent. Customer email generation is allowed only for
`approved` and `approved_with_override` quotations and excludes internal costs,
margins, pricing policies, authority thresholds, rule IDs, provenance, and
internal comments.

`ENABLE_LLM=false` remains the default. A provider-neutral wording-rewriter
interface is available for future use, but no provider client or secret is
required. Rewriting can change wording only: protected quotation identifiers,
products, quantities, prices, currencies, dates, and terms are validated
exactly. An invalid rewrite is discarded in favor of the deterministic
template.

Customer quotation PDFs are generated entirely in memory with ReportLab and
downloaded through Streamlit. They contain quotation metadata, customer and
product details, commercial totals, approval information, and the demo
disclaimer. Internal costs and margins are not passed into the PDF template.
The configured validity period is 30 days.

The deployment default uses ReportLab's built-in English/Western font support.
Email content remains UTF-8. PDF input containing unsupported characters is
replaced safely rather than causing generation to fail. Chinese PDF output is
not enabled because no legally approved, deployable Chinese font is bundled.
Optional logo paths are relative or caller-supplied, and a missing logo is
ignored without failing document generation.

## Architecture and Data Boundaries

```text
Streamlit session
  -> requirement collection and catalog recommendation
  -> cached read-only synthetic pricing data
  -> technical + commercial validation
  -> guarded human approval state machine
  -> deterministic email/PDF generation
  -> in-memory downloads and redacted JSON exports
```

The default path needs no API key, network access, live SAP connection,
database, background worker, or email server. All deployment data is synthetic
and treated as USD. No FX conversion is performed.

### Security warning

This is a demonstration application, not a production quotation or
authorization system. Do not enter real customer-sensitive information or
secrets in a public deployment. Keep `SHOW_INTERNAL_COSTS=false` on
customer-accessible instances. Customer communications and PDFs exclude costs,
margins, minimum-price policy, authority thresholds, rule IDs, workbook
provenance, and internal comments.

Approval roles and names are self-declared demo inputs. They are not
authenticated and must never be interpreted as company authorization.

### Current limitations

- No live SAP, FX, authentication, persistent database, or email delivery.
- Demo thresholds and quantity discounts are not approved company policy.
- Synthetic data does not represent approved products, prices, or policies.
- Pricing currently prices the first selected main product.
- PDF deployment defaults to English/Western fonts; Chinese glyphs are not
  bundled.
- Optional private/local archived mode requires separately controlled source
  data and is not part of public deployment.

## Tests

Run the unit test suite:

```powershell
python -m unittest discover -s tests -v
```

Compile-check the application and tests:

```powershell
python -m compileall -q app streamlit_app.py tests
```

Run the deployment-focused workflow tests:

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

## Streamlit Community Cloud

The final deployment procedure, safety checks, and local-only data-source switch
are documented in `docs/streamlit_cloud_deployment.md`. The verified test matrix
and acceptance evidence are in `docs/final_demo_test_report.md`.

Cloud deployment summary:

1. Push only the safe synthetic repository to GitHub.
2. Create a Streamlit Community Cloud app.
3. Select the safe repository and branch.
4. Set the entry point to `streamlit_app.py`.
5. Deploy without API keys or secrets.
6. Verify Scenarios A, B, and C.

Deployment is a manual remaining step. No hosted URL has been tested.
