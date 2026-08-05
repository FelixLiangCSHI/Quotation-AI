# Internal MVP — Security review

Scope: the internal quotation MVP at the end of Phase 8. This review covers
file upload, authentication, authorisation, secrets, AI boundaries and document
rendering. Findings are stated honestly, including what is **not** yet done.

## Summary

| Area | Status |
| --- | --- |
| Excel / file upload | Hardened |
| Authentication | Hardened, with residual items |
| Authorisation | Hardened |
| Secrets | Hardened |
| AI boundaries | Hardened |
| Document rendering | Hardened |
| Transport and cookies | **Deployment responsibility — not enforced by the application** |

---

## 1. Excel and file upload

| Control | Status | Where |
| --- | --- | --- |
| Size ceiling, 50 MB default, configurable | Present | `app/ingestion/workbook.py`, `INGESTION_MAX_UPLOAD_BYTES` |
| Extension allowlist (`.xlsx`, `.xlsm`) | Present | `app/ingestion/config.py` |
| Content validation by ZIP magic bytes | Present | `app/ingestion/workbook.py` |
| Encrypted / password-protected / legacy OLE2 refusal | Present | `app/ingestion/workbook.py` |
| ZIP-bomb protection: uncompressed-size cap | Present | `_assert_not_a_zip_bomb` |
| ZIP-bomb protection: compression-ratio cap | Present | `_assert_not_a_zip_bomb` |
| ZIP-bomb protection: archive entry count cap | Present | `_assert_not_a_zip_bomb` |
| Unsafe internal archive path refusal | Present | `_assert_not_a_zip_bomb` |
| Per-sheet row cap | Present | `read_sheet` |
| Macros never executed, workbook flagged | Present | `app/ingestion/workbook.py` |
| No formula execution (`data_only=True`, `keep_links=False`) | Present | `_load_workbook` |
| Safe filenames — basename only | Present | `validate_workbook_file`, `LocalWorkbookStorage.store` |
| Path-traversal prevention on retrieval | Present | `LocalWorkbookStorage._path_for` confines every resolved path under the configured root |

Declared sizes are checked **before** any entry is read, and no archive entry is
ever extracted to disk.

## 2. Authentication

| Control | Status | Where |
| --- | --- | --- |
| PBKDF2-HMAC-SHA256, 240,000 iterations, 16-byte random salt | Present | `app/auth/passwords.py` |
| Constant-time verification | Present | `app/auth/passwords.py` |
| Session token from `secrets.token_urlsafe(32)` | Present | `app/auth/provider.py` |
| Persistent sessions with a 12-hour expiry and revocation | Present | `app/auth/local_provider.py` |
| Inactive-user check on every session resolution | Present | `resolve_session` |
| Username-enumeration protection — identical error | Present | `authenticate` |
| Failed-attempt lockout (5 attempts / 15 minutes, configurable) | Present | `AUTH_MAX_FAILED_LOGINS` |
| Plaintext passwords never logged or stored | Present | Verified by test |
| Session token excluded from the audit record | Present | `authenticate` |

**Residual items.** The lockout counter is per-process and in-memory, so a
multi-process deployment throttles per worker rather than globally; a shared
counter is the correct fix if the MVP is scaled out. There is no idle timeout
and no token rotation on a role change. The password policy enforces a minimum
length only. Secure, `HttpOnly` and `SameSite` cookie attributes are a reverse
proxy responsibility and are not set by the application.

## 3. Authorisation

| Control | Status | Where |
| --- | --- | --- |
| Closed role enumeration; no free-text roles | Present | `app/auth/roles.py` |
| Central role → permission map | Present | `ROLE_PERMISSIONS` |
| Backend permission checks in every service | Present | `approval_service`, `document_service`, `audit_view`, `emailing/service` |
| Object-level quotation access (owner, approver or auditor) | Present | `DocumentService._require_object_access` |
| Per-action approval permission and assignee check | Present | `ApprovalService.act` |
| A blocked quotation cannot be approved by any route | Present | `allowed_actions_for` |
| Normal approve unavailable at or below the threshold | Present | `allowed_actions_for` |
| Policy-version staleness guard on approval | Present | `ApprovalService.act` |
| Administrator-only pricing-data publish and activate | Present | `app/services/pricing_data_admin.py` |
| Administrator-only view of the pricing data import page | Present | `pages/1_Pricing_data_import.py` |
| Document generation and download are permission-checked and audited | Present | `DocumentService` |
| Internal audit export requires `view_audit_records` | Present | `export_internal_audit_document` |

The uploader identity recorded against a pricing data version is the
authenticated principal, not a free-text field.

