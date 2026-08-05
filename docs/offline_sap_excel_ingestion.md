# Offline SAP Excel ingestion (Phase 2)

The application has **no live SAP connection**. The only pricing input is an
offline Excel export that an authorised internal user uploads.

## Pipeline

An uploaded workbook is never used directly as the runtime pricing source. It
must pass through every stage below before it can influence a quotation:

```text
upload
 -> file validation      (app/ingestion/workbook.py)
 -> sheet selection      (list_sheets / read_sheet)
 -> column mapping       (app/ingestion/mapping.py)
 -> normalisation        (app/ingestion/normalization.py)
 -> row validation       (app/ingestion/validation.py)
 -> error quarantine     (rejected rows are stored separately)
 -> user confirmation    (app/ui/pricing_data_page.py)
 -> publication          (app/ingestion/repository.py)
 -> explicit activation  (a second, separate action)
```

## Supported inputs

| Input | Result |
| --- | --- |
| `.xlsx` | Accepted |
| `.xlsm` | Accepted, with a warning that macros are never executed |
| `.xls`, `.csv`, anything else | Rejected: unsupported format |
| Password-protected / encrypted workbook | Rejected with remediation advice |
| Renamed non-workbook, empty file, oversized file | Rejected |

## Canonical schemas

Defined in `app/ingestion/schemas.py`:

| Dataset | Required fields | Unique key |
| --- | --- | --- |
| `ProductMasterRecord` | `product_id`, `description` | `product_id` |
| `HistoricalQuotationRecord` | `quotation_id`, `product_id`, `quotation_date`, `quantity`, `net_price`, `currency` | `quotation_id` + `product_id` |
| `PricingRecord` | `product_id`, `list_price`, `net_price`, `currency` | `product_id` + `region` + `currency` |
| `CompatibilityRecord` | `product_id`, `compatible_product_id` | both |
| `CostComponentRecord` | `product_id`, `component_code`, `amount`, `currency` | all three |

Each canonical field carries a list of configurable SAP/Excel aliases (for
example `Material`, `MATNR`, `Cat#` and `SKU` all map to `product_id`). A
deployment can supply additional aliases at call time without a code change,
and a user can always override any suggestion in the mapping UI.

## Archived SAP price-list layout

The desensitised `SAP_archived` export is a *wide* price list: one row per
catalogue number with the whole cost breakdown in columns (`COGS`,
`Installation COGS`, `Warranty COGS`, `COGS I&W`, `Freight`, `Duty`, `Tariff`,
`Transfer Price`, service costs) and **no currency column** — the whole
workbook is denominated in `SAP_BASE_CURRENCY`.

Two mechanisms make that layout mappable without weakening the pipeline:

* `ColumnMappingProfile.constant_values` supplies a field that is constant for
  the whole file (here, the currency) instead of a column. A constant satisfies
  a required field, is applied to every row before normalisation, and cannot be
  set for a field that is also mapped to a column.
* `app/ingestion/sap_archived.py` records the archived header aliases and the
  currency constant once, and plans every populated sheet as a pricing dataset.

Upload it from the **1b. Upload SAP archived export** tab of the pricing data
page. Sheets that do not match the layout are reported, not silently dropped.
Everything after the mapping is the ordinary pipeline: normalisation,
validation, quarantine, explicit confirmation, publication and activation.

The pricing dataset therefore carries the full cost breakdown through to
`app/ingestion/pricing_source.py`, so the pricing engine (and the Agent 2
explanation built on it) sees a complete cost basis rather than COGS alone.

## Validation rules

| Check | Severity |
| --- | --- |
| Missing required field | reject |
| Empty price field | reject |
| Invalid numeric value | reject |
| Invalid date | reject |
| `valid_to` before `valid_from` | reject |
| Non-positive quantity | reject |
| Unsupported currency | reject |
| Malformed region code | reject |
| Duplicate product id | reject |
| Duplicate quotation / pricing row | reject |
| Missing product reference | reject |
| Inconsistent unit of measure for one product | reject |
| Net price above list price | reject |
| Net price below minimum price | reject |
| Negative price or cost | reject |
| COGS above net price (would price below cost) | warning |
| Unit alias normalised (for example `each` → `EA`) | warning |

