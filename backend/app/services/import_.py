"""ייבוא נתוני חברה (v0.9.1 שלב ב) - דריי-ראן בלבד (PLAN.md §8 step 6).

**מודל היעד**: הייבוא תמיד נכתב (בשלב ה-commit, task #7) תחת company_id של
המשתמש המאשר את הבקשה - לעולם לא לפי company_id שכתוב בקובץ, ולעולם לא יוצר
חברה חדשה. זה מניח שלמשתמש המייבא כבר יש חברה קיימת במערכת היעד (נוצרה דרך
ה-onboarding הרגיל, לא דרך הפיצ'ר הזה) - "ייצוא/ייבוא" הוא ה*תוכן* של החברה,
לא זהותה. השורה היחידה בטבלת companies שבחבילה משמשת רק לאימות סבירות
(האם זו בכלל חבילת ייצוא), לא נכתבת בעצמה.

מכאן נובע ישירות למה dry_run לא בודק בכלל company_id שבשורה: הסיווג (NEW /
SKIP_EXISTING / ERROR) מבוסס אך ורק על *קיום המפתח הראשי* מול היעד - לא על
תוכן company_id שממילא יידרס ב-commit. שורת עובד שמצהירה על company_id זר
בקובץ מסווגת בדיוק כמו שהייתה מסווגת בלי ההצהרה הזו בכלל.

**דריי-ראן הוא "in-memory" במובן אחד ספציפי**: הוא לעולם לא כותב נתוני
דומיין (Employee/Grant/...) - לא db.add, לא db.commit על אף שורה מהחבילה.
הוא כן קורא מה-DB (שאילתות בלבד) כדי לבדוק קיום/התנגשות מול היעד - "in-memory"
מתאר את הכתיבה, לא את הקריאה (ראו PLAN.md §3, וההבחנה המפורשת בין
"db.add/db.commit" ל-"SELECT-ים בלבד" בסקירת הארכיטקטורה).

**עמודת company_id בטבלאות (OptionPool/Employee/Trustee/Document) מוסרת
מהסיווג**: התאמה מבוססת רק על *קיום המפתח הראשי היכן שהוא* + חברות ב-scope
של היעד (build_company_scope, מיובא מ-services/export.py - אותה לוגיקת scoping
המשמשת גם את הייצוא, ראו שם). זה מה שמאפשר שלוש תוצאות בלבד לכל שורה:

- NEW - המפתח לא קיים בשום מקום ב-DB.
- SKIP_EXISTING - המפתח כבר קיים, *ובתוך* ה-scope של החברה המייבאת (idempotent
  re-import - decision D, אותו יחס כמו LedgerEvent).
- ERROR - המפתח כבר קיים, אבל *מחוץ* ל-scope של החברה המייבאת - חברה אחרת.
  לעולם לא נדרס בשקט (decision 9).

**חבילות מס** הן היוצא מן הכלל: אין company_id בכלל (נתון ייחוס גלובלי), אז
ההתאמה היא natural key בלבד (country_code, grant_type, effective_start_date[,
bracket_order]) - בדיוק כמו שהוחלט בייצוא, ובלי pack_id (שהוסר משם).

**LedgerEvent** אין לו company_id ישיר; ה-scope שלו נגזר מה-aggregate שהוא
מתאר (OptionPool/Employee/Grant/VestingSchedule/ExerciseRequest) - חייב
להימצא בבאצ' הזה או כבר ב-scope של היעד. אידמפוטנטיות היא על
(aggregate_id, sequence_no), לא על event_id.

**AuditLog/NotificationPreference/NotificationDismissal**: entity_type="User"
(ב-AuditLog) ו-user_id (בשתי הטבלאות האחרות) לא מאומתים מול ה-scope - טבלת
users אינה חלק מהיקף הייצוא/ייבוא הזה (decision 1), ולכן אין כנגד מה לאמת.
נבדקת רק התנגשות מפתח ראשי, לא שרשור FK מלא.
"""

import json
from dataclasses import dataclass, field
from datetime import date as _date
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from backend.app.models import (
    AuditLog, Document, Employee, ExerciseRequest, ExerciseTaxRecord, Grant,
    IncomeTaxBracket, LedgerEvent, NotificationDismissal, NotificationPreference,
    OptionPool, TaxRatesHistory, TaxRulePack, Trustee, VestingSchedule,
)
from backend.app.services.export import EXPORT_SCHEMA_VERSION, CompanyScope, build_company_scope

