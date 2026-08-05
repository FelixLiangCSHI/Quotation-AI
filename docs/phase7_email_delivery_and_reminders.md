# Phase 7 — email composition, delivery and two-day approval reminders

Phase 7 turns the deterministic email *previews* of the earlier phases into a
real, approval-gated email workflow with pluggable delivery adapters and a
persistent reminder worker.

## Separation of responsibilities

| Concern | Module |
| --- | --- |
| Trusted facts and deterministic templates | `app/emailing/composition.py` |
| AI wording assistance (Agent 3) and its validation | `app/emailing/composition.py` + `app/agents/` |
| Recipient resolution and validation | `app/emailing/recipients.py` |
| Delivery adapters | `app/emailing/providers.py` |
| Delivery persistence and use cases | `app/emailing/service.py`, `EmailRecord` |
| Reminder scheduling | `app/services/approval_service.py` (due time), `app/emailing/reminders.py` (worker) |
| Process entry point | `worker/reminder_worker.py` |
| Read-only operator views | `pages/4_Email_centre.py` |

Every trusted value — quotation ID and version, customer, product IDs and
descriptions, quantities, prices, total revenue, gross margin, policy
threshold, decision status, currency, dates, Incoterm, approval status and
approver identity — is read from persisted domain state by
`build_email_facts()`. Agent 3 may only rewrite wording: its output passes
schema validation, protected-fact validation and customer/internal boundary
validation. Any failure discards the AI output, uses the deterministic
template, and records the fallback reason on the email record.

## Email types

| Type | Audience | Gate |
| --- | --- | --- |
| `approval_request` | assigned approver | pending task; margin and threshold only when the recipient may see commercial detail |
| `approval_reminder` | assigned approver | still pending, due, and below the configured reminder count |
| `customer_quotation` | customer | `approved` or `approved_with_override`, plus an explicit human draft review |
| `revision_request` | quotation owner | internal only; rule references only with commercial-detail permission |
| `rejection_notification` | quotation owner | internal only, never customer-facing |

Customer emails are rendered from a customer-safe template and are additionally
scanned for internal terms (margin, cost, threshold, override, rule, comment)
before they can be delivered.

## Delivery providers

`EmailDeliveryProvider` is a `Protocol` with a single
`send(*, message, idempotency_key) -> EmailDeliveryResult` method.

- `ConsoleEmailProvider` — local development and tests; keeps sent messages in
  memory and deduplicates repeated idempotency keys.
- `SMTPEmailProvider` — real SMTP, with the password read from the environment
  variable *named* by `SMTP_PASSWORD_ENV`.
- `MicrosoftGraphEmailProvider` — complete request builder and validation;
  configuration-gated by `GRAPH_ENABLED` and the presence of the tenant,
  client and secret environment variables. It refuses to send while
  unconfigured instead of failing silently.

No secret, tenant ID, API key or real email address is present in the source.

## Environment variables

Names only; values are supplied by the deployment environment.

```
EMAIL_DELIVERY_PROVIDER          console | smtp | graph
EMAIL_SENDER_ADDRESS             internal sender address
EMAIL_INTERNAL_DOMAINS           comma-separated internal domains
EMAIL_ALLOW_CUSTOMER_DELIVERY    true to permit customer-facing delivery
EMAIL_AUTO_SEND_APPROVAL_REQUEST true to send on submission automatically
EMAIL_BODY_STORAGE               hash | redacted | full
EMAIL_MAX_DELIVERY_ATTEMPTS      bounded retry budget per email
EMAIL_TEMPLATE_VERSION           persisted with every email record
APPROVAL_REMINDER_DELAY_HOURS    default 48 (two calendar days)
APPROVAL_REMINDER_MAX_COUNT      default 1
SMTP_HOST
SMTP_PORT
SMTP_USERNAME
SMTP_PASSWORD_ENV                name of the variable holding the password
SMTP_USE_TLS
SMTP_TIMEOUT_SECONDS
GRAPH_TENANT_ID_ENV              name of the variable holding the tenant ID
GRAPH_CLIENT_ID_ENV              name of the variable holding the client ID
GRAPH_CLIENT_SECRET_ENV          name of the variable holding the secret
GRAPH_SENDER_USER_ID
GRAPH_BASE_URL
GRAPH_ENABLED
```

## Body storage

`EMAIL_BODY_STORAGE` defaults to `hash`: only a SHA-256 body hash and the
template metadata are persisted. `redacted` stores a shape-only placeholder,
and `full` stores the body for environments where policy allows it. The hash
and template version are always stored, so a delivered email remains
verifiable without retaining sensitive content.

## Reminders

`ApprovalService.submit_for_approval` persists `reminder_due_at` as the
submission time plus `APPROVAL_REMINDER_DELAY_HOURS` (default two days). The
worker:

1. queries pending tasks whose reminder is due,
2. claims each task in a transaction (`FOR UPDATE SKIP LOCKED` where the
   database supports it),
3. rechecks the status inside the claim,
4. builds the idempotency key
   `quotation_id|quotation_version|approval_task_id|two_day_pending_approval|cycle`,
5. sends at most once per cycle and persists success or failure,
6. skips completed, rejected, revised, cancelled and stale tasks,
7. retries transient failures after a bounded backoff, and stops on permanent
   recipient or configuration errors.

Because every piece of reminder state is a database column, restarting the web
process — or running two workers at once — cannot lose or duplicate a
reminder.

### Running the worker

```bash
python -m worker.reminder_worker --run-once          # cron or container job
python -m worker.reminder_worker --interval-seconds 900   # simple loop
```

A cron entry or container schedule invoking `--run-once` is the recommended
MVP mechanism. The scheduler is deliberately *not* inside Streamlit; the
Email centre page only displays reminder state and offers manual retries.

## Tests

- `tests/unit/test_phase7_email_composition_and_delivery.py` — Agent 3
  deterministic mode, accepted rewrite, timeout fallback, malformed-response
  fallback, protected-value contradiction fallback, recipient validation,
  configuration, console/SMTP/Graph providers.
- `tests/integration/test_phase7_email_workflow.py` — approval request routing
  and wording, approver substitution refusal, customer approval gating and
  boundary, PDF version matching, owner notifications, persistence and retry.
- `tests/integration/test_phase7_reminders.py` — two-day due time, no early
  send, single send when due, suppression after approval/revision/rejection/
  stale cancellation, duplicate-run and duplicate-worker safety, transient
  retry, permanent failure recording, restart survival, CLI `--run-once`.
