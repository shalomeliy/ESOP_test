# ARCHITECTURE — how the backend actually fits together

Static code map, not a status doc — it changes when the shape of the system changes, not
every version. For "what's being built now" see `HANDOFF.md`; for "why" see `GOAL.md`;
for "who builds what" see `AGENT_WORKFLOW.md`. This file answers "where does X live and
how does it work", the kind of question that otherwise takes reading five files to answer.

Verify file:line references against the current code before relying on them for an edit —
this file is not regenerated automatically when line numbers shift.

## API layer — one router per domain, not one `routes.py`

`backend/app/api/` holds 15 router modules, each owning one domain's endpoints; all are
mounted in `main.py:47-61` under the shared prefix `/api/v1`:

| Router | Owns |
|---|---|
| `auth.py` | login / session / password change |
| `search_meta.py` | global search |
| `notifications.py` | computed-on-read notification feed |
| `employees.py` | admin employee CRUD |
| `company.py` | company settings, acknowledgment-window overrides |
| `grants.py` | grant creation/lifecycle |
| `exercise_requests.py` | option exercise submit/approve |
| `audit.py` | audit log query |
| `ledger.py` | raw ledger event query/replay |
| `trustee.py` | trustee-portal endpoints |
| `employee_dashboard.py` | employee-portal endpoints |
| `documents.py` | PDF generation, download, acknowledgment |
| `export.py` | company data export/import (dry-run + commit) |
| `cap_table.py` | dilution/cap-table computation |
| `reports.py` | the 7 reports + saved-report config |

There is no per-request company-scoping dependency — it's enforced ad hoc per endpoint,
either by comparing `current_user.company_id` to the target row directly, or via
`build_company_scope(...)` (see below) for multi-table endpoints. When adding an endpoint,
copy the scoping pattern of a sibling in the same router rather than inventing one.

## Services layer (`backend/app/services/`)

| Module | Purpose |
|---|---|
| `ledger.py` | event-sourcing core — see below |
| `company_scope.py` | multi-tenant table registry — see below |
| `engine.py` | vesting calculation (`calculate_vested_options`) |
| `tax_engine.py` | tax-rule-pack dispatch — see below |
| `documents.py` | PDF rendering (ReportLab), RTL/Hebrew handling |
| `document_status.py` | document state machine + acknowledgment-window resolution |
| `document_access.py` | single shared ownership check for document reads |
| `export.py` / `import_.py` | company data export / dry-run+commit import, both built on `company_scope.TABLE_REGISTRY` |
| `reconciliation.py` | re-runs vesting+tax engines on an imported bundle, diffs against saved results |
| `cap_table.py` | pure read-time fully-diluted dilution calc, no persistence |
| `reports.py` | the 7 reports + BI dashboard, all built from `CompanyScope` |
| `notifications.py` | deadline/notification computation, nothing persisted |
| `search_engine.py` | `difflib`-based deterministic fuzzy search |
| `audit.py` | writes `AuditLog` rows inside the caller's transaction |

## The ledger — event-sourced state, not directly-edited columns

`backend/app/services/ledger.py`, models at `models.py:447` (`LedgerEvent`) and `:482`
(`LedgerOwnership`). Business state for six aggregate types is a **fold over an
append-only event sequence**, not a mutable column:

- **Ledger-native aggregates** (have a projector in `PROJECTORS`, `ledger.py:233`):
  `OptionPool`, `Employee`, `Grant`, `VestingSchedule`, `ExerciseRequest`, `ShareIssuance`.
  Everything else (Document, AuditLog, notifications, tax packs) is computed directly from
  columns/other tables — not ledger-projected.
- **`LedgerEvent`** fields: `event_type`, `aggregate_type`, `aggregate_id`, `payload` (JSON
  string), `effective_date` (when it was true in the world), `recorded_at` (when the system
  learned it — immutable; a DB trigger blocks `UPDATE`), `sequence_no` (fold-order tiebreak
  per aggregate, single-writer-SQLite assumption), `source` (`LIVE` vs `BACKFILL_v0.6.0`).
- **Write path**: `append_event()` (`ledger.py:56`) validates the event/aggregate type and
  assigns the next `sequence_no`. The caller must append the event in the **same
  transaction** as any column update it mirrors — the two are not auto-synced.
- **Ownership index**: `record_ownership()` (`ledger.py:93`) writes a separate, immutable
  `LedgerOwnership` row used *only* for authorization checks. Endpoints check ownership
  against this table, never against replayed/projected data — this was added specifically
  to close a prior IDOR pattern (P2 in `QA_TESTBOOK.md`'s failure-pattern table).
- **Read/projection**: `events_for()` (`ledger.py:116`) supports two independent time cuts:
  `as_of_effective_date` ("what was true in the world by X") and `as_of_knowledge_date`
  ("what did the system know by X"). `project(db, aggregate_type, aggregate_id, ...)`
  (`ledger.py:243`) dispatches to the aggregate's projector and returns `None` if the
  aggregate has no events at all — never a zero-value default (see failure pattern P4).

When adding a new mutation to a ledger-native aggregate: append an event through
`append_event()`, don't just update the column — the column (if one still exists in
parallel) and the ledger will silently diverge otherwise.

