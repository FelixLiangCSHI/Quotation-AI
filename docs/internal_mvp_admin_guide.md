# Internal MVP — Administrator guide

This guide is for an **Administrator** who manages users, pricing data, the
commercial policy and the operational health of the internal MVP.

## Your permissions

`configure_system`, `manage_data_versions`, `manage_policy_versions`,
`manage_users`, `view_approval_tasks`, `view_audit_records`,
`view_commercial_detail`.

Note that an administrator deliberately does **not** hold `create_quotation` or
`approve_pass`. Separation of duties is intentional: the person who controls
the pricing data is not the person who approves the commercial outcome.

## Managing users

Create every account explicitly with one of the four known roles:
`sales_user`, `sales_manager`, `pricing_manager`, `administrator`. Roles are a
closed enumeration — an unknown role string is rejected, so no one can be given
a privileged role through free text.

Passwords are stored as salted PBKDF2-HMAC-SHA256 hashes with 240,000
iterations. Plaintext passwords are never logged, never stored and never
included in an audit record. Sessions are database rows with a 12-hour expiry
and can be revoked.

After five failed sign-in attempts inside a fifteen-minute window an account is
temporarily locked. Tune this with `AUTH_MAX_FAILED_LOGINS`; setting it to `0`
disables the lockout and is not recommended.

## Importing offline SAP pricing data

There is no live SAP connection. The only input is an offline `.xlsx` or
`.xlsm` export, and only an administrator can import it.

The import is a deliberate multi-step process — nothing is ever activated
implicitly:

1. **Upload.** The file is validated before it is parsed: extension allowlist,
   size ceiling (50 MB by default), ZIP magic bytes, refusal of encrypted,
   password-protected and legacy OLE2 workbooks, decompression-bomb guards
   (uncompressed size, compression ratio, entry count) and rejection of unsafe
   internal archive paths. Macro-enabled workbooks are accepted but flagged;
   macros are never executed. Sheets are read with formula evaluation disabled,
   so a spreadsheet formula can never run.
2. **Map columns.** A suggested mapping is offered; you confirm or correct it.
3. **Review.** Normalisation and row validation produce a report with accepted,
   warning and rejected rows. Rejected rows are quarantined with a reason, and
   you can export the report as CSV or Markdown.
4. **Publish.** Publishing creates a new pricing data version recorded with your
   authenticated username, the source filename and the SHA-256 file hash. A
   duplicate file hash is refused unless you explicitly force a re-import.
   **Publishing does not change what pricing uses.**
5. **Activate.** A separate, explicit action makes a published version the
   active pricing source. Exactly one version is active at a time. The previous
   version is retained, so you can roll back by activating it again.

Publishing and activation both require `manage_data_versions` and are recorded
in the audit trail as `pricing_data_version_published` and
`pricing_data_version_activated`.

Uploaded workbooks are written under `INGESTION_STORAGE_ROOT`, which must point
outside the repository. Raw workbooks are never committed.

## Managing the commercial policy

See `docs/commercial_policy_configuration.md` for the full detail. In short:
the active policy is `POL-MARGIN-MVP-001@1.0.0` with a provisional 35%
threshold, a `greater_than` comparison and a block-on-missing-cost rule.

A policy change is always a **new version**, never an edit of the active one.
Decisions record the policy version that produced them, and a task raised under
an older version becomes stale.

## Operational status

Two equivalent surfaces report system health, neither of which exposes a
credential or a raw connection string:

```bash
# Command line
python -m app.operations.cli

# HTTP, when the FastAPI service is running
curl http://localhost:8000/status
curl http://localhost:8000/status/database
```

Reported components: application, database, migrations, active pricing-data
version, active commercial-policy version and current pass-margin threshold,
email-provider configuration, Agent 1–4 provider status, reminder-worker last
run and document storage.

Database URLs are reduced to a driver, host and database summary. Secret-backed
settings report only whether the value is *present*, never the value itself.

## Document storage and retention

Generated customer documents are stored in the database with their SHA-256
hash, the quotation version and the approval action that authorised them.

- A material quotation edit **supersedes** every earlier customer document.
  Nothing is deleted.
- A superseded document cannot be downloaded through the normal customer-safe
  route. An auditor can retrieve it explicitly for historical purposes and it
  stays associated with its original quotation version.
- `export_internal_audit_document` produces a restricted JSON register of every
  document for a quotation. It requires `view_audit_records`.

## Fonts and brand assets

No font file is committed to this repository, and no licensed font may be
added. Point `DOCUMENT_FONT_REGULAR_PATH` and `DOCUMENT_FONT_BOLD_PATH` at a
licensed font on the host. If they are unset or unreadable the renderer falls
back to a built-in face, records a warning and still produces the document —
including for non-Western text.

Logos and approved marketing figures live in `DOCUMENT_ASSET_ROOT`
(`./assets/documents` by default) and are referenced by **bare file name only**.
Absolute paths, relative traversal, sub-directories and URLs are all rejected,
and the renderer never fetches a remote resource.

## Backups

Back up the database — it holds quotations, approvals, audit events, email
records and generated documents — and the `INGESTION_STORAGE_ROOT` directory,
which holds the source workbooks.