# ===================================================================
# מגבלות על קלט לא-ידוע (decision 9) - נבדקות לפני שנוגעים בתוכן בכלל.
# הועברו לכאן מ-task #5 בכוונה: זה בדיוק הצד שבו ה-JSON מגיע מבחוץ.
# ===================================================================

MAX_IMPORT_FILE_BYTES = 20 * 1024 * 1024  # 20MB - קובץ QA/דמו סביר, לא batch ייצור ענק
MAX_IMPORT_JSON_DEPTH = 12  # bundle->tables->table_name->rows->row->value = 5 בפועל; מרווח סביר לפני חשד
IMPORT_MAX_ROWS = 50_000  # אותה מגבלה בדיוק כמו EXPORT_MAX_ROWS - עקביות, לא שרירותיות


class ImportFileTooLargeError(ValueError):
    def __init__(self, size_bytes: int, limit_bytes: int):
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes
        super().__init__(f"Import file is {size_bytes} bytes, exceeding the {limit_bytes}-byte limit")


class ImportJsonTooDeepError(ValueError):
    def __init__(self, depth: int, limit: int):
        self.depth = depth
        self.limit = limit
        super().__init__(f"Import JSON nests {depth} levels deep, exceeding the {limit}-level limit")


class ImportTooManyRowsError(ValueError):
    def __init__(self, row_count: int, limit: int):
        self.row_count = row_count
        self.limit = limit
        super().__init__(f"Import bundle contains {row_count} rows, exceeding the {limit}-row limit")


class ImportSchemaVersionMismatch(ValueError):
    def __init__(self, found, expected: int):
        self.found = found
        self.expected = expected
        super().__init__(f"Import bundle schema_version {found!r} does not match "
                         f"the supported version {expected} for this instance")


class InvalidImportBundleError(ValueError):
    """החבילה לא עברה אפילו את בדיקת הסבירות הבסיסית (למשל: אין טבלת companies
    בכלל) - לא ניסיון ייבוא לגיטימי שנכשל, אלא קלט שאינו חבילת ייצוא."""


def assert_file_size_within_limit(size_bytes: int) -> None:
    """נבדק *לפני* קריאת התוכן לזיכרון - ראו קריאת ה-endpoint (api/export.py),
    שבודק את size לפני .read() מלא."""
    if size_bytes > MAX_IMPORT_FILE_BYTES:
        raise ImportFileTooLargeError(size_bytes, MAX_IMPORT_FILE_BYTES)


def _json_depth(value, current: int = 1) -> int:
    if current > MAX_IMPORT_JSON_DEPTH:
        return current  # מספיק שחרגנו - לא צריך להמשיך לרדת עמוק יותר
    if isinstance(value, dict) and value:
        return max((_json_depth(v, current + 1) for v in value.values()), default=current)
    if isinstance(value, list) and value:
        return max((_json_depth(v, current + 1) for v in value), default=current)
    return current


def assert_json_depth_within_limit(parsed: object) -> None:
    depth = _json_depth(parsed)
    if depth > MAX_IMPORT_JSON_DEPTH:
        raise ImportJsonTooDeepError(depth, MAX_IMPORT_JSON_DEPTH)


def _total_row_count(bundle: dict) -> int:
    return sum(len(rows) for rows in bundle.get("tables", {}).values())


def assert_row_count_within_limit(bundle: dict) -> None:
    row_count = _total_row_count(bundle)
    if row_count > IMPORT_MAX_ROWS:
        raise ImportTooManyRowsError(row_count, IMPORT_MAX_ROWS)


def parse_and_validate_bundle_shape(raw: bytes) -> dict:
    """שער הכניסה היחיד לתוכן חיצוני: גודל -> JSON תקין -> עומק -> מספר שורות
    -> schema_version -> סבירות בסיסית. בכל שלב כשל עוצר לפני השלב היקר הבא -
    לא קוראים JSON ענק רק כדי לגלות אחר כך שהעומק שלו חשוד."""
    assert_file_size_within_limit(len(raw))
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise InvalidImportBundleError(f"Import file is not valid JSON: {e}") from e
    assert_json_depth_within_limit(parsed)
    if not isinstance(parsed, dict) or "tables" not in parsed:
        raise InvalidImportBundleError("Import file is not a recognized export bundle (missing 'tables')")
    assert_row_count_within_limit(parsed)
    if parsed.get("export_schema_version") != EXPORT_SCHEMA_VERSION:
        raise ImportSchemaVersionMismatch(parsed.get("export_schema_version"), EXPORT_SCHEMA_VERSION)
    if not parsed["tables"].get("companies"):
        raise InvalidImportBundleError("Import bundle has no companies row - not a valid export")
    return parsed


