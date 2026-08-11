# PLAN.md — v0.9.1 Phase B: Export / Import / Reconciliation

Grounded in the ESOP_test repo as of `VERSION` = `0.9.1` (phase A already shipped per `docs/qa/v0.9.1.md:3-7`). Produced from an approved feature spec (five-perspective expert fan-out + tax-domain-expert sign-off, all approved by the participant). All file:line references below were read directly from the repo.

**Status: IN PROGRESS — steps 1-11 of §8 complete (see "Implementation notes" after each step below, and `HANDOFF.md` for the session-close context). Next: step 12 (update `docs/qa/v0.9.1.md`). Read this file in full before continuing, not just `HANDOFF.md`.**

---

## 1. Current architecture and relevant data flow

**FK graph (from `backend/app/models.py`, confirmed byte-for-byte against `database/init_scheme.sql`, which `tests/test_project_invariants.py:309-362` enforces column-by-column):**

```
Company (models.py:23)
 ├─ OptionPool (models.py:34, company_id FK, CHECK ck_option_pools_shares_balance)
 ├─ Employee (models.py:53, company_id FK, nullable)
 └─ Trustee (models.py:75, company_id FK)
      Grant (models.py:82: employee_id, pool_id, trustee_id? FK)
       └─ VestingSchedule (models.py:102, grant_id FK, UNIQUE → true 1:1)
       └─ Document (models.py:463: grant_id, company_id, employee_id, trustee_id? — all four duplicated directly on the row, not derived via join)
       └─ ExerciseRequest (models.py:262: grant_id, employee_id)
User (models.py:213: company_id?/trustee_id?/employee_id? — exactly one populated by role, not DB-enforced)
 └─ UserSession (models.py:236) — EXCLUDED per decision 1
AuditLog (models.py:248) — entity_type/entity_id, resolved by dispatch, no FK
NotificationPreference / NotificationDismissal (models.py:297, 318) — user_id FK
LedgerEvent (models.py:391) — aggregate_type/aggregate_id, no FK to the domain tables themselves
LedgerOwnership (models.py:426) — EXCLUDED (recomputed on import, per decision 1)
TaxRulePack (models.py:119) / TaxRatesHistory (models.py:143) / IncomeTaxBracket (models.py:169) — global reference data keyed by (country_code, grant_type, effective_start_date[, bracket_order]), not company-owned
StockPricesHistory (models.py:195) — company_id FK — NOT mentioned in the approved scope list (flagged at the end)
```

**Router split (`backend/app/main.py:6-9, 46-57`):** 12 domain routers registered under `/api/v1`, each a bare `APIRouter()`. `tests/test_project_invariants.py:415-454` enforces that every `backend/app/api/*.py` module is mounted exactly once and that no two routers claim the same `(path, method)`. A new `export.py` router must follow this pattern exactly or the invariant test fails.

**company_id-scoping pattern** — three variants already in the codebase, all relevant:
- Direct column filter, no join: `documents.py:185` (`Document.company_id == current_user.company_id`), `employees.py:27`.
- Dispatch by `entity_type` on rows that don't carry `company_id` directly: `audit.py:14-38` — `Employee`/`Grant`+`TaxSimulation`/`Company`/`ExerciseRequest` each have a bespoke ownership derivation. The full set of `entity_type` values actually written is **`Company, Document, Employee, ExerciseRequest, Grant, TaxSimulation, User, VestingSchedule`** (verified via `grep -rn "record_audit_event(db, \""` across `backend/`, plus `exercise_requests.py:222,236` which use a two-line call the naive grep missed). `audit.py`'s dispatch does **not** yet cover `Document`, `VestingSchedule`, `User`, or `TaxSimulation` as separate branches — it folds `TaxSimulation` into `Grant` (`audit.py:22-23`) and has no branch at all for `Document`/`VestingSchedule`/`User`. Export's AuditLog scoping needs a superset of this dispatch.
- Ledger ownership index, never projected data: `ledger.py:23-35` (`_assert_ledger_ownership`) — checks `LedgerOwnership` rows, not `project()` output. `document_access.py:19-35` is the single-entry-point pattern the plan should copy for the new export access check.

**Ledger's bitemporal design (`services/ledger.py`):** `append_event` (line 56) is the only write path; `_next_sequence_no` (line 47) computes `(aggregate_id, sequence_no)`, uniquely constrained at the DB level (`models.py:397`, `uq_ledger_events_aggregate_seq`). `ledger_events` is append-only by DB trigger (`init_scheme.sql:392-396`, `trg_ledger_events_no_update`/`no_delete`) — confirmed live in tests via `test_ledger_replay.py:142-181`. `PROJECTORS` (ledger.py:220-226) fold events per aggregate type; `project()` (line 229) is the generic entry point. This is exactly the mechanism decision 4 (reconciliation) needs to invoke against imported events, and exactly why decision 5 (idempotency) is safe to implement as "skip if `(aggregate_id, sequence_no)` already exists" — never `append_event` (which reassigns `sequence_no` and defaults `recorded_at`), and never an UPDATE (blocked by the DB trigger regardless).

**Tax engine's rule-pack loading (`services/tax_engine.py`):** `TaxCalculationEngine.calculate_tax` (line 64) selects the newest `TaxRulePack` with `effective_start_date <= exercise_date` for `(country_code, grant_type)` (lines 67-76), then dispatches on `pack.calculation_method` (line 94) to `_calculate_flat` (reads one `TaxRatesHistory` row, line 100, unique per pack) or `_calculate_progressive` (reads all `IncomeTaxBracket` rows ordered by `bracket_order`, line 113). Returns `TaxCalculationResult` (line 44) carrying `pack_id`, `source_url`, `effective_rate`, `tax_amount`, `method` — **but this dataclass is only ever populated on the `/employee/simulate-exercise` preview path** (`exercise_requests.py:212-243`, which persists it as free-text JSON in `AuditLog.after_value` at line 235). The approval path (`_decide_exercise_request`, `exercise_requests.py:111-130`) **never calls `TaxCalculationEngine` at all** — it only flips `status`, sets `reviewed_by_user_id`/`reviewed_at`/`review_notes`, and appends `EXERCISE_REQUEST_DECIDED`. This is the exact gap decision 2 asks to close, and it is bigger than "persist it differently" — **tax must be computed at approval time where today it is not computed at all.**

**RESOLVED, then corrected during implementation:** the participant first approved `business_today()` at the moment of approval. Implementing it tripped `tests/test_project_invariants.py::test_the_clock_is_never_the_source_of_a_tax_date` — a real, deliberate invariant (not a false positive) that bars exactly this: a tax-dated field must come from a document/action, never from "the clock right now," because Israel is ahead of UTC and the US is behind it, so no single clock reading is safe for both (the same principle ח1/ח2 exist to protect, per HANDOFF.md). Brought back to the participant; **final decision: `exercise_date = business_date_of(req.requested_at)`** — the business date of the employee's own exercise-request submission, not the day the admin got around to approving it. This is also the more correct number: approval can lag the actual request by days, and the tax-relevant date is when the employee acted, not when the paperwork cleared. Implemented in `backend/app/api/exercise_requests.py::_compute_exercise_tax_record`, verified by `tests/test_exercise_tax_records.py::test_tax_amount_reflects_the_requests_date_not_the_approval_day`.

