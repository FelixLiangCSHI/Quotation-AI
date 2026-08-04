# Final Demo Test Report

## Classification

**B — Minor manual deployment step remains**

Implementation and a synthetic-only copied-package simulation pass. GitHub
publication and hosted Streamlit URL testing remain manual and have not been
performed.

## Deployment package

- Data mode: synthetic by default.
- Entry point: `streamlit_app.py`.
- Python runtime: 3.11.9.
- API keys: none.
- Network/live SAP/database/email worker: none.
- Generated documents: in memory.
- Session state: isolated per Streamlit session.

## Repository size

- Pre-exclusion workspace, excluding the local virtual environment and Python
  caches: **180,782,879 bytes (172.41 MiB)**.
- Synthetic-only copied runtime package before these two final documents:
  **283,399 bytes, 33 files**.
- Final safe allowlist after documentation: **298,196 bytes (0.284 MiB),
  36 files**.

No local file exceeds GitHub's 100 MiB individual-file limit. Large images,
source documents, internal data, legacy frontend files, and duplicate assets
are excluded from the public package.

Three exact duplicate groups were identified in local-only material:

- one decision-tree workbook copy,
- one quotation sample PDF copy,
- one rule-review CSV pair.

They were not deleted because local usage was not established; their parent
paths are excluded from deployment.

## Data-safety audit

Excluded from the public package:

- proprietary archived pricing/cost/minimum/margin data,
- internal catalog snapshots and rule artifacts,
- customer/sample documents,
- decision-tree source material,
- meeting spreadsheets,
- oversized and unused images,
- generated samples,
- legacy browser frontend and scripts.

The final security re-review classified the public allowlist as **PASS
(confidence 9/10)** with no high-confidence secrets, credentials, customer
data, or proprietary pricing.

Remaining security limitation: approval actors/roles are self-declared. The UI
and documentation mark approval as a simulation that is not authorization.

## Synthetic data coverage

The committed synthetic dataset contains:

- five exact comparable rows for the primary demo product,
- a second description-family product,
- a missing-net-price/missing-cost record,
- fake list/net/minimum/cost/additive values,
- synthetic product compatibility,
- synthetic detector/grid support,
- synthetic generator/tube specifications.

This supports safe approval, review-required deviation, blocked price-floor,
description fallback, and incomplete-data demonstrations without real values.

## Dependency verification

Pinned runtime dependencies:

- Streamlit 1.36.0,
- FastAPI 0.111.0,
- Uvicorn 0.30.1,
- HTTPX 0.27.0,
- openpyxl 3.1.5,
- ReportLab 4.2.2.

A fresh temporary virtual environment installed `requirements.txt`
successfully and imported every dependency plus `streamlit_app`. The temporary
environment was deleted. Python 3.11 was unavailable locally, so the clean
installation used Python 3.12.13; Streamlit Cloud remains pinned to Python
3.11.9.

## Test matrix

| Check | Result |
|---|---|
| Python compilation | Passed, 0 errors |
| Full unit discovery | 119/119 passed |
| Exact original baseline | 32/32 passed |
| Expanded API/recommender/rule baseline | 33/33 passed |
| Focused workflow/output/state matrix | 24/24 passed |
| Synthetic schema and default mode | Passed |
| Safe/review/blocked workflows | Passed |
| PDF signature/content/redaction/concurrency | Passed |
| Email approval gate and redaction | Passed |
| Independent workflow sessions | Passed |
| Clean requirements installation/imports | Passed |
| Streamlit initial render | Passed |
| Scenario A load and synthetic product selection | Passed |
| Pricing analysis/high confidence/validation stage | Passed |
| Synthetic-only copied package | Passed |

The final Streamlit AppTest completed initial render, Scenario A loading,
pricing analysis, and validation-stage rendering with zero application
exceptions in 29.09 seconds.

## Acceptance criteria

Verified:

- no API keys required,
- one-field-at-a-time requirement collection,
- recommendation and selection,
- multiple comparable display,
- deterministic price/total/discount/margin/confidence,
- technical and commercial validation,
- normal approval for safe quotes,
- mandatory reason for override,
- no approval for blocked quotes,
- internal/customer email previews,
- customer-output cost/margin redaction,
- in-memory PDF and JSON downloads,
- quotation reset,
- independent sessions,
- full tests passing,
- proprietary data absent from the copied public package.

## Remaining limitations

- No hosted URL has been deployed or tested.
- No authentication or real authorization.
- No persistent storage or audit database.
- No live SAP, FX conversion, or email delivery.
- Demo thresholds and prices are synthetic and not approved policy.
- English/Western PDF fonts are the deployment default.
- The Cloud build must confirm Python 3.11 dependency installation.

## Final architecture

```text
streamlit_app.py
├── session-scoped workflow orchestration
├── deterministic requirement/recommendation services
│   └── Data/synthetic/quotation_snapshot.json
├── deterministic pricing service
│   └── Data/synthetic/pricing_demo.csv
├── technical + commercial validation
├── simulated human approval state machine
├── deterministic email previews
├── in-memory ReportLab PDF
└── redacted in-memory JSON downloads
```

Excluded local-only sources:

```text
archived pricing data
internal catalog/rule artifacts
decision-tree sources
sample documents and images
legacy frontend/scripts
```

## Five-minute demo script

1. **0:00–0:30 — Safety and architecture**
   - Point out synthetic mode, no API keys, no live SAP, and session isolation.
2. **0:30–1:30 — Scenario A**
   - Load Scenario A, run pricing, show five comparables, assumptions, high
     confidence, and validation.
3. **1:30–2:15 — Approval and outputs**
   - Approve as `Demo Approver`, emphasize simulated authorization, preview the
     customer email, and download PDF/customer JSON.
4. **2:15–3:15 — Scenario B**
   - Load Scenario B, show `REVIEW REQUIRED`, demonstrate that normal approval
     is unavailable, then enter an override reason.
5. **3:15–4:00 — Scenario C**
   - Load Scenario C, show the price-floor block and that only revision/rejection
     actions are available.
6. **4:00–4:40 — State safety**
   - Edit quantity or price, show stale result invalidation, then start a new
     quotation and show the new ID.
7. **4:40–5:00 — Close**
   - Reiterate synthetic-only deployment, customer redaction, and remaining
     production gaps: authentication, approved policies, and live integrations.
