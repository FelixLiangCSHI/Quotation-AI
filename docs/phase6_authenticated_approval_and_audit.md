# Phase 6 — authenticated human approval, persistent approval tasks, permissions and audit trail

Phase 5 produces a deterministic commercial decision. Phase 6 turns the
self-declared approval demo into a persistent internal approval workflow in
which a real, authenticated internal user makes the decision, the decision
survives a restart, and every material event is auditable.

## 1. Authentication

`app/auth/` is the authentication abstraction.

| Module | Responsibility |
| --- | --- |
| `app/auth/roles.py` | Closed `Role` and `Permission` enums and the central `ROLE_PERMISSIONS` map |
| `app/auth/passwords.py` | PBKDF2-HMAC-SHA256 hashing and verification (standard library only) |
| `app/auth/provider.py` | `AuthenticatedUser`, the `AuthenticationProvider` protocol, and the enterprise SSO placeholder |
| `app/auth/local_provider.py` | The MVP provider: local accounts, hashed passwords, persistent sessions |
| `app/auth/bootstrap.py` | Creates the first administrator when the database has no user yet |

The MVP provider stores accounts in `users` and issues rows in
`user_sessions`. The UI keeps only the opaque session token; identity, roles
and permissions are resolved from the database on every interaction
(`app/services/auth_session.py`). Passwords are never stored or logged in
clear text and never appear in session state or audit records.

`EnterpriseSsoAuthenticationProvider` implements the same protocol and refuses
to authenticate until configuration is supplied, so enterprise SSO can be
added later without changing any caller.

## 2. Roles and permissions

Roles are a closed enumeration. `parse_role` is the only sanctioned way to turn
text into a role, and it rejects anything unknown, so a user can never
self-declare a privileged role.

| Role | Permissions |
| --- | --- |
| Sales User | create quotation, edit own draft, run pricing, run validation, submit quotation, view own quotations, respond to revision requests |
| Sales Manager | view approval tasks, approve PASS, approve with documented override, request revision, reject |
| Pricing Manager | the Sales Manager approval abilities, plus view detailed commercial analysis and propose commercial policy versions |
| Administrator | manage users, manage data versions, manage policy versions, view audit records, configure system settings |

The Administrator role deliberately does not carry commercial approval
authority: system administration is not the same as commercial sign-off.
Only the Pricing Manager (and the Administrator, for audit purposes) holds
`VIEW_COMMERCIAL_DETAIL`, so total cost is hidden from a Sales Manager.

## 3. Persistent approval tasks

`approval_tasks` records the task reference, quotation reference, quotation
version, decision status, assigned approver and assigned role, the submitting
user, the submission timestamp, the task status, the reminder due timestamp,
the completion timestamp, and the active policy-version, pricing-run and
validation-run identifiers.

Submission (`ApprovalService.submit_for_approval`) resolves the approver from
stored users. Free-text approvers are impossible: the caller supplies a user id
and the service verifies the user exists, is active, and holds an approver
role.

## 4. Deterministic allowed actions

`ALLOWED_ACTIONS_BY_DECISION` in `app/services/approval_service.py` is the
single source of truth:

| Decision | Allowed actions |
| --- | --- |
| `pass` | `approve`, `request_revision` |
| `review_required` | `approve_with_override`, `request_revision`, `reject` |
| `blocked` | `request_revision`, `reject` |

The Streamlit approval inbox renders exactly `view.allowed_actions`, and the
service re-derives the same set when the action is submitted. An action that
the domain layer would refuse can never be reached through the UI, and calling
the service directly is equally refused.

Reasons: `approve` takes an optional comment; `approve_with_override`,
`request_revision` and `reject` all require a reason. An override additionally
requires an explicit acknowledgement that the margin is equal to or below the
configured policy threshold.

## 5. Overrides

Every override writes an `approval_overrides` row holding the original
decision, the evaluated margin, the policy threshold, the policy version, the
approver, the approver role, the justification, the timestamp, the final
approved price, the final calculated margin and the related rule IDs.

An approver may not change the price as part of an approval. A price change is
a material edit: it must go through `apply_material_edit`, which increments the
quotation version, cancels the open task as `cancelled_stale`, invalidates the
approval and generated customer outputs, and requires pricing, validation and
the logical judgement to rerun before resubmission.

## 6. Staleness, concurrency and idempotency

An action is refused when the task refers to an older quotation version, when
pricing or validation is stale, when the policy version is missing or changed,
when the decision status changed, when the task is already completed, or when
the acting user lacks the permission. Each action carries a request id with a
unique constraint so a repeated submission cannot be applied twice, and the
whole action runs in one transaction, so a refused or failed action persists
nothing.

## 7. Audit trail

`audit_events` records the actor, actor role, event type, quotation reference,
quotation version, timestamp, before state, after state, changed fields, policy
version, triggered rule IDs and the action/request id. Events are written for
login, quotation creation and edits, pricing runs, technical validation, margin
calculation, the logical decision, submission, approver assignment, every
approval action, override justifications, revision requests, rejections, stale
task cancellation and customer output generation.

Audit records never contain credentials. `AuditViewService` requires
`VIEW_AUDIT_RECORDS` and additionally redacts any credential-shaped key before
the record is displayed. The read-only audit page is `app/ui/audit_page.py`.

## 8. Approver page

`app/ui/approval_page.py` is authenticated and shows, for each pending task,
the quotation owner, customer, quotation version, the multi-line configuration,
total revenue, total cost where the role permits it, gross margin, the active
provisional threshold, the PASS / REVIEW_REQUIRED / BLOCKED decision, the
technical validation status, data-quality flags, the triggered rule IDs, and
the AI-generated explanation clearly labelled as non-authoritative.

## 9. What Phase 6 does not do

A PASS result is never approved automatically. The system does not create an
approved status without an authorised human action, does not allow normal
approval at or below the threshold, and does not permit any approval of a
blocked quotation.