**Vesting engine (`services/engine.py`):** `DeterministicESOPEngine.calculate_vested_options` (line 51) is pure and deterministic given `(Grant, VestingSchedule, target_date)` — ideal for reconciliation replay with no I/O beyond the imported rows themselves.

**Document lifecycle triggers:** `documents` rows are frozen once `ACKNOWLEDGED` (`init_scheme.sql:382-390`, two triggers). This matters for import: a `Document` row imported with `status=ACKNOWLEDGED` must be inserted in a way that satisfies the trigger's `WHEN OLD.status = 'ACKNOWLEDGED'` guard on UPDATE — a plain single-statement `INSERT` is fine (the trigger fires on UPDATE/DELETE only), but any post-insert correction attempt on such a row will hit the freeze.

---

## 2. Proposed thin end-to-end slice

Smallest slice that proves the whole shape works, before CSV/ledger/tax/import/reconciliation:

**Slice: `GET /api/v1/admin/export` → JSON-only, `Company` + `Employee` rows for the caller's own company, written to an export-store file, downloaded through an authenticated endpoint that re-checks `company_id` — no CSV, no ledger, no tax packs, no import side.**

Concretely:
1. `backend/app/services/export.py`: a generic table-registry walker (decision 10) that, for a `company_id`, can currently only resolve two tables: `Company` (by PK) and `Employee` (`company_id` filter, mirroring `employees.py:27`). Writes one JSON manifest with `export_schema_version` at the top.
2. `backend/app/api/export.py`: `POST /api/v1/admin/export` (admin-only via `require_roles(UserRole.COMPANY_ADMIN)`, the same dependency used everywhere, e.g. `documents.py:123`) creates the file under a new `export_store/` directory (mirroring `DOCUMENT_STORE_DIR`, `services/documents.py:29`) and records one row in a new history table (see §3). `GET /api/v1/admin/export/{export_id}/download` re-checks `company_id` against the history row before returning `FileResponse`, exactly like `_download_document_response` (`documents.py:109-116`).
3. Register the router in `main.py` immediately after `documents.router` — `test_every_api_router_module_is_mounted_in_main` (`test_project_invariants.py:435-454`) will fail loudly if forgotten, which is the safety net this slice exists to prove.
4. One test: company A cannot download company B's export (403), and the JSON round-trips `Company.name`/`Employee.email` correctly.

This proves: router registration pattern, admin-only auth, authenticated-download-not-static-file pattern, and the export-store-on-disk pattern — the riskiest infrastructure decisions (9, 10, 12) — before any domain complexity (FK ordering, ledger replay, tax packs) is added.

---

## 3. Exact files to add or change

### New files

- **`backend/app/services/export.py`** — generic table-registry walker over `models.py`. One `TABLE_REGISTRY: dict[str, TableExportSpec]` entry per exported model, each spec holding: the SQLAlchemy model class, the company-scoping strategy (direct column vs. dispatch vs. natural-key filter), and CSV column order. Owns:
  - `run_export(db: Session, company_id: str) -> ExportBundle` — opens the single `BEGIN DEFERRED` read transaction (decision 12), reads every table once, closes.
  - CSV formula-injection escaping helper (decision 9) — new, no existing precedent, stays a pure-Python helper.
  - Tax-pack natural-key resolution: given the company's `Employee.country_code` values and `Grant.grant_type` values, select every `TaxRulePack` matching those `(country_code, grant_type)` pairs (all `effective_start_date` versions, not just the latest — reconciliation needs history), then all `TaxRatesHistory`/`IncomeTaxBracket` rows sharing those same natural keys (never `pack_id` literal, per decision 1).
  - AuditLog/NotificationPreference/NotificationDismissal scoping: a dispatch table that is a **superset** of `audit.py:18-38` (must add `Document`, `VestingSchedule`, `User`, `TaxSimulation`-as-`Grant` branches).

- **`backend/app/services/import_.py`** — domain-aware importer (decision 10). Owns:
  - `dry_run(bundle, db) -> ImportReport` — pure in-memory validation, **never touches `db.add`/`db.commit`** (decision 3). Read-only `SELECT`s for cross-company ID collision (decision 9) and schema_version match (decision 8).
  - `commit(bundle, db) -> ImportReport` — the actual write, in FK topological order (decision 3), forcing `company_id`/`role`/`is_admin` server-side on every row regardless of file content (decision 9).
  - `LedgerEvent` idempotency: check-before-insert on `(aggregate_id, sequence_no)` (never `append_event`).
  - **All other in-scope tables (`Employee`, `Grant`, `VestingSchedule`, `Document`, `ExerciseRequest`, `ExerciseTaxRecord`, tax-pack rows, etc.): same-company re-import idempotency, resolved decision D.** If a row's primary/natural key already exists **within the importer's own company**, skip it (no-op), the same treatment as `LedgerEvent` — never reject with a conflict and never overwrite. A key collision **outside** the importer's own company remains a hard rejection (decision 9, unchanged) — the skip-if-exists behavior applies only to same-company retries, e.g. re-running an import that partially failed for infra reasons after a successful dry-run.
  - Post-batch projection recompute: after all `LedgerEvent` rows are committed, recompute `OptionPool.allocated_shares`/`unallocated_shares` and `LedgerOwnership` rows from `project()` (`ledger.py:229`) for every imported aggregate — once, not per-event.
  - Streaming/chunked CSV parse with a row-count cap and shallow JSON depth cap (decision 9).

- **`backend/app/api/export.py`** — HTTP-only router (decision 10), thin wrappers calling `services/export.py` and `services/import_.py`. Registered in `main.py` next to `documents.router`.

