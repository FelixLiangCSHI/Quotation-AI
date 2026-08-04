# Streamlit Community Cloud Deployment

## Readiness

Classification: **B — Minor manual deployment step remains**.

The synthetic-only package is technically verified. A repository has not been
created or pushed from this workspace, and no hosted Streamlit URL has been
tested.

## Mandatory data-safety boundary

Public deployment must use:

```text
PRICING_DATA_MODE=synthetic
DEMO_MODE=true
SHOW_INTERNAL_COSTS=false
ENABLE_LLM=false
```

The defaults already match these values. Do not configure
`PRICING_DATA_MODE=archived_workbook` in Streamlit Community Cloud.

The public repository must not contain:

- archived pricing workbooks,
- internal catalog snapshots or rule artifacts,
- decision-tree source files,
- source/sample PDFs,
- meeting spreadsheets,
- generated internal/customer samples,
- legacy frontend assets,
- large product images.

The repository `.gitignore` excludes those categories. `.gitignore` does not
remove files already tracked in Git history, so verify the actual index and
history before pushing.

Prefer a private GitHub repository and restricted deployment if organizational
policy supports it. A public repository is acceptable only for the verified
synthetic allowlist.

## Safe repository preparation

1. Create a new empty GitHub repository. Prefer private visibility.
2. Initialize Git in a clean copy of this workspace, not in a folder containing
   unrelated files.
3. Copy or stage only:
   - `app/*.py`,
   - `Data/synthetic/**`,
   - `streamlit_app.py`,
   - `.streamlit/config.toml` (theme only; never `secrets.toml`),
   - `requirements.txt`,
   - `runtime.txt`,
   - `.gitignore`,
   - `.env.example`,
   - `README.md`,
   - the three allowlisted deployment/checklist documents.
4. Run `git status --short` and inspect every staged path.
5. Run `git ls-files` and confirm no excluded data or asset path appears.
6. If any proprietary file was ever committed, create a fresh clean repository
   or remove it from Git history before publication.
7. Do not use Git LFS for runtime data; the synthetic runtime files are small.

## Local verification

From the safe repository root:

```powershell
python -m pip install -r requirements.txt
python -m compileall -q app streamlit_app.py
streamlit run streamlit_app.py
```

The app requires no API key, database, live SAP connection, email server,
background worker, or localhost API.

## Streamlit Community Cloud steps

1. Push the reviewed safe repository to GitHub.
2. Sign in to Streamlit Community Cloud.
3. Select **Create app**.
4. Choose the safe repository and deployment branch.
5. Set the entry point to:

   ```text
   streamlit_app.py
   ```

6. Do not add API keys or proprietary data to Streamlit secrets.
7. Do not override `PRICING_DATA_MODE`.
8. Deploy.
9. Open the hosted URL in a new browser session.
10. Verify:
    - Scenario A reaches normal approval and customer outputs.
    - Scenario B requires an override reason.
    - Scenario C cannot be approved.
    - PDF and JSON downloads use the current quotation ID.
    - a second private/incognito session receives a different quotation ID.

## Authorized private local archived mode

Archived mode is not a Cloud deployment option. For authorized local testing
only:

```powershell
$env:PRICING_DATA_MODE = "archived_workbook"
streamlit run streamlit_app.py
```

Keep the private source file outside any public repository and follow
organizational access, retention, and handling policies. Reset the environment
variable to `synthetic` before public testing.

## Security and privacy limitations

- Approval identity is self-declared and unauthenticated. It is a workflow
  simulation, not company authorization.
- Never enter real customer-sensitive information or secrets in a public demo.
- Internal views contain synthetic values only and are not approved policies.
- No data persists after the Streamlit session ends.
- No email is sent.
- Customer PDF support defaults to English/Western fonts.
- `runtime.txt` requests Python `3.11`. Streamlit Community Cloud only supports
  major.minor versions, so an exact patch pin such as `python-3.11.9` makes the
  build fail.

## Post-deployment record

Record the deployed URL, commit SHA, deployment date, repository visibility,
and acceptance tester only after the hosted app has been tested. This workspace
does not claim a successful deployment.
