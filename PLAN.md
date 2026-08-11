# PLAN.md — v0.9.1 Phase B: Export / Import / Reconciliation

Grounded in the ESOP_test repo as of `VERSION` = `0.9.1` (phase A already shipped per `docs/qa/v0.9.1.md:3-7`). Produced from an approved feature spec (five-perspective expert fan-out + tax-domain-expert sign-off, all approved by the participant). All file:line references below were read directly from the repo.

**Status: IN PROGRESS — steps 1-6 of §8 complete and committed (see "Implementation notes" after each step below, and `HANDOFF.md` for the session-close context). Next: step 7 (import commit path). The prior session closed mid-feature due to context-length discipline (`tools/context_check.py`), not a version boundary — read this file in full before continuing, not just `HANDOFF.md`.**

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

## Implementation notes added during task #5

- **Export row-count guardrail** (`services/export.py::assert_export_within_size_limit`, `EXPORT_MAX_ROWS`, default 50,000, overridable via `ESOP_EXPORT_MAX_ROWS`) runs a cheap COUNT-only pass (`estimate_export_row_count`) before `run_export`'s full per-row hydration+serialization. Verified that `run_export` is never invoked once the cheap check fails (spy in `test_export.py::test_export_of_an_oversized_company_is_rejected_before_any_table_read`), not just that the response code is 413.
- **The JSON-depth cap (decision 9) does not apply to export.** That cap protects against a maliciously deep *input* file; the export bundle's shape is fixed and closed (`bundle → tables → table_name → flat row dicts`), built only by this code, never by external input — there's no vector for its depth to vary. The real depth cap belongs to task #6 (import dry-run), where the JSON comes from outside. Noted explicitly in `run_export`'s docstring so it isn't mistaken for a dropped requirement.