Rows are separated into **valid**, **warning** and **rejected**. Only valid and
warning rows are published; rejected rows are stored in
`pricing_data_rejections` with their issue list and never enter the active
dataset.

## Validation report

Downloadable from the review tab as CSV (one line per issue) and Markdown (an
audit-friendly summary). A JSON summary is stored on the version row.

## Stored metadata

`pricing_data_versions` records the source filename, file hash (SHA-256),
upload timestamp, uploader, row counts (accepted / warning / rejected), the
mapping profile per dataset, the validation summary, the published version
label with its publication timestamp, and the activation timestamp.

## Raw workbook storage

Raw workbooks are **never** committed. They are written through the
`WorkbookStorage` interface; the default `LocalWorkbookStorage` writes under
`INGESTION_STORAGE_ROOT` (default `./var/pricing_uploads`, git-ignored). An
object-storage adapter can be substituted without touching the pipeline.

## Publication and activation

`PricingDataRepository` is the only writer. Three separate explicit actions:

1. `create_version_from_preview` — stages a version. It is not readable by the
   pricing engine.
2. `publish` — makes it readable. It does **not** become active.
3. `activate` — makes it the pricing engine's source. Any previously active
   version is deactivated. A previous version can be reactivated at any time.

Importing the same workbook twice is blocked by file hash
(`DuplicateImportError`) unless the user explicitly confirms a re-import.

## Synthetic fallback

`register_synthetic_fallback()` preserves `Data/synthetic/pricing_demo.csv` as
a published development version. When no version is active,
`resolve_pricing_source()` returns the synthetic dataset, so local development
and the demo continue to work without any import.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `INGESTION_STORAGE_ROOT` | `./var/pricing_uploads` | Secure raw-workbook location |
| `INGESTION_SUPPORTED_CURRENCIES` | `USD,EUR,GBP,CNY,JPY,AUD,CAD` | Currency allowlist |
| `INGESTION_MAX_UPLOAD_BYTES` | `52428800` | Upload ceiling |
| `INGESTION_REGION_PATTERN` | `^[A-Z]{2,3}(-[A-Z0-9]{1,3})?$` | Region code shape |

## Fixtures

`tests/fixtures/excel_fixtures.py` generates synthetic SAP-like workbooks
covering a valid workbook, missing columns, duplicate rows, malformed prices,
mixed currencies, unknown product ids, inconsistent units, invalid quotation
rows and a multi-sheet workbook. Run
`python -m tests.fixtures.excel_fixtures ./var/fixtures` to write them to disk
for a manual demo. All data is invented; no real customer or SAP data is used.

## Unresolved data assumptions

These need SME confirmation before pilot use:

1. **Currency conversion.** None is performed. Rows keep the currency in the
   export; a quotation in another currency is not converted.
2. **Currency allowlist.** The default list is a placeholder, not an approved
   commercial policy.
3. **Region codes.** Assumed to be ISO-3166 alpha-2/alpha-3 with an optional
   short suffix. The real SAP sales-organisation region vocabulary is unknown.
4. **Unit equivalence.** `each`/`pc`/`piece` are treated as `EA`, `kit` as
   `SET`. The authoritative SAP UoM table has not been supplied.
5. **Price relationships.** `net <= list` and `net >= minimum` are treated as
   hard errors, and `cogs > net` as a warning. Whether a legitimate SAP export
   can violate these is unconfirmed.
6. **Uniqueness keys.** Pricing uniqueness assumes product + region + currency.
   If real exports carry price-list or validity-period dimensions, this key is
   too narrow and would wrongly flag duplicates.
7. **Dates.** Ambiguous `dd/mm` versus `mm/dd` strings are read day-first. Real
   exports should use native Excel dates or ISO strings.
8. **Uploader identity.** Captured as free text until authentication arrives in
   a later phase; it is not yet a verified principal.
9. **Multi-dataset publication.** A published version currently holds all
   mapped datasets from one workbook. Whether datasets should be versioned
   independently is undecided.