# ===================================================================
# דוח דריי-ראן
# ===================================================================

NEW = "NEW"
SKIP_EXISTING = "SKIP_EXISTING"
ERROR = "ERROR"


@dataclass
class RowOutcome:
    table: str
    index: int
    row_id: Optional[str]
    status: str
    error: Optional[str] = None


@dataclass
class ImportDryRunReport:
    valid: bool
    rows_attempted: int = 0
    rows_new: int = 0
    rows_skipped_existing: int = 0
    rows_failed: int = 0
    # outcomes כולל כל שורה (גם NEW/SKIP) - task #7 (commit) צריך את זה כדי
    # לדעת בדיוק מה לכתוב ומה לדלג עליו, לא רק את השגיאות.
    outcomes: List[RowOutcome] = field(default_factory=list)
    errors: List[RowOutcome] = field(default_factory=list)


def _parse_date(value) -> _date:
    return _date.fromisoformat(value) if isinstance(value, str) else value


# ===================================================================
# טבלאות "רגילות" - מפתח ראשי יחיד + FK-ים שנפתרים מול scope (יעד + באצ').
# ===================================================================

@dataclass
class _TableSpec:
    pk_field: str
    scope_category: str  # שם attribute תואם על CompanyScope ועל batch_seen
    fk_checks: Tuple[Tuple[str, str], ...] = ()  # (fk_field, referenced_scope_category)


_TABLE_SPECS: Dict[str, _TableSpec] = {
    "option_pools": _TableSpec("pool_id", "pool_ids"),
    "employees": _TableSpec("employee_id", "employee_ids"),
    "trustees": _TableSpec("trustee_id", "trustee_ids"),
    "grants": _TableSpec("grant_id", "grant_ids",
                         (("pool_id", "pool_ids"), ("employee_id", "employee_ids"),
                          ("trustee_id", "trustee_ids"))),
    "vesting_schedules": _TableSpec("schedule_id", "schedule_ids", (("grant_id", "grant_ids"),)),
    "documents": _TableSpec("document_id", "document_ids",
                            (("grant_id", "grant_ids"), ("employee_id", "employee_ids"),
                             ("trustee_id", "trustee_ids"))),
    "exercise_requests": _TableSpec("request_id", "request_ids",
                                    (("grant_id", "grant_ids"), ("employee_id", "employee_ids"))),
    "exercise_tax_records": _TableSpec("record_id", "exercise_tax_record_ids",
                                       (("request_id", "request_ids"),)),
}

# מודל ORM + עמודת המפתח הראשי, לצורך בדיקת "קיים בכלל, בכל חברה" (global,
# לא מסונן) - שאילתה אחת לכל טבלה, לא אחת לכל שורה.
_PK_COLUMN = {
    "option_pools": OptionPool.pool_id,
    "employees": Employee.employee_id,
    "trustees": Trustee.trustee_id,
    "grants": Grant.grant_id,
    "vesting_schedules": VestingSchedule.schedule_id,
    "documents": Document.document_id,
    "exercise_requests": ExerciseRequest.request_id,
    "exercise_tax_records": ExerciseTaxRecord.record_id,
}


def _existing_pks_by_table(db: Session) -> Dict[str, Set[str]]:
    return {table: {row[0] for row in db.query(column).all()} for table, column in _PK_COLUMN.items()}


