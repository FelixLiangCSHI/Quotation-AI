# Frontend UX gap analysis

Analysis of the Streamlit frontend before the enterprise-experience changes in
this iteration. It covers the entry point, authentication, navigation, session
handling and the quotation, approval and pricing pages.

## 1. Current page structure

Entry point: `streamlit_app.py`. It is an application shell, not a page. It

- promotes Streamlit secrets to environment variables (`app/runtime.py`),
- ensures the database schema exists,
- evaluates the session (`app/ui/session_guard.py`),
- renders the login page when unauthenticated,
- otherwise renders the sidebar and the selected workspace.

Pages live in `app/ui/`:

| Key | Module | Section | Required permission |
| --- | --- | --- | --- |
| `dashboard` | `dashboard_page.py` | Workspace | none (any authenticated user) |
| `create_quotation` | `quotation_workspace.py` | Sales | `CREATE_QUOTATION` |
| `my_quotations` | `my_quotations_page.py` | Sales | `VIEW_OWN_QUOTATIONS` |
| `approval_center` | `approval_page.render` | Approval | `VIEW_APPROVAL_TASKS` |
| `approval_history` | `approval_page.render_history` | Approval | `VIEW_APPROVAL_TASKS` |
| `documents` / `email` | `documents_page.py`, `email_page.py` | Output | quotation permissions |
| `pricing_data` | `pricing_data_page.py` | Data | `MANAGE_DATA_VERSIONS` |
| `policy` / `users` / `system` | `admin_pages.py` | Administration | `MANAGE_POLICY`, `MANAGE_USERS` |
| `audit` | `audit_page.py` | Administration | `VIEW_AUDIT_RECORDS` |

Supporting modules: `navigation.py` (workspace registry and permission
filtering), `dashboard_data.py` (read-only metrics), `session_guard.py` (idle
timeout), `login_page.py` (sign-in form).

## 2. Current user flow

```
open app -> bootstrap secrets -> ensure schema -> evaluate session
        -> not authenticated: login page (only reachable page)
        -> authenticated: resolve default page for role -> sidebar + workspace
```

Routing is already role driven: `pages_for(user)` filters `WORKSPACES` by
permission and `default_page_key(user)` picks the landing page, so there is no
manual "workflow A / workflow B" choice left in the shell. A sales user lands
on a sales dashboard, an approver on the approval-oriented dashboard, a pricing
manager on pricing management and an administrator on the admin view.

Authentication uses the existing backend only: `app/services/auth_session.py`
wraps `LocalPasswordAuthenticationProvider`. Session state stores an opaque
session token plus the active user id; identity, roles and permissions are
re-resolved from the database on every interaction. No password or secret is
stored in session state.

## 3. Existing reusable components

- `app/ui/navigation.py` — permission-filtered workspace registry, landing page
  resolution and workspace labels; pure data, unit-testable.
- `app/ui/session_guard.py` — idle-timeout evaluation and timeout notice.
- `app/ui/dashboard_data.py` — role-aware dashboard metrics from the service
  layer.
- `app/services/auth_session.py` — sign in / sign out / current user.
- `app/auth/roles.py` — the closed role set and the permission matrix.

## 4. Missing enterprise UX components

1. **Product identity on the login page.** The sign-in screen shows a generic
   "Sign in" title rather than the product name, tagline and AI-assisted
   workflow subtitle expected from an internal enterprise application.
2. **Usable demo credentials.** Demo accounts must be seeded manually with a
   CLI and an environment variable, so a fresh Streamlit Cloud deployment has
   no account anyone can sign in with, and the login page can only pre-fill a
   username. A demo-mode deployment needs seeded accounts and a visible
   credentials hint.
3. **Weak-password policy escape for demo seeds.** The password policy has an
   eight-character minimum, which blocks short shared demo passwords even when
   the deployment is explicitly a synthetic demo.
4. Minor: no explicit authentication timestamp surfaced to the UI beyond the
   session guard's activity tracking.

## 5. Proposed migration plan

1. Keep the shell, routing and permission model unchanged — they already meet
   the role-driven requirements.
2. Rework the login page header into the product identity block (title,
   subtitle, tagline) while keeping the same form and the same authentication
   backend.
3. Give the demo accounts a default shared password and seed them
   automatically at startup when demo mode is on, so the demo credentials
   section on the login page is accurate. Seeding stays idempotent and never
   touches existing accounts; non-demo deployments keep the environment-driven
   behaviour and the full password policy.
4. Update the deployment documentation to describe the default demo password
   and how to override it.