## `company_scope` — the multi-tenant isolation contract

`backend/app/services/company_scope.py`. `build_company_scope(db, company_id)` computes a
`CompanyScope` dataclass of ID sets (pool_ids, employee_ids, grant_ids, …) **once**, reused
by every loader/query in a request instead of re-deriving scoping per table.

`TABLE_REGISTRY` (`company_scope.py:192`) is a single `Dict[str, TableSpec]` shared by both
`export.py` and `import_.py` — it replaced two independently-drifting dicts after a real bug
(a new table registered for export but forgotten in import's force-company_id set).
`SPECIAL_CASED_TABLES` (`:264`) lists tables that do carry a real `company_id` column but
are deliberately excluded (`companies`, `users`, `ledger_ownership`,
`stock_prices_history`, `document_acknowledgment_window_overrides`, `saved_reports`), each
with a documented reason.

**Invariant, enforced in CI**: `tests/test_project_invariants.py`'s
`test_every_company_scoped_table_is_registered_or_explicitly_special_cased` fails the build
if any model with a literal `company_id` column is in neither collection. **Rule for a new
table with `company_id`**: register it in `TABLE_REGISTRY` (with a loader and
`force_company_id=True`) or add it to `SPECIAL_CASED_TABLES` with a reason — there is no
third, silent option, and `pytest` will catch it if you skip this.

## Auth (`backend/app/auth.py`)

Session-token based, not JWT. `create_session()` issues a random
`secrets.token_urlsafe(32)` stored in `UserSession` (30-day expiry). `get_current_user()`
reads `Authorization: Bearer <token>`, loads the session, checks expiry and `is_active`.
Roles (`models.py:229`): `COMPANY_ADMIN`, `TRUSTEE`, `EMPLOYEE`. `require_roles(*roles)` is
a dependency factory that also 403s if `must_change_password` is set — except on
`/search` and `/notifications`, which use bare `get_current_user` (a known, documented gap,
`docs/qa` risk R-051). Account lockout: 5 failed attempts → 15 minute lock.

## Tax engine (`backend/app/services/tax_engine.py`)

`TaxCalculationEngine.calculate_tax(db, country_code, grant_type, exercise_date, gain)`
looks up the most recent `TaxRulePack` with `effective_start_date <= exercise_date` for that
`(country, grant_type)` pair, then dispatches by `pack.calculation_method`
(`FLAT_RATE` or `PROGRESSIVE_BRACKETS`, reading `TaxRatesHistory`/`IncomeTaxBracket`). Tax
rules are **versioned data** (rule packs), not hardcoded per-country branches. Missing
coverage raises `MissingTaxRuleError` with a specific reason
(`NEVER_MODELED` / `NO_RULE_EFFECTIVE_AS_OF_DATE` / `PACK_HAS_NO_DETAIL_ROWS` /
`INVALID_CALCULATION_METHOD`) — there is deliberately no silent fallback rate. Never add a
tax rule here without `tax-domain-expert` sign-off (see `CLAUDE.md`).

## Document engine (`backend/app/services/documents.py` + `document_status.py`)

PDFs are rendered with ReportLab (flowables assembled per doc type in Python), not an
HTML-template engine. Files land in `document_store/` (gitignored, like the DB) and are
never served as static files — always through an endpoint that calls
`document_access.py`'s shared ownership check first. Hebrew/RTL is handled explicitly:
`_ensure_unicode_font` registers a Unicode font and `_rtl` runs text through
`bidi.get_display`; missing font support fails loudly (`DocumentRenderingError`) rather than
rendering garbled text. Acknowledgment tracking is a separate state machine in
`document_status.py`: `DRAFT → SENT → {ACKNOWLEDGED, DECLINED, EXPIRED}`, lazy expiry
checked on every load (no background scheduler), with a 3-tier acknowledgment-window
resolution (per-company-per-template override → per-company override → global default).

## Migrations

Alembic (`alembic.ini`, `migrations/`) — see `README.md` for the day-to-day command
sequence (stamp vs. upgrade, drift checks). This file only confirms the mechanism exists.

## Testing conventions (`tests/conftest.py`)

`ESOP_DATABASE_URL` is set to a temp sqlite path **before** any `backend.app` import,
because `database.py` builds its Engine at import time — the import-order note in
`CLAUDE.md` refers to this. Schema is built via `alembic upgrade head`, not
`Base.metadata.create_all()`, specifically because triggers (e.g. the append-only guard on
`ledger_events`) exist only in migrations, not in ORM metadata. Guard fixtures
(`guard_production_db`, `_assert_not_production`) abort the run if the engine ever points at
the real `esop_database.db`. Each test runs inside a rolled-back transaction shared with the
`client` fixture's `get_db` override.

`tests/test_project_invariants.py` checks the **repo as a system**, not code correctness —
e.g. `VERSION` not drifting behind the shipped QA docs, and the `company_scope` completeness
check above. The rule behind this file: every bug caught once becomes a permanent check
here, so it never recurs silently.