def _validate_normal_tables(bundle: dict, target_scope: CompanyScope,
                            batch_seen: Dict[str, Set[str]],
                            existing_pks: Dict[str, Set[str]]) -> List[RowOutcome]:
    outcomes: List[RowOutcome] = []
    for table_name, spec in _TABLE_SPECS.items():
        rows = bundle["tables"].get(table_name, [])
        seen = batch_seen.setdefault(spec.scope_category, set())
        for index, row in enumerate(rows):
            pk_value = row.get(spec.pk_field)
            error = None

            for fk_field, ref_category in spec.fk_checks:
                fk_value = row.get(fk_field)
                if fk_value is None:
                    continue  # nullable FK (למשל trustee_id) - היעדרו תקין
                if fk_value not in getattr(target_scope, ref_category) and fk_value not in batch_seen.get(
                        ref_category, set()):
                    error = f"{fk_field}={fk_value!r} does not resolve to any known {ref_category}"
                    break

            if error is None and pk_value in seen:
                error = f"duplicate {spec.pk_field}={pk_value!r} within the same import bundle"

            if error is None and pk_value in getattr(target_scope, spec.scope_category):
                outcomes.append(RowOutcome(table_name, index, pk_value, SKIP_EXISTING))
                seen.add(pk_value)
                continue

            if error is None and pk_value in existing_pks.get(table_name, set()):
                error = f"{spec.pk_field}={pk_value!r} already exists under a different company"

            if error:
                outcomes.append(RowOutcome(table_name, index, pk_value, ERROR, error))
            else:
                outcomes.append(RowOutcome(table_name, index, pk_value, NEW))
                seen.add(pk_value)
    return outcomes


# ===================================================================
# חבילות מס - natural key, לא scope, לא pack_id (הוסר בייצוא).
# ===================================================================

def _tax_key(row: dict, *, with_bracket_order: bool = False) -> tuple:
    key = (row["country_code"], row["grant_type"], _parse_date(row["effective_start_date"]))
    return key + (row["bracket_order"],) if with_bracket_order else key


def _validate_tax_tables(db: Session, bundle: dict) -> List[RowOutcome]:
    outcomes: List[RowOutcome] = []

    existing_pack_keys = {
        (p.country_code, p.grant_type, p.effective_start_date) for p in db.query(TaxRulePack).all()
    }
    batch_pack_keys: Set[tuple] = set()
    for index, row in enumerate(bundle["tables"].get("tax_rule_packs", [])):
        key = _tax_key(row)
        status = SKIP_EXISTING if key in existing_pack_keys else NEW
        outcomes.append(RowOutcome("tax_rule_packs", index, str(key), status))
        batch_pack_keys.add(key)
    known_pack_keys = existing_pack_keys | batch_pack_keys

    existing_rate_keys = {
        (r.country_code, r.grant_type, r.effective_start_date) for r in db.query(TaxRatesHistory).all()
    }
    for index, row in enumerate(bundle["tables"].get("tax_rates_history", [])):
        key = _tax_key(row)
        if key not in known_pack_keys:
            outcomes.append(RowOutcome("tax_rates_history", index, str(key), ERROR,
                                       f"no matching tax_rule_packs row for natural key {key}"))
            continue
        status = SKIP_EXISTING if key in existing_rate_keys else NEW
        outcomes.append(RowOutcome("tax_rates_history", index, str(key), status))

    existing_bracket_keys = {
        (b.country_code, b.grant_type, b.effective_start_date, b.bracket_order)
        for b in db.query(IncomeTaxBracket).all()
    }
    for index, row in enumerate(bundle["tables"].get("income_tax_brackets", [])):
        pack_key = _tax_key(row)
        if pack_key not in known_pack_keys:
            outcomes.append(RowOutcome("income_tax_brackets", index, str(pack_key), ERROR,
                                       f"no matching tax_rule_packs row for natural key {pack_key}"))
            continue
        full_key = _tax_key(row, with_bracket_order=True)
        status = SKIP_EXISTING if full_key in existing_bracket_keys else NEW
        outcomes.append(RowOutcome("income_tax_brackets", index, str(full_key), status))

    return outcomes


# ===================================================================
# LedgerEvent - scope נגזר מה-aggregate, אידמפוטנטיות על (aggregate_id,
# sequence_no) ולא על event_id (decision 5).
# ===================================================================

_LEDGER_AGGREGATE_CATEGORY = {
    "OptionPool": "pool_ids",
    "Employee": "employee_ids",
    "Grant": "grant_ids",
    "VestingSchedule": "schedule_ids",
    "ExerciseRequest": "request_ids",
}