**Residual item.** `manage_users` and `configure_system` are defined and
granted, but user creation does not yet take an acting principal, so account
management is protected by process rather than by a code check.

## 4. Secrets

| Control | Status |
| --- | --- |
| Environment-only loading, resolved at the point of use | Present |
| Configuration stores only the **name** of the variable holding a secret | Present |
| Describe methods report presence booleans, never values | Present |
| Agent audit records are scrubbed of secrets | Present |
| Database URLs redacted to driver, host and database in status output | Present |
| No `.env` committed; `.env.example` contains placeholders only | Present |
| No secret in any export or generated document | Present |

**Residual item.** There is no integration with a dedicated secret manager
(Vault, Key Vault, Secrets Manager). Environment variables are the only source.
`DATABASE_ECHO` can print SQL to stderr and must stay disabled outside
development.

## 5. AI boundaries

| Control | Status |
| --- | --- |
| Protected-field validation — a model cannot drop or alter a trusted commercial fact | Present |
| Prohibited internal fields rejected in customer-facing agent output | Present |
| No new commercial claims from an agent | Present |
| Instructions separated from business data (system prompt versus input payload) | Present |
| Per-agent timeout, default 30 s | Present |
| Deterministic fallback and circuit breaker for every agent | Present |
| Agent 4 cannot alter the quotation ID, version, customer, product IDs, quantities, prices, totals, currency, validity date, Incoterm, delivery assumptions, approval status, approver or policy status | Present — the strict `DocumentPlan` schema has no field for any of them, and a smuggled field is a schema violation |
| Agent 4 text is sanitised before template insertion | Present |
| No raw confidential data logged by default | Present |

**Residual item.** An agent `error_detail` is logged verbatim at INFO. It is
scrubbed of known secret patterns but could still echo provider text. Keep the
log level at WARNING in a shared environment.

## 6. Document rendering

| Control | Status | Where |
| --- | --- | --- |
| Sandboxed Jinja environment, autoescaping, `StrictUndefined`, cleared globals | Present | `app/documents/renderer.py` |
| Templates loaded only from the package template directory | Present | `render_quotation_html` |
| Agent text **stripped**, not escaped — tags, template expressions, URLs, paths and control characters removed | Present | `sanitize_plan_text` |
| Internal-disclosure scan on every free-text field | Present | `contains_internal_disclosure` |
| Final customer-safety scan of the rendered body | Present | `_assert_customer_safe` |
| No arbitrary local file access — assets resolve by bare file name inside an approved root | Present | `app/documents/assets.py` |
| No arbitrary external network access during rendering — no remote `src`, no `@import`, no script | Present | Verified by test |
| Safe output filenames — no separator, no traversal | Present | `safe_document_filename` |
| Charts are customer-safe by construction — revenue and quantity only | Present | `app/documents/charts.py` |
| Generation only for `approved` or `approved_with_override` | Present | `_assert_generation_allowed` |
| Material edit supersedes prior customer documents; nothing is deleted | Present | `invalidate_for_material_edit` |
| Document metadata records the hash, version and approval action | Present | `GeneratedDocument` |

Defence in depth: the agent guard rails, the plan sanitiser and the rendered-body
scan are three independent layers. A failure in any one of them is caught by the
next.

## 7. Verification performed

- `python -m pytest -q` — full unit, integration and end-to-end suite, green.
- `python -m alembic upgrade head` from an empty database, plus a full
  downgrade-to-base and re-upgrade round trip.
- `python -m compileall` across `app`, `worker`, `pages`, `tests`, `migrations`.
- `ruff check` (`F`, `E9`) over the Phase 8 modules and their tests — clean.
- `pip-audit -r requirements.txt` — **no known vulnerabilities**.
- Secret scan over every changed file — clean.
- CodeQL static analysis.

## 8. Open risks

| Severity | Risk | Mitigation |
| --- | --- | --- |
| Medium | Login lockout is per-process, so it weakens under horizontal scaling | Move the counter to the database or a shared cache before scaling out |
| Medium | Cookie security attributes and HTTPS are not enforced by the application | Terminate TLS at a proxy and set the attributes there |
| Low | No secret-manager integration | Acceptable for an internal MVP; revisit before production |
| Low | `manage_users` not enforced in code | Restrict administrative access by process for now |
| Low | No session idle timeout or rotation on a role change | Keep the 12-hour lifetime short |
| Low | Weak password policy (length only) | Set expectations administratively |
| Low | Agent `error_detail` logged verbatim | Run at WARNING in shared environments |
| Low | No MIME declaration check beyond magic bytes | Magic bytes plus the openable-archive check is a stronger signal than a client-supplied header |