- **New migration** (naming convention `YYYY_MM_DD_HHMM-<hex>_<slug>.py`, chained to current head `c8d5e2f0a1b4`):
  - Adds **`exercise_tax_records`** table (decision 2): `record_id PK`, `request_id` FK to `exercise_requests.request_id` UNIQUE, `country_code`, `grant_type`, `effective_start_date` (resolved pack's natural key, not `pack_id`), `calculation_method`, `gain`, `tax_amount`, `effective_rate`, `official_source_url`, `computed_at` (`UtcDateTime`).
  - Adds **`data_transfer_runs`** table (backs export/import history, decision 13): `run_id PK`, `direction` (`EXPORT`/`IMPORT_DRY_RUN`/`IMPORT_COMMIT`), `source_company_id`/`target_company_id`, `initiated_by_user_id` FK, `schema_version`, `rows_attempted`/`rows_succeeded`/`rows_failed`, `status`, `file_path`, `created_at`.
  - `database/init_scheme.sql` **must** be hand-updated in the same change — enforced by `test_project_invariants.py:309-362`.

- **`backend/app/models.py`** — add `ExerciseTaxRecord` and `DataTransferRun` ORM classes.

- **`backend/app/schemas.py`** — new Pydantic models: `ExportRequest`/`ExportRunOut`, `ImportDryRunRequest`/`ImportDryRunReport`/`ImportCommitRequest`/`ImportCommitReport`, `ReconciliationReportOut`. Follow the `DocumentOut` pattern (`schemas.py:320-342`), `Optional[...] = None` for genuinely-absent fields.

- **New test files**: `tests/test_export.py`, `tests/test_import_dry_run.py`, `tests/test_import_commit.py`, `tests/test_reconciliation.py`, `tests/test_exercise_tax_records.py`.

### Existing files to change

- **`backend/app/api/exercise_requests.py`** — `_decide_exercise_request` (lines 111-130) must, when `payload.approve is True`, call `TaxCalculationEngine.calculate_tax(..., exercise_date=business_date_of(req.requested_at))` and write an `ExerciseTaxRecord` (storing both `gain` and `tax_amount`, per resolved decision A) in the same transaction. **New business-logic call, not a refactor.** Note the corrected date source above — not `business_today()`.
- **`backend/app/main.py`** — register `export.router`.
- **`database/init_scheme.sql`** — add `exercise_tax_records` and `data_transfer_runs` DDL.
- **`clients/admin_portal/index_manage.html`** — new `<section id="tab-export">` following the `tab-documents` markup shape (`index_manage.html:187-228`).
- **`clients/shared/export_import.js`** (new) — reuses `ESOPDocuments.escapeHtml`/`orDash`/`formatTimestamp`/`errorDetail` (`documents.js:32-105`); adds a `DataTransferRun.status` badge and a Hebrew-text-paired match/mismatch renderer.
- **`docs/qa/v0.9.1.md`** — extend with a phase-B QA section (`QA-091-##` continuing from 38) and close risk item 8.

---

## 4. API / type changes

All new endpoints use `Depends(require_roles(UserRole.COMPANY_ADMIN))`.

| Method & path | Request | Response | Failure behavior |
|---|---|---|---|
| `POST /api/v1/admin/export` | `{}` (company inferred from `current_user.company_id`) | `DataTransferRunOut` | `413` if size guardrail exceeded before any table read. `500` only for genuine I/O failure. |
| `GET /api/v1/admin/export/{run_id}/download` | — | file stream | `404` if run doesn't exist; `403` if `run.source_company_id != current_user.company_id`. |
| `POST /api/v1/admin/import/dry-run` | multipart upload | `ImportDryRunReport` | `422` for `schema_version` mismatch; `409` for cross-company ID collision; `413` for size/row-count cap breach (checked before parsing). Never `500` for data problems. |
| `POST /api/v1/admin/import/commit` | `{dry_run_id: str}` | `ImportCommitReport` | `409` if referenced dry-run is stale/already committed/failed. All-or-nothing: any row failure rolls back the whole transaction, returns `409`. |
| `GET /api/v1/admin/export-import/history` | `?direction=`, `?status=` | `List[DataTransferRunOut]` | `400` for unknown filter value. |
| `GET /api/v1/admin/export-import/{run_id}/reconciliation` | — | `ReconciliationReportOut` | `404` if not `IMPORT_COMMIT` or not yet reconciled; `403` on cross-company `run_id`. |

---

## 5. UI state model and data dependencies

Two screens under `clients/admin_portal/index_manage.html`'s existing tab-per-`<section>` shape.

**Screen 1 — Export/Import history:** states `loading → list → error`, plus modals for `dry-run-report` and `reconciliation-report`. Reuses `ESOPDocuments.formatTimestamp`/`.orDash`/`.errorDetail`/`.downloadDocument`. New: `runStatusBadge(run)`, `matchCell(sourceCount, targetCount, matched, detail)` (icon **and** Hebrew text together, never color-only).

**Screen 2 — Export/import action panel (modal):** states `idle → uploading → dry-run-running → dry-run-shown → committing → done|failed`. "Done" copy states precisely what was verified (e.g. "163/163 rows exported, ledger replay not yet verified — see reconciliation report after import"), not "Export complete."

No chart library introduced; reconciliation is a plain `<table>`.

---

## 6. Tests at the cheapest meaningful layer

| Decision | Test that would fail without the change | File |
|---|---|---|
| 1. Scope + tax-pack natural-key export | `test_export_includes_only_this_companys_rows`; `test_tax_pack_export_is_scoped_by_natural_key_not_pack_id`; `test_demo_pack_flag_is_set_when_official_source_url_is_the_demo_sentinel` | `tests/test_export.py` |
| 2. Durable per-exercise tax record | `test_approving_an_exercise_request_writes_an_exercise_tax_record`; `test_exercise_tax_record_stores_natural_key_not_pack_id` | extend `tests/test_authorization_and_approvals.py` + new `tests/test_exercise_tax_records.py` |
| 3. Dry-run never touches DB session | `test_dry_run_makes_zero_db_writes`; `test_dry_run_rejects_whole_batch_on_one_bad_row` | `tests/test_import_dry_run.py` |
| 4. Reconciliation replays engines | `test_reconciliation_recomputes_vested_options_and_flags_a_mismatch`; `test_reconciliation_replays_tax_calc_for_completed_exercises_using_the_new_record` | `tests/test_reconciliation.py` |
| 5. Import idempotency (ledger + all other tables) | `test_reimporting_the_same_ledger_events_is_a_noop`; `test_projections_are_recomputed_once_after_the_batch_not_per_event`; `test_reimporting_the_same_bundle_into_the_same_company_is_a_noop_not_a_conflict` (same-company key collision on Employee/Grant/etc. is skipped, not rejected); `test_cross_company_key_collision_still_rejected` (decision 9's rejection is unchanged for a *different* company) | `tests/test_import_commit.py` |
| 6. Two-step commit | `test_commit_without_a_prior_dry_run_is_rejected`; `test_committing_a_stale_dry_run_returns_409` | `tests/test_import_commit.py` |
| 7. Size guardrail | `test_export_of_an_oversized_company_is_rejected_before_any_table_read`; `test_import_rejects_a_csv_over_the_row_cap_before_parsing_the_rest` | `tests/test_export.py`, `tests/test_import_dry_run.py` |
| 8. Schema versioning | `test_import_rejects_mismatched_schema_version_with_a_clear_error` | `tests/test_import_dry_run.py` |
| 9a. company_id/role never trusted from file | `test_import_ignores_company_id_in_the_file_and_forces_the_callers_company`; `test_cross_company_id_collision_is_rejected_not_upserted` | `tests/test_import_dry_run.py` |
| 9b. CSV formula-injection guard | `test_export_escapes_leading_formula_characters_in_csv_cells` | `tests/test_export.py` |
| 9c. Export never static-served | `test_export_download_endpoint_rechecks_company_id`; `test_export_store_is_not_mounted_as_static` | `tests/test_export.py` |
| 9d. AuditLog same transaction as action | `test_export_audit_row_is_in_the_same_commit_as_the_export_run_row` | `tests/test_export.py` |
| 10. Router registration | Covered by existing `test_every_api_router_module_is_mounted_in_main`/`test_no_duplicate_path_method_pairs_across_routers` (`test_project_invariants.py:415-454`) — no new test needed. |
| 11. CSV relational format | `test_csv_export_uses_one_file_per_table_with_fk_columns_as_plain_strings` | `tests/test_export.py` |
| 12. WAL-safe single read transaction | `test_export_reads_are_a_single_transaction_not_ambient_sessions` | `tests/test_export.py` |
| 13. UI copy / states | Out of pytest scope; covered by `docs/qa/v0.9.1.md` manual QA cases. |

---

## 7. Risks, rollback, and non-goals

**Restated approved non-goals:** whole-DB/cross-company export; trustee/employee export access; async job queue; cross-schema-version import; bulk CSV import of raw employee/grant data (v1.6.0 territory); cap table export (v1.0.0 territory).

**New risks found while tracing code:**

1. **Pre-business-clock ledger skew.** `docs/qa/v0.9.1.md:182` (risk #4) already documents old `ledger_events` rows as permanently on the wrong timezone (uncorrectable — `trg_ledger_events_no_update` forbids it). Reconciliation replay must not treat this pre-existing skew as an import bug; the report's "what was verified" copy should note it.
2. **`documents` ACKNOWLEDGED-freeze triggers constrain import ordering.** A plain multi-row INSERT is safe; a two-phase insert-then-fixup is not. Import `Document` rows in `version` order within each `(grant_id, template_type)` group, single-pass.
3. **Router invariant tests are strict.** `export.py` must be registered in `main.py` in the *same commit* that adds it, or `test_every_api_router_module_is_mounted_in_main` fails the build in between.
4. **`init_scheme.sql` has drifted from `models.py` twice before** (its own header warns of this). The two new tables here are the highest-risk point for repeating that history — run the column-by-column invariant test, don't just write it.
5. **Rollback ordering.** `ExerciseTaxRecord` writes happen inside an existing, already-tested code path (`_decide_exercise_request`). Rolling back the migration without reverting the `exercise_requests.py` change would break approval — they roll back together, or the code change degrades gracefully if the table is absent.

---

## 8. Ordered implementation steps with verification

1. Add `ExerciseTaxRecord` + `DataTransferRun` to `models.py`; write the migration; update `init_scheme.sql`.
   Verify: `alembic upgrade head`; `pytest tests/test_project_invariants.py -k init_scheme`.
2. Wire tax computation into `_decide_exercise_request` on approval; write `ExerciseTaxRecord`.
   Verify: `test_exercise_tax_records.py::test_approving_an_exercise_request_writes_an_exercise_tax_record`; full `test_authorization_and_approvals.py` regression.
3. Thin slice from §2: `services/export.py` (Company+Employee, JSON only) + `api/export.py` + `main.py` registration.
   Verify: router-mount invariants + cross-company download 403 smoke test.
4. Extend `services/export.py` to the full table registry (all tables, CSV writer, tax-pack resolution, AuditLog dispatch superset, single read transaction).
   Verify: `test_export.py` full suite; manual inspection of one exported CSV with a demo-flagged tax pack.
5. Add size/row-count/JSON-depth guardrails to export.
   Verify: guardrail test with a spy confirming no table query ran past the cap.
6. `services/import_.py`: dry-run only.
   Verify: `test_import_dry_run.py`; confirm zero DB writes even on an error-reporting dry-run.
7. `services/import_.py`: commit path.
   Verify: `test_import_commit.py`; re-run `test_ledger_replay.py` unchanged.
8. Two-step commit enforcement.
   Verify: stale/reused dry-run rejection tests.
9. Reconciliation service (replay vesting + tax engines).
   Verify: `test_reconciliation.py`, including a tampered-target-row test.
10. `api/export.py`: history + reconciliation-report endpoints.
    Verify: full `pytest` regression run.
11. UI: history table + modals, `clients/shared/export_import.js`.
    Verify: manual QA pass against new `docs/qa/v0.9.1.md` cases.
12. Update `docs/qa/v0.9.1.md`.
    Verify: `pytest tests/test_project_invariants.py` (QA-index invariants).
13. Bump `VERSION` once phase A + B are both complete end-to-end.
    Verify: `test_version_file_is_not_behind_the_qa_testbook`.

---

## Decisions resolved during planning (all confirmed by the participant)

1. **A — `ExerciseTaxRecord` stores `gain` (the pre-tax input) in addition to `tax_amount`.** RESOLVED: yes. Without it, reconciliation could only compare a stored number to itself, not actually replay the calculation. Reflected in §3's `exercise_tax_records` migration and §6's test list.
2. **B — Date for the approval-time tax calculation.** RESOLVED, then corrected during implementation (task #2): first approved as `business_today()` at the moment of approval; implementing it broke the existing `test_the_clock_is_never_the_source_of_a_tax_date` invariant (a tax date must come from a document/action, never a clock — the same principle ח1/ח2 exist to protect). Final: `business_date_of(req.requested_at)` — the business date of the employee's own request. See §1's fuller note and `tests/test_exercise_tax_records.py::test_tax_amount_reflects_the_requests_date_not_the_approval_day`.
3. **C — `StockPricesHistory` export scope.** RESOLVED: excluded from export. Storing `gain` directly (item A) removes the need to re-derive FMV from historical stock prices on the target system.
4. **D — Same-company re-import behavior for non-ledger tables.** RESOLVED: skip-if-exists (no-op), the same treatment `LedgerEvent` already gets — never reject with a conflict, never overwrite. A cross-company key collision remains a hard rejection (decision 9, unchanged). Reflected in §3's `import_.py` bullet and §6's test list.
5. **Naming collision, resolved without needing a participant decision:** `LedgerEvent.schema_version` (per-event payload shape) vs. the export bundle's version marker — the bundle-level field is named `export_schema_version` to avoid shadowing the per-row field during CSV column mapping.
6. **E — Reconciliation's vesting-check scope (task #9).** RESOLVED: narrow and direct — `DeterministicESOPEngine.calculate_vested_options` is compared on both sides with a shared, explicit `as_of` date, deliberately bypassing `vesting_cutoff_date` (which needs a loaded `Employee` with status/`termination_date` that a transient bundle-built `Grant` doesn't have). This proves the `Grant`/`VestingSchedule` fields themselves survived the import intact — narrower than "does the dashboard-facing number match," a scope explicitly accepted over reconstructing full employee context from the bundle.
7. **F — Reconciliation's row-selection strategy (task #9).** RESOLVED: uniform — every row in the bundle is reconciled the same way, without trying to recover `commit()`'s discarded per-row `NEW`/`SKIP_EXISTING` classification (it isn't persisted anywhere after a successful commit). Chosen over threading that classification through the same request as `commit()` for the sake of simplicity; the cost is stated explicitly in the report's `known_limitations`, not left implicit — a mismatch on a row that was already on the target before this import (decision D, skip-if-exists) may reflect the target's own retained data, not an import defect.

## Implementation notes added during task #11

- **New shared module `clients/shared/export_import.js`** (plain browser-global IIFE, `window.ESOPExportImport`, same pattern as `documents.js`), exposing `directionBadge`/`runStatusBadge`/`rowsSummary`/`matchCell`/`mismatchValue`. Kept as a shared module rather than inline in `index_manage.html` even though the screen is currently admin-only, matching this repo's existing convention (`documents.js`) of factoring out anything a second portal might one day need, per this codebase's own P3 rationale.
- **One "action panel" modal serves both directions** (`#ei-action-modal`), toggled by `openActionModal('export'|'import')`, rather than two separate modals — matches PLAN.md §5's original "Screen 2" wording ("Export/import action panel") literally. Export's state machine is a strict subset of import's (`idle → running → done|failed`, no upload/dry-run step); import implements the full `idle → uploading → dry-run-running → dry-run-shown → committing → done|failed` chain from §5.
- **Deliberately did NOT add a "resume commit later" action on old, un-committed `IMPORT_DRY_RUN` rows in the history table**, even though the backend trivially supports it (`POST /admin/import/commit` only needs `{dry_run_id}`, and the bundle is already saved server-side under `export_store/`). §5's state model is a single linear in-modal session; resuming a stale dry-run from a past session isn't in that spec. Not built, to avoid scope creep beyond the approved UI spec — flagged here rather than silently added.
- **Run-detail modal (`#ei-run-detail-modal`) shows only the coarse fields already on `DataTransferRunOut`** (`rows_attempted`/`rows_succeeded`/`rows_failed`) for a historical row, never the new/skipped-existing/not-portable/per-row-error breakdown — that granularity exists only in the *live* POST response at dry-run/commit time and is never persisted on the row itself. A caveat line says so explicitly, shown for both `IMPORT_DRY_RUN` and `IMPORT_COMMIT` rows (both lose the same breakdown — see task #8's note on `rows_succeeded = rows_written + rows_skipped_existing`).
- **Reconciliation report modal calls `GET .../reconciliation` live on every open**, matching task #10's "no persisted status" design — there is no client-side cache of a past reconciliation result.
- **Independent review (`change-reviewer`, 11/08/2026): first pass `REPAIR`, both real findings fixed before closing the task.** (1) The run-detail modal's missing-breakdown caveat was gated to `IMPORT_DRY_RUN` only, omitting the identical limitation on `IMPORT_COMMIT` rows — widened to cover both. (2) `escapeHtml(m.entity_id).slice(0, 8)` escaped before truncating, which could split a multi-character HTML entity mid-string on a future non-UUID `entity_id` — reordered to slice-then-escape. A third note (unescaped `err.message` in two new `innerHTML` catch blocks) was flagged as consistent with a pre-existing pattern used throughout the rest of this file, not a new regression, but fixed anyway in the two new sites (`loadExportImportHistory`, `openReconciliationModal`) since it was cheap and the task specifically asked to check every `innerHTML` interpolation site. Escaping/XSS-safety on the higher-risk surfaces (mismatch table, dry-run error table), the icon+Hebrew-text accessibility rule, "Done" copy precision, modal-state-reset-on-reopen, and `authHeaderOnly()`/`authHeaders()` consistency were all independently verified `PASS`.
- **Manually verified end-to-end against a live sandbox** (`ESOP_DATABASE_URL=sqlite:///./qa_sandbox.db`, port 8001, seeded via `backend.seed_data`): export → download → re-upload the same bundle as a dry-run (correctly reports 0 new / 97 skipped — same-company idempotency, decision D) → commit (0 written, 97 skipped) → reconciliation (clean, correct `known_limitations` text) — full round trip, zero browser console errors. Also verified the two designed failure paths: an invalid-JSON upload surfaces the server's exact 422 detail, and a bundle with a corrupted FK (`grants[0].employee_id` pointed at a nonexistent employee) correctly reports one `ERROR` row, sets `status=FAILED`, and disables the commit button. `pytest`: 332 passed, unchanged (frontend-only change, no backend files touched).

## Implementation notes added during task #9

- **New file `backend/app/services/reconciliation.py`, one entry point: `reconcile(db, bundle, as_of=None) -> ReconciliationReport`.** Bundle-in, matching `dry_run`/`commit`'s own signature style (`services/import_.py`) rather than accepting a `run_id` — resolving `run_id → bundle` (via `DataTransferRun.based_on_run_id → dry_run.file_path`, the exact chain `api/export.py::import_commit` already uses) is left as task #10's HTTP-layer plumbing, keeping this service testable with a hand-built bundle dict, same style as `tests/test_import_commit.py`.
- **No `target_company_id` parameter, unlike `dry_run`/`commit`.** Not needed: every `grant_id`/`request_id` reconciliation looks up by primary key is only in the bundle at all because a *successful* `commit()` already re-ran `dry_run` and proved it belongs to the importing company's scope (freshly written as `NEW`, or already there as `SKIP_EXISTING` — both cases require `target_scope` membership, never a different company's row — see `_validate_normal_tables`'s three-way NEW/SKIP_EXISTING/ERROR split). Re-deriving scope here would duplicate a check `commit()` already performed as a precondition for this function ever being called meaningfully.
- **Vesting comparison builds two independent, unattached objects and runs the same static method on each — no tautology.** `Grant(**_deserialize_row(Grant, grant_row))`/`VestingSchedule(**_deserialize_row(VestingSchedule, schedule_row))` are built straight from the bundle's own JSON (never `db.add`ed, exactly like `commit()`'s own construction before writing) and fed to `DeterministicESOPEngine.calculate_vested_options` — separately from a second call against the actual target-DB rows loaded by `grant_id`. A grant with no `vesting_schedules` row in the bundle at all is skipped (not a mismatch — there's nothing to replay, symmetric with the engine's own `MissingVestingScheduleError`).
- **Reused `_deserialize_row`/`_parse_date` from `services/import_.py` rather than writing a second deserializer.** Both are private (underscore-prefixed) module functions, imported directly — a deliberate call to avoid exactly the drift risk a second hand-written JSON→ORM mapping would introduce between two services that must agree on it bit-for-bit.
- **Tax comparison recomputes from the target's own committed data, not by re-reading the bundle's `exercise_tax_records` row as ground truth.** `ExerciseTaxRecord.gain` (decision A) plus `exercise_date = business_date_of(target_request.requested_at)` (same rule as decision B — the employee's own request date, never a clock) are run back through `TaxCalculationEngine.calculate_tax` against the **target's** current `TaxRulePack`/`TaxRatesHistory`/`IncomeTaxBracket` tables, and every field the pack resolution produces is compared — `tax_amount`, `effective_rate`, `table_effective_date`, and `method` — not `tax_amount` alone. This is what catches a tax pack that resolves to a *different* natural-key match with a coincidentally similar rate (the tax-domain review's specific concern); comparing `tax_amount` in isolation could miss that. Covered by `test_reconciliation_flags_a_tax_pack_that_resolves_differently_on_the_target`.
- **No rounding tolerance.** Both sides run through the exact same deterministic, already-rounded (`round(..., 2)`) code paths with the same explicit inputs — a difference in output means a difference in input, not float noise. Exact equality is the correct comparison here, not a false leniency.
- **`ReconciliationReport.known_limitations` is a real field on the report object, not just UI copy (task #11's concern) — three fixed strings, always present:** ledger/`OptionPool` balances are out of this report's scope entirely (vesting + tax only); a matching source/target vesting-window recompute is not proof of correctness for grants anchored before the 2006 Amendment 147 reform (`TRUSTEE_HOLDING_MONTHS = 24` is a global constant, both sides reproduce the identical known-wrong classification — see `HANDOFF.md`); and a mismatch on an already-pre-existing target row may reflect decision D (skip-if-exists) rather than an import bug (decision F above). Verified non-empty and containing the SKIP_EXISTING/2006 markers in `test_reconciliation_report_always_states_its_known_limitations`.
- **Nine new tests in `tests/test_reconciliation.py`, service-level only (no HTTP layer yet — task #10 adds it).** Includes the two tests named in §6's test matrix, and the tampered-target-row test the plan's step-9 verify line calls for: `test_reconciliation_recomputes_vested_options_and_flags_a_mismatch` commits a clean bundle, confirms a clean reconciliation, then mutates the **target DB's** `VestingSchedule.cliff_months` directly (not the bundle) and re-reconciles against the *same* bundle object — proving the check reads live target state rather than comparing a cached value to itself. A parallel test does the same for tax by mutating a target-side `TaxRatesHistory.capital_gains_rate` post-commit. Full suite: 322 passed (was 313).
- **No router/`main.py` registration needed at this stage.** This is a pure service-layer addition with no new endpoint, no new model, no new migration — `test_every_api_router_module_is_mounted_in_main` and the other router invariants are unaffected. Task #10 is where an endpoint (and its router registration, in the same commit per this plan's risk #3) gets added.
- **Independent review (`change-reviewer`, 11/08/2026): `PASS`.** Both approved decisions (E, F) verified faithfully implemented; the tautology risk on both engines verified disproven by evidence, not just code-reading (the reviewer ran the tampered-row tests itself). Three minor findings, all fixed before closing the task rather than deferred: (1) `ReconciliationMismatch` on the tax side always reported `tax_amount` as the differing field even when only `method`/`effective_rate`/`table_effective_date` actually diverged — now each of the four compared fields is reported individually with its own actual value, not a hardcoded one; (2) a dead `except MissingVestingScheduleError` branch in `_reconcile_vesting` that could never fire (the two upstream `None` checks already exclude its only trigger) — removed rather than commented around, per this codebase's no-dead-code convention; (3) the `target_request is None` defensive branch in `_reconcile_tax` had no test, asymmetric with the equivalent vesting-side branch which did — added `test_reconciliation_reports_a_missing_target_exercise_request_without_crashing`.

## Implementation notes added during task #10

- **`GET /admin/export-import/{run_id}/reconciliation` treats "not yet reconciled" as "the source bundle can no longer be located," not as a persisted status — resolving an ambiguity this plan's original §4 API table left open (written before task #9 designed `reconcile()` as a pure, bundle-in function with no stored state).** There is no separate "trigger reconciliation" action and no new table: the endpoint resolves `run.based_on_run_id → dry_run_row.file_path` (the exact chain `import_commit` already uses) and calls `reconcile()` live on every request. A `run_id` that isn't `IMPORT_COMMIT` at all is `404` (a real "not found," matching `download_export`/`import_commit`'s own wrong-direction 404). **Corrected after independent review: a missing bundle (file_path column empty, or the file deleted from `export_store/` after commit) is `500`, not `404`** — the first version of this endpoint claimed 404 for both cases but never actually checked `full_path.exists()`, so the "missing file" case crashed with an unhandled `FileNotFoundError` inside `read_export_json` instead of returning anything graceful. `500` matches the existing convention `download_export` already uses for exactly this situation (a `DataTransferRun` row that exists and is otherwise valid, but its own storage artifact doesn't) — a found-but-broken artifact is a server-side storage fault, not a client-facing "not found." `import_commit` has the same gap (checks the `file_path` column but never `full_path.exists()`) — pre-existing on `main`, not introduced by this task, left as-is here since fixing it was out of this task's scope.
- **Two coverage gaps from that same review were closed before merge, not deferred:** `test_reconciliation_endpoint_returns_500_when_the_bundle_file_is_missing_from_disk` (deletes the on-disk file post-commit, asserts `500`) and `test_reconciliation_endpoint_surfaces_a_real_mismatch_in_the_response_body` (mutates a target-side `TaxRatesHistory` rate post-commit and asserts on the actual HTTP response body's `mismatches[]`, not just `clean` — every other mismatch-producing test in this file calls `reconcile()` directly, never through the endpoint). A third, lower-priority gap was also closed: `test_history_endpoint_includes_export_runs_scoped_to_the_callers_company` proves the history endpoint's `or_()` scoping is correct for an `EXPORT` row (`source_company_id` set, `target_company_id` null), not just the `IMPORT_DRY_RUN`/`IMPORT_COMMIT` side the original test covered.
- **`GET /admin/export-import/history` scopes by `source_company_id` **or** `target_company_id` matching the caller** — the only endpoint in this feature that does, because it is the one screen (§5) that shows export and import runs together in one table; every other endpoint in `api/export.py` belongs to a single, already-known direction and checks only the one company column that applies to it.
- **The reconciliation endpoint writes an `AuditLog` row (`"RECONCILED"`) and calls `db.commit()` inside a `GET`**, matching `download_export`'s existing precedent (not a new pattern introduced here) — `HANDOFF.md`'s open-debt note about `_documents_out`'s GET-writes-inside-a-read-path is a distinct, already-known trade-off in a different module, not something this task reproduces unknowingly.
- **No new migration, model, or router registration.** Both endpoints are added to the existing `api/export.py` `router`, already mounted in `main.py` since task #3 — `test_every_api_router_module_is_mounted_in_main` needed no change.
- **`ReconciliationMismatchOut.source_value`/`target_value` are typed `Optional[Any]`, not a fixed type**, because `reconciliation.py`'s `ReconciliationMismatch` (task #9) can carry a `float` (amount/rate), a `date` (`table_effective_date`), or a `str` (`calculation_method`) depending on which field actually diverged — FastAPI's response serialization (`jsonable_encoder`) converts each correctly regardless of the declared `Any`.
- **Nineteen tests total in `tests/test_reconciliation.py` (10 HTTP-level, matching the file's existing 9 service-level ones), split top-to-bottom exactly like `test_import_commit.py`.** HTTP-level tests exercise the full dry-run → commit → reconciliation-report chain through real endpoints (not `reconcile()` called directly), plus the 404/403/500 boundary cases and the history endpoint's cross-company isolation and filter validation. Full suite: 332 passed (was 322).
- **Independent review (`change-reviewer`, 11/08/2026): first pass verdict `REPAIR`, both findings fixed before merge, not deferred.** Two real, reproducible `WARNING`-level findings — the 404-vs-500 bug above (the review actually reproduced the crash, not just read the code) and the two missing HTTP-level tests — both closed in the same task rather than shipped with known gaps. No `BLOCKER`. Everything else (404-then-403 ordering, `or_()` cross-company scoping including the null-column edge cases, `Optional[Any]` JSON serialization of float/date/str mismatch values, and the audit-log-inside-GET pattern being genuine `download_export` precedent rather than new scope) was independently verified `PASS`, including by the reviewer running the actual test suite and a live repro rather than trusting this document's claims.

## Implementation notes added during task #4 (engineering details within the already-approved scope, not new participant decisions)

- **CSV is derived on demand, not stored as a second file.** `export_store/` holds one JSON bundle per run (the canonical artifact). `GET /admin/export/{run_id}/download?format=csv` reads that JSON back and converts it to a CSV-per-table ZIP in memory (`services/export.py::render_bundle_as_csv_zip`). This avoids two on-disk artifacts per run ever drifting from each other — one stored source of truth, CSV is a view over it.
- **Tax-pack scoping's "not forward-dated" cutoff is derived from the company's own stored data** (latest `Grant.grant_date` / `ExerciseRequest.requested_at` in scope), never from `business_today()` or any clock — same principle as item B above. See `services/export.py::_export_tax_scope_cutoff`.
- **`pack_id` is stripped from every exported `tax_rule_packs`/`tax_rates_history`/`income_tax_brackets` row.** The natural key columns are already present on those rows; keeping `pack_id` around risked a future importer being tempted to match on it.
- **`ExerciseTaxRecord` is in the export scope**, though it wasn't in the original decision-1 list (that list predates task #2, which created the table). Its purpose per decision 2 was to make reconciliation replay possible on completed exercises — excluding it from export would defeat that.

## Implementation notes added during task #6

- **Target-company model, clarified (this was implicit, not stated explicitly anywhere in the plan):** import always writes into the **importer's own existing company** (`current_user.company_id`) — it never creates a new company from the bundle's `companies` row. That row exists in the bundle only as a sanity check ("is this actually an export bundle") and, later, as reconciliation input — it is never inserted. This follows directly from decision 9 ("company_id... forced server-side") and from `DataTransferRun.target_company_id` already being a real column: the admin performing the import must already have an account on the target system. Primary keys of every other table (`employee_id`, `grant_id`, `pool_id`, …) are preserved verbatim from the source — only `company_id`-bearing fields get forced at write time (task #7). This is why dry-run's classification never inspects a row's own `company_id` field: it's inert data that gets overwritten regardless, so it can't affect NEW/SKIP_EXISTING/ERROR classification (proven in `test_import_ignores_company_id_in_the_file_and_forces_the_callers_company`).
- **`CompanyScope`/`build_company_scope`** (renamed from the export-only `_ExportScope`/`_build_scope`) are now shared infrastructure between `services/export.py` and `services/import_.py` — both need "what already belongs to this company" and there was no reason to compute it twice. Extended with `exercise_tax_record_ids`.
- **HTTP status codes deviate from this plan's original API table in one place, deliberately:** the table said cross-company collisions get a top-level `409`. Implemented instead as one more `ERROR` entry in the same 200-response report used for every other row-level problem (FK failures, orphaned ledger aggregates, etc.) — reserving `413`/`422` purely for whole-bundle gate failures (size, JSON depth, row count, schema version) that prevent per-row validation from running at all. Rationale: a collision is discovered *during* the same per-row pass as everything else; promoting just this one error type to a distinct top-level status code would mean losing the rest of that row's siblings' classifications in the same response. The row is never silently upserted either way — decision 9's actual requirement — this only changes which HTTP status carries the news.
- **New dependency: `python-multipart==0.0.20`.** Required by FastAPI/Starlette for any `UploadFile`/`File(...)` parameter — without it, the app fails at request-handling time with a `RuntimeError`, not an import-time error, so it wasn't caught until the first multipart test ran. Added with the same per-dependency justification comment style as the rest of `requirements.txt`.
- **Import-side guardrails (file-size cap, JSON-depth cap, row-count cap)** — deferred from task #5 because task #5 covered only the export side — are implemented in `services/import_.py::parse_and_validate_bundle_shape`, checked in this order: size → JSON-parseable → depth → row count → schema version → basic shape (`companies` present). Each gate fails before the next, more expensive one runs. The file is read with a bounded `file.file.read(MAX_IMPORT_FILE_BYTES + 1)` in the endpoint, not `UploadFile.read()` unbounded, so a deliberately huge upload never gets buffered in full.
- **CSV/multipart import (importing the CSV-zip format the export side can produce) was not built.** Only the JSON bundle format is accepted by `/admin/import/dry-run`. This is a real scope reduction from decision 11's "JSON + CSV" symmetry, not an oversight — flagging it explicitly rather than silently shipping less than planned.

## Implementation notes added during task #8

`POST /api/v1/admin/import/commit` (`backend/app/api/export.py`) wires task #7's already-tested `commit()` behind the two-step contract: request body is `{dry_run_id}` only, never a re-upload — the endpoint reads the bundle back from the `DataTransferRun.file_path` the dry-run endpoint already saved (same `export_store/` convention as export).

- **Ownership/existence checks copy `download_export`'s exact 404-then-403 pattern**: `run_id` not found or not `IMPORT_DRY_RUN` → 404; found but `target_company_id != current_user.company_id` → 403. Kept identical rather than inventing a new shape for the same problem.
- **"Committable" is `status == SUCCESS`, nothing else** — matches this plan's original decision text ("stale/reused") once traced through `models.py`'s own docstring on `DataTransferStatus.COMMITTED` ("a flag for an `IMPORT_DRY_RUN` that a commit has already 'consumed' — checkable via `based_on_run_id`, not just assumed in code"). No time-based expiry was added; a dry-run that reported `FAILED` was never valid, and one that's `COMMITTED` has already been spent. Both → 409.
- **A dry-run that *was* valid but no longer is (state changed underneath it before commit) is also 409, not 200 with a report.** This is `commit()`'s own re-run of `dry_run` (task #7) surfacing at the HTTP layer — unlike the dry-run endpoint itself, which always returns 200 with a report (diagnostic, no side effect either way), a *commit* attempt that can't proceed is a failed action, and 409 is the correct code per this plan's original API table. The stale dry-run's own `DataTransferRun` row is deliberately **not** flipped to `FAILED` in this case — it was accurate when created; a fresh dry-run against current state would show what actually changed. Covered by `test_commit_endpoint_rejects_when_state_changed_since_the_dry_run`.
- **On success, the dry-run row is flipped to `COMMITTED` and a new `IMPORT_COMMIT` `DataTransferRun` is created with `based_on_run_id` pointing at it** — the FK link this plan's schema (task #1) already provisioned for exactly this purpose. `rows_succeeded` on that history row follows the same convention as the export/dry-run endpoints (`rows_written + rows_skipped_existing`).
- **Six new tests**, split HTTP-level (this task) vs. service-level (task #7, unchanged) within `tests/test_import_commit.py`: successful commit marks the dry-run `COMMITTED` and links `based_on_run_id`; unknown `dry_run_id` → 404; wrong-company `dry_run_id` → 403; an originally-`FAILED` dry-run → 409; re-committing an already-`COMMITTED` dry-run → 409; and the state-changed-since-dry-run race → 409. Full suite: 313 passed (was 307).

## Implementation notes added during task #7

Two real gaps were found while implementing `commit()` that this plan's original text (§3/§8 step 7) didn't address — both confirmed with the participant before writing code, not decided unilaterally:

- **Every `*_user_id` column is forced to `NULL` at write time**, unconditionally — `documents.acknowledged_by_user_id`/`created_by_user_id`, `exercise_requests.reviewed_by_user_id`, `audit_log.actor_user_id`, `ledger_events.actor_user_id`. These are real, enforced foreign keys (`PRAGMA foreign_keys=ON`, `database.py`) to `users`, and `users` is permanently out of import scope (decision 1) — the referenced account can never exist on the target system. Nulling is unconditional (never "only if the id doesn't resolve locally") so behavior doesn't depend on the coincidence of the same user existing on both systems. "Who did this" attribution does not survive a cross-system import; the action itself does.
- **`notification_preferences.user_id` / `notification_dismissals.user_id` are `NOT NULL` foreign keys to `users`** (`init_scheme.sql`), unlike every other `*_user_id` column above — they can't be nulled. Since `users` is never imported, a genuinely *new* row in either table can never be written on a real cross-company import; it would always violate the FK. `dry_run` (task #6) previously misclassified such a row as importable `NEW`. Fixed together with `commit()`: a new status, `NOT_PORTABLE`, is now reported for these two tables' new rows (still not `ERROR` — it doesn't block the rest of the bundle over per-user notification settings the target admin will just reconfigure) — and `commit()` never writes to either table. `ImportDryRunReport`/`ImportDryRunReportOut` gained `rows_not_portable`; existing/`SKIP_EXISTING` rows in these two tables are unaffected.

Other implementation decisions, within the already-approved scope:

- **Deserialization is the exact inverse of `export.py::_serialize_row`.** `_deserialize_value` (`services/import_.py`) converts ISO date/datetime strings back to `date`/`datetime` and enum-value strings back to the enum member, keyed off each column's actual SQLAlchemy type (`UtcDateTime`, `Enum`, `Date`) — not a per-table hand-written mapping. Necessary because `UtcDateTime.process_bind_param` calls `.tzinfo` on the assigned value; a raw JSON string has none and raises.
- **`tax_rule_packs.pack_id` is regenerated on every import**, not preserved — it was already stripped at export (even from `tax_rule_packs` itself, not just the two tables referencing it — see task #4's note). A fresh pack gets a new UUID (`models.py` column default); `tax_rates_history`/`income_tax_brackets` rows resolve their `pack_id` by natural key against a map built from *all* target-side packs (pre-existing + newly inserted) after the packs are flushed. Without this, `tax_engine.py`'s `_calculate_flat`/`_calculate_progressive` — which filter strictly by `pack_id`, never by natural key — would find zero detail rows for any imported pack.
- **`LedgerOwnership` rows are (re-)established via `record_ownership()` for every newly-written aggregate** (`OptionPool`/`Employee`/`Grant`/`VestingSchedule`/`ExerciseRequest`), mirroring `backfill_ledger.py`'s original approach — `LedgerOwnership` itself is never in the export bundle (decision 1). Every call uses `company_id=target_company_id` directly, never derived through a pool/grant lookup like the live write paths do (`grants.py` etc.) — unlike live traffic, commit's target is always a single, already-known company.
- **`OptionPool.allocated_shares`/`unallocated_shares` are recomputed from `ledger.project()` once per touched pool, after all ledger events in the batch are written** — not copied from the file, and not touched for pools that received no new events. This matters specifically for the `SKIP_EXISTING`-pool-plus-new-events case: the pool row itself is never overwritten from the file (decision D), but if the import adds ledger history the target didn't have yet, the pool's own balance columns would silently drift from what the ledger now says without this step. Covered by `test_option_pool_balance_is_recomputed_after_new_events_land_on_an_existing_pool`.
- **Scope limited to `OptionPool`, matching this plan's original wording exactly** ("recompute `OptionPool.allocated_shares`/`unallocated_shares`... for every imported aggregate") — not extended to `VestingSchedule.paused_days_total`, which is arithmetically the same kind of cumulative delta (`project_vesting_schedule` sums `paused_days_total` the same way `project_option_pool` sums share deltas) and is theoretically exposed to the identical `SKIP_EXISTING`-plus-new-events drift. Left as a known, undecided residual gap rather than silently fixed beyond the approved scope — flagging here rather than in a new commit so it's visible before task #8.
- **Never calls `append_event()`** — it would reassign `sequence_no` and default `recorded_at` to now, destroying exactly what decision 5's idempotency is supposed to preserve (the true historical `recorded_at`, see §7 risk 1). `LedgerEvent` rows are constructed directly from the deserialized bundle row.
- **`commit()` re-runs `dry_run()` fresh against the exact `bundle` it's given, and writes nothing at all if that fresh run is invalid** (all-or-nothing, decision 3) — it does not accept or trust a previously-computed `ImportDryRunReport`. Matches `HANDOFF.md`'s description of the task. Does not call `db.commit()` itself, consistent with every other function in this module and in `services/export.py` — the caller (a future endpoint, task #8) owns the transaction boundary.
- **No HTTP endpoint added in this task** — confirmed with the participant to keep task #7 service-layer only (`services/import_.py::commit()` plus `tests/test_import_commit.py`, calling the service function directly, same style as task #6's service-level tests), matching this plan's own step split (step 7 vs. step 8's "two-step commit enforcement"). Task #8 wires `POST /admin/import/commit`, including the `based_on_run_id`/stale-dry-run 409 checks.

## Implementation notes added during task #5

- **Export row-count guardrail** (`services/export.py::assert_export_within_size_limit`, `EXPORT_MAX_ROWS`, default 50,000, overridable via `ESOP_EXPORT_MAX_ROWS`) runs a cheap COUNT-only pass (`estimate_export_row_count`) before `run_export`'s full per-row hydration+serialization. Verified that `run_export` is never invoked once the cheap check fails (spy in `test_export.py::test_export_of_an_oversized_company_is_rejected_before_any_table_read`), not just that the response code is 413.
- **The JSON-depth cap (decision 9) does not apply to export.** That cap protects against a maliciously deep *input* file; the export bundle's shape is fixed and closed (`bundle → tables → table_name → flat row dicts`), built only by this code, never by external input — there's no vector for its depth to vary. The real depth cap belongs to task #6 (import dry-run), where the JSON comes from outside. Noted explicitly in `run_export`'s docstring so it isn't mistaken for a dropped requirement.