def _validate_ledger_events(db: Session, bundle: dict, target_scope: CompanyScope,
                            batch_seen: Dict[str, Set[str]]) -> List[RowOutcome]:
    outcomes: List[RowOutcome] = []
    rows = bundle["tables"].get("ledger_events", [])

    existing_pairs = {(e.aggregate_id, e.sequence_no) for e in
                      db.query(LedgerEvent.aggregate_id, LedgerEvent.sequence_no).all()}
    existing_event_ids = {row[0] for row in db.query(LedgerEvent.event_id).all()}
    seen_pairs: Set[tuple] = set()
    seen_event_ids: Set[str] = set()

    for index, row in enumerate(rows):
        event_id = row.get("event_id")
        aggregate_type = row.get("aggregate_type")
        aggregate_id = row.get("aggregate_id")
        category = _LEDGER_AGGREGATE_CATEGORY.get(aggregate_type)

        if category is None:
            outcomes.append(RowOutcome("ledger_events", index, event_id, ERROR,
                                       f"unknown aggregate_type {aggregate_type!r}"))
            continue
        if aggregate_id not in getattr(target_scope, category) and aggregate_id not in batch_seen.get(
                category, set()):
            outcomes.append(RowOutcome("ledger_events", index, event_id, ERROR,
                                       f"aggregate_id {aggregate_id!r} ({aggregate_type}) not found "
                                       "in this import batch or in the target company"))
            continue

        corrects = row.get("corrects_event_id")
        if corrects is not None and corrects not in existing_event_ids and corrects not in seen_event_ids:
            outcomes.append(RowOutcome("ledger_events", index, event_id, ERROR,
                                       f"corrects_event_id {corrects!r} precedes its own correction "
                                       "in this batch - append-only order violated"))
            continue

        pair = (aggregate_id, row.get("sequence_no"))
        status = SKIP_EXISTING if (pair in existing_pairs or pair in seen_pairs) else NEW
        outcomes.append(RowOutcome("ledger_events", index, event_id, status))
        seen_pairs.add(pair)
        seen_event_ids.add(event_id)

    return outcomes


# ===================================================================
# AuditLog / Notification* - התנגשות מפתח ראשי בלבד. entity_type="User" /
# user_id לא מאומתים - טבלת users מחוץ להיקף (decision 1), ראו docstring.
# ===================================================================

def _validate_by_pk_only(db: Session, bundle: dict, table_name: str, model, pk_field: str) -> List[RowOutcome]:
    existing = {row[0] for row in db.query(getattr(model, pk_field)).all()}
    outcomes = []
    for index, row in enumerate(bundle["tables"].get(table_name, [])):
        pk_value = row.get(pk_field)
        status = SKIP_EXISTING if pk_value in existing else NEW
        outcomes.append(RowOutcome(table_name, index, pk_value, status))
    return outcomes


def _validate_audit_and_notifications(db: Session, bundle: dict) -> List[RowOutcome]:
    return (
        _validate_by_pk_only(db, bundle, "audit_log", AuditLog, "audit_id")
        + _validate_by_pk_only(db, bundle, "notification_preferences", NotificationPreference, "preference_id")
        + _validate_by_pk_only(db, bundle, "notification_dismissals", NotificationDismissal, "dismissal_id")
    )


# ===================================================================
# נקודת הכניסה
# ===================================================================

def dry_run(db: Session, bundle: dict, target_company_id: str) -> ImportDryRunReport:
    """אימות טהור: לא db.add, לא db.commit, על אף שורת דומיין מהחבילה. schema_version
    ותקינות בסיסית כבר נבדקו ב-parse_and_validate_bundle_shape - קורא זה מניח חבילה
    שכבר עברה את השער הזה."""
    target_scope = build_company_scope(db, target_company_id)
    existing_pks = _existing_pks_by_table(db)
    batch_seen: Dict[str, Set[str]] = {}

    outcomes: List[RowOutcome] = []
    outcomes += _validate_normal_tables(bundle, target_scope, batch_seen, existing_pks)
    outcomes += _validate_tax_tables(db, bundle)
    outcomes += _validate_ledger_events(db, bundle, target_scope, batch_seen)
    outcomes += _validate_audit_and_notifications(db, bundle)

    errors = [o for o in outcomes if o.status == ERROR]
    return ImportDryRunReport(
        valid=not errors,
        rows_attempted=len(outcomes),
        rows_new=sum(1 for o in outcomes if o.status == NEW),
        rows_skipped_existing=sum(1 for o in outcomes if o.status == SKIP_EXISTING),
        rows_failed=len(errors),
        outcomes=outcomes,
        errors=errors,
    )
