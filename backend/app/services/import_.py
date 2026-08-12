"""ייבוא נתוני חברה (v0.9.1 שלב ב) - דריי-ראן + commit (PLAN.md §8 steps 6-7).

**מודל היעד**: הייבוא תמיד נכתב תחת company_id של
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

**נמצא בזמן תכנון commit() (task #7), לא היה מפורש קודם**: users אינה רק "לא
מאומתת" - היא *לעולם לא קיימת* ביעד (מעולם לא מיובאת). כל עמודת *_user_id
היא FK אמיתי ל-users עם אכיפה בפועל (PRAGMA foreign_keys=ON, database.py),
ולכן commit() מאפס אותה תמיד לפני כתיבה - documents.acknowledged_by_user_id/
created_by_user_id, exercise_requests.reviewed_by_user_id, audit_log.actor_user_id,
ledger_events.actor_user_id. לא "לפעמים, אם המשתמש קיים גם ביעד" - איפוס
עקבי, כדי שההתנהגות לא תהיה תלויה בצירוף מקרים (אותו employee_id/admin
שקיים גם ביעד). ייחוס "מי ביצע" לא שורד מעבר לגבול המערכת; הפעולה עצמה שורדת.

**notification_preferences/notification_dismissals הן חריג נוסף, חמור יותר**:
ה-user_id שלהן הוא NOT NULL (init_scheme.sql), לא nullable כמו כל שאר עמודות
ה-*_user_id - כלומר אי אפשר לאפס אותו. מכיוון ש-users לעולם לא מיובאת, שורה
*חדשה* בטבלאות האלה לא ניתנת לכתיבה בשום מצב (תפגע תמיד ב-FK), לא רק "תיכשל
אם המשתמש לא קיים". commit() אף פעם לא כותב לשתי הטבלאות האלה; dry_run
מסמן שורה חדשה בהן כ-NOT_PORTABLE (סטטוס נפרד מ-NEW/SKIP_EXISTING/ERROR) -
לא ERROR (לא חוסם ייבוא של שאר החבילה בגלל הגדרות התראה אישיות), ולא NEW
(היה משקר - השורה לעולם לא באמת תיכתב). שורה קיימת עדיין SKIP_EXISTING
כרגיל (היא כבר ביעד, אין מה לכתוב מלכתחילה).
"""

import json
from dataclasses import dataclass, field
from datetime import date as _date, datetime as _datetime
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import Date as _SADate, Enum as _SQLEnum
from sqlalchemy.orm import Session

from backend.app.models import (
    AuditLog, Document, Employee, ExerciseRequest, ExerciseTaxRecord, Grant,
    IncomeTaxBracket, LedgerEvent, NotificationDismissal, NotificationPreference,
    OptionPool, ShareClass, ShareIssuance, Shareholder, TaxRatesHistory,
    TaxRulePack, Trustee, VestingSchedule,
)
from backend.app.services.export import EXPORT_SCHEMA_VERSION, CompanyScope, build_company_scope
from backend.app.services.ledger import project, record_ownership
from backend.app.types import UtcDateTime

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
# שורה חדשה בטבלה שיש לה FK חובה (NOT NULL) ל-users, שלעולם לא מיובאת (decision 1) -
# ראו הפסקה על notification_preferences/notification_dismissals בראש הקובץ. לא ERROR
# (לא חוסם את שאר החבילה) ולא NEW (commit() לעולם לא כותב שורה כזו בפועל).
NOT_PORTABLE = "NOT_PORTABLE"


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
    rows_not_portable: int = 0
    rows_failed: int = 0
    # outcomes כולל כל שורה (גם NEW/SKIP/NOT_PORTABLE) - commit() צריך את זה כדי
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
    # v1.0.1: share_classes חייבת לבוא לפני option_pools - option_pools.share_class_id
    # הוא fk_check שנפתר מול batch_seen["share_class_ids"], וזה מאוכלס רק אחרי
    # שהטבלה שמקדימה אותה ב-_TABLE_SPECS עברה עיבוד (ראו _validate_normal_tables:
    # הוא איטרטור יחיד על הדיקט הזה, בסדר שלו בדיוק). אותו עיקרון בדיוק כמו
    # pool_ids/employee_ids/trustee_ids שכבר זמינים לפני grants.
    "share_classes": _TableSpec("share_class_id", "share_class_ids"),
    "option_pools": _TableSpec("pool_id", "pool_ids", (("share_class_id", "share_class_ids"),)),
    "employees": _TableSpec("employee_id", "employee_ids"),
    "trustees": _TableSpec("trustee_id", "trustee_ids"),
    # shareholders אחרי employees (employee_id) ואחרי share_classes; share_issuances
    # אחרי שניהם - אותו סדר טופולוגי, פעם אחת.
    "shareholders": _TableSpec("shareholder_id", "shareholder_ids",
                               (("employee_id", "employee_ids"),)),
    "share_issuances": _TableSpec("share_issuance_id", "share_issuance_ids",
                                  (("shareholder_id", "shareholder_ids"),
                                   ("share_class_id", "share_class_ids"))),
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
    "share_classes": ShareClass.share_class_id,
    "option_pools": OptionPool.pool_id,
    "employees": Employee.employee_id,
    "trustees": Trustee.trustee_id,
    "shareholders": Shareholder.shareholder_id,
    "share_issuances": ShareIssuance.share_issuance_id,
    "grants": Grant.grant_id,
    "vesting_schedules": VestingSchedule.schedule_id,
    "documents": Document.document_id,
    "exercise_requests": ExerciseRequest.request_id,
    "exercise_tax_records": ExerciseTaxRecord.record_id,
}

# אותן טבלאות, המודל עצמו - task #7 (commit) בונה שורת ORM ישירות מ-
# _TABLE_SPECS, באותו סדר טופולוגי שכבר נבדק (§3, ותוקן ב-v1.0.1): share_classes
# לפני option_pools (share_class_id) ולפני shareholders/share_issuances,
# employees/trustees לפני grants, share_classes+shareholders לפני
# share_issuances, grants לפני vesting_schedules/documents/exercise_requests,
# exercise_requests לפני exercise_tax_records.
_MODEL_BY_TABLE = {
    "share_classes": ShareClass,
    "option_pools": OptionPool,
    "employees": Employee,
    "trustees": Trustee,
    "shareholders": Shareholder,
    "share_issuances": ShareIssuance,
    "grants": Grant,
    "vesting_schedules": VestingSchedule,
    "documents": Document,
    "exercise_requests": ExerciseRequest,
    "exercise_tax_records": ExerciseTaxRecord,
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
    # v1.0.1: ShareIssuance הוא ledger aggregate מ-v1.0.0 (LEDGER_AGGREGATE_TYPES,
    # models.py) עם סוג אירוע יחיד (SHARE_ISSUANCE_ESTABLISHED) - בלי השורה הזו,
    # כל ledger_events row מסוג הזה בחבילה היה נכשל כ-"unknown aggregate_type".
    "ShareIssuance": "share_issuance_ids",
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

def _validate_by_pk_only(db: Session, bundle: dict, table_name: str, model, pk_field: str,
                         *, not_portable: bool = False) -> List[RowOutcome]:
    """not_portable=True: טבלה שבה שורה *חדשה* לעולם לא ניתנת לכתיבה (FK חובה
    ל-users שלעולם לא קיימת ביעד - ראו הפסקה בראש הקובץ). שורה קיימת עדיין
    SKIP_EXISTING כרגיל; רק הענף החדש מסומן NOT_PORTABLE במקום NEW."""
    existing = {row[0] for row in db.query(getattr(model, pk_field)).all()}
    new_status = NOT_PORTABLE if not_portable else NEW
    outcomes = []
    for index, row in enumerate(bundle["tables"].get(table_name, [])):
        pk_value = row.get(pk_field)
        status = SKIP_EXISTING if pk_value in existing else new_status
        outcomes.append(RowOutcome(table_name, index, pk_value, status))
    return outcomes


def _validate_audit_and_notifications(db: Session, bundle: dict) -> List[RowOutcome]:
    return (
        _validate_by_pk_only(db, bundle, "audit_log", AuditLog, "audit_id")
        + _validate_by_pk_only(db, bundle, "notification_preferences", NotificationPreference,
                               "preference_id", not_portable=True)
        + _validate_by_pk_only(db, bundle, "notification_dismissals", NotificationDismissal,
                               "dismissal_id", not_portable=True)
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
        rows_not_portable=sum(1 for o in outcomes if o.status == NOT_PORTABLE),
        rows_failed=len(errors),
        outcomes=outcomes,
        errors=errors,
    )


# ===================================================================
# commit() - הכתיבה בפועל (PLAN.md §8 step 7). מריץ dry_run מחדש מול ה-bundle
# שהתקבל - לא סומך על דוח ישן שיכול היה להתיישן בין הרגע שהוא חושב לרגע
# ה-commit (למשל: מישהו אחר ייבא בינתיים וייצר התנגשות חדשה). לא כותב שום
# דבר אם dry_run לא תקין - decision 3 הוא all-or-nothing, לא כתיבה חלקית של
# השורות שכן היו תקינות. לא עושה db.commit() - אותה מוסכמה כמו שאר ה-services
# (append_event/run_export/dry_run עצמו): ה-caller (endpoint עתידי, task #8)
# סוגר את הטרנזקציה.
# ===================================================================

# הטבלאות היחידות עם עמודת company_id בפועל (models.py) - decision 9: לעולם
# לא מהקובץ, נאכף כאן ולא ב-dry_run כי dry_run לא כותב כלום. Grant/VestingSchedule/
# ExerciseRequest/ExerciseTaxRecord אין להן עמודת company_id בכלל (ה-scoping
# שלהן נגזר דרך שרשור FK, כבר אומת ב-dry_run).
# v1.0.1: share_classes/shareholders/share_issuances מצטרפות - כל שלושתן
# נושאות company_id ישיר (models.py), בדיוק כמו option_pools. אל תסמכו על
# ה"ledger-native" של ShareIssuance כטעם לדלג עליה כאן - יש לה עמודת company_id
# ישירה ולא-nullable בדיוק כמו option_pools, בשונה מ-Grant/VestingSchedule.
# בלעדי השורה הזו, bundle עם company_id זר בשורת share_issuances/shareholders
# היה נכתב עם הערך מהקובץ, לא נדרס לחברת היעד (ראו סקירת האבטחה בתכנון).
_FORCE_COMPANY_ID_TABLES = {
    "option_pools", "employees", "trustees", "documents",
    "share_classes", "shareholders", "share_issuances",
}

# עמודות *_user_id שמאופסות תמיד בכתיבה - ראו הפסקה המורחבת בראש הקובץ:
# users לעולם לא מיובאת (decision 1), אז כל reference אליה מהקובץ לא יכול
# לפתור ביעד. ledger_events/audit_log לא ב-_MODEL_BY_TABLE (מטופלים בנפרד
# למטה) אבל חולקים את אותו העיקרון בדיוק.
_NULLED_USER_COLUMNS: Dict[str, Tuple[str, ...]] = {
    "documents": ("acknowledged_by_user_id", "created_by_user_id"),
    "exercise_requests": ("reviewed_by_user_id",),
    "audit_log": ("actor_user_id",),
    "ledger_events": ("actor_user_id",),
}

# aggregate_type (ledger.py) לכל טבלה שהיא גם "בעלת" LedgerOwnership. Trustee/
# Document/ExerciseTaxRecord לא ברשימה בכוונה - הן לא סוגי aggregate בעצמן
# (LEDGER_AGGREGATE_TYPES ב-models.py), רק תכונות על ישויות אחרות.
_AGGREGATE_TYPE_BY_TABLE = {
    "option_pools": "OptionPool",
    "employees": "Employee",
    "grants": "Grant",
    "vesting_schedules": "VestingSchedule",
    "exercise_requests": "ExerciseRequest",
    "share_issuances": "ShareIssuance",
}


def _deserialize_value(column, value):
    """הופך ערך JSON-safe (isoformat string, ערך Enum כ-str) בחזרה לטיפוס
    שהעמודה מצפה לו - ההפך המדויק של export.py::_serialize_row. בלי זה
    UtcDateTime.process_bind_param נופל על value.tzinfo (ל-str אין), ו-SQLEnum
    מקבל str גולמי שלא בהכרח שווה לחבר ה-Enum שהעמודה מצפה לו."""
    if value is None:
        return None
    col_type = column.type
    if isinstance(col_type, UtcDateTime):
        return _datetime.fromisoformat(value) if isinstance(value, str) else value
    if isinstance(col_type, _SQLEnum) and col_type.enum_class is not None:
        return value if isinstance(value, col_type.enum_class) else col_type.enum_class(value)
    if isinstance(col_type, _SADate) and isinstance(value, str):
        return _date.fromisoformat(value)
    return value


def _deserialize_row(model, row: dict) -> dict:
    columns = model.__table__.columns
    return {name: _deserialize_value(columns[name], value) for name, value in row.items() if name in columns}


@dataclass
class ImportCommitReport:
    valid: bool
    rows_attempted: int = 0
    rows_written: int = 0
    rows_skipped_existing: int = 0
    rows_not_portable: int = 0
    rows_failed: int = 0
    errors: List[RowOutcome] = field(default_factory=list)


def _build_row(table_name: str, model, row: dict, *, target_company_id: str) -> object:
    values = _deserialize_row(model, row)
    if table_name in _FORCE_COMPANY_ID_TABLES:
        values["company_id"] = target_company_id
    for column_name in _NULLED_USER_COLUMNS.get(table_name, ()):
        if column_name in values:
            values[column_name] = None
    return model(**values)


def _grant_ownership_fields(db: Session, grant_id: str) -> Tuple[Optional[str], str]:
    """trustee_id/employee_id של מענק, ל-record_ownership על ישויות שנגזרות
    ממנו (VestingSchedule) - שאילתה ולא cache, כי בנקודה הזו כל grant (חדש
    או קיים) כבר flushed ל-DB (grants מעובד לפני vesting_schedules, §3)."""
    grant = db.query(Grant).filter(Grant.grant_id == grant_id).one()
    return grant.trustee_id, grant.employee_id


def _record_ownership_for_new_row(db: Session, table_name: str, obj, target_company_id: str) -> None:
    """LedgerOwnership לעולם לא מיובאת כטבלה (decision 1) - כל שורת ownership
    לישות חדשה נבנית כאן מחדש, בדיוק כמו backfill_ledger.py בזמנו. company_id
    הוא תמיד target_company_id ולא נגזר דרך pool כמו בזרימה החיה (grants.py) -
    היעד תמיד חברה אחת, זו של המייבא (ההבהרה המפורשת מ-task #6)."""
    aggregate_type = _AGGREGATE_TYPE_BY_TABLE.get(table_name)
    if aggregate_type is None:
        return
    if table_name == "option_pools":
        record_ownership(db, aggregate_id=obj.pool_id, aggregate_type=aggregate_type,
                         company_id=target_company_id)
    elif table_name == "employees":
        record_ownership(db, aggregate_id=obj.employee_id, aggregate_type=aggregate_type,
                         company_id=target_company_id, employee_id=obj.employee_id)
    elif table_name == "grants":
        record_ownership(db, aggregate_id=obj.grant_id, aggregate_type=aggregate_type,
                         company_id=target_company_id, trustee_id=obj.trustee_id,
                         employee_id=obj.employee_id)
    elif table_name == "vesting_schedules":
        trustee_id, employee_id = _grant_ownership_fields(db, obj.grant_id)
        record_ownership(db, aggregate_id=obj.schedule_id, aggregate_type=aggregate_type,
                         company_id=target_company_id, trustee_id=trustee_id, employee_id=employee_id)
    elif table_name == "exercise_requests":
        record_ownership(db, aggregate_id=obj.request_id, aggregate_type=aggregate_type,
                         company_id=target_company_id, employee_id=obj.employee_id)
    elif table_name == "share_issuances":
        # אותו קריאה בדיוק כמו create_share_issuance (cap_table.py) בזרימה
        # החיה - בלי employee_id/trustee_id, כי ShareIssuance לא נושא אותם
        # ישירות (shareholder_id בלבד, ו-Shareholder.employee_id הוא nullable).
        record_ownership(db, aggregate_id=obj.share_issuance_id, aggregate_type=aggregate_type,
                         company_id=target_company_id)


def _write_normal_tables(db: Session, bundle: dict, outcomes_by_table: Dict[str, Dict[int, RowOutcome]],
                         target_company_id: str) -> int:
    """כותב את שמונה טבלאות _TABLE_SPECS בסדר הטופולוגי הקבוע שלו (אותו סדר
    שכבר אומת ב-dry_run, §3), flush בין טבלה לטבלה - הלקח מ-task #2
    (HANDOFF.md): SQLAlchemy לא מבטיח סדר INSERT בין טבלאות שחולקות FK גולמי
    בלי relationship()."""
    written = 0
    for table_name, model in _MODEL_BY_TABLE.items():
        rows = bundle["tables"].get(table_name, [])
        table_outcomes = outcomes_by_table.get(table_name, {})
        new_objects = [
            _build_row(table_name, model, row, target_company_id=target_company_id)
            for index, row in enumerate(rows)
            if table_outcomes.get(index) is not None and table_outcomes[index].status == NEW
        ]
        if not new_objects:
            continue
        db.add_all(new_objects)
        db.flush()
        written += len(new_objects)
        for obj in new_objects:
            _record_ownership_for_new_row(db, table_name, obj, target_company_id)
    return written


def _write_tax_tables(db: Session, bundle: dict, outcomes_by_table: Dict[str, Dict[int, RowOutcome]]) -> int:
    """tax_rule_packs.pack_id מוסר בייצוא (export.py::_TAX_TABLE_EXCLUDED_COLUMNS) -
    כולל על tax_rule_packs עצמו, לא רק על שתי הטבלאות המפנות אליו. כל pack חדש
    מקבל pack_id טרי (generate_uuid(), models.py default), ו-tax_rates_history/
    income_tax_brackets מפנים אליו מחדש לפי natural key - בקובץ אין להן בכלל
    pack_id להסתמך עליו (בדיוק הסיבה ש-decision 1 קיים: pack_id לא שורד
    seed/backfill חדש, ו-tax_engine.py._calculate_flat/_calculate_progressive
    מסננים דווקא לפי pack_id - בלי הפתרון הזה חישוב מס על היעד לא היה מוצא
    את שורות הפירוט בכלל)."""
    written = 0

    pack_outcomes = outcomes_by_table.get("tax_rule_packs", {})
    new_packs = [
        TaxRulePack(**_deserialize_row(TaxRulePack, row))
        for index, row in enumerate(bundle["tables"].get("tax_rule_packs", []))
        if pack_outcomes.get(index) is not None and pack_outcomes[index].status == NEW
    ]
    if new_packs:
        db.add_all(new_packs)
        db.flush()
        written += len(new_packs)

    pack_id_by_key = {
        (p.country_code, p.grant_type, p.effective_start_date): p.pack_id
        for p in db.query(TaxRulePack).all()
    }

    for table_name, model in (("tax_rates_history", TaxRatesHistory), ("income_tax_brackets", IncomeTaxBracket)):
        table_outcomes = outcomes_by_table.get(table_name, {})
        new_objects = []
        for index, row in enumerate(bundle["tables"].get(table_name, [])):
            outcome = table_outcomes.get(index)
            if outcome is None or outcome.status != NEW:
                continue
            values = _deserialize_row(model, row)
            key = (values["country_code"], values["grant_type"], values["effective_start_date"])
            values["pack_id"] = pack_id_by_key[key]  # dry_run כבר אימת שה-key הזה קיים
            new_objects.append(model(**values))
        if new_objects:
            db.add_all(new_objects)
            db.flush()
            written += len(new_objects)

    return written


def _write_ledger_events(db: Session, bundle: dict,
                         outcomes_by_table: Dict[str, Dict[int, RowOutcome]]) -> Tuple[int, Set[str]]:
    """מכניס LedgerEvent ישירות (LedgerEvent(...)), *לעולם לא* דרך append_event -
    append_event היה מייחס sequence_no חדש ומברירת מחדל recorded_at לרגע ה-
    commit, ומוחק בדיוק את מה שהאידמפוטנטיות של decision 5 אמורה לשמר
    (recorded_at ההיסטורי האמיתי - ראו §7 סיכון 1). מחזיר גם את קבוצת ה-
    aggregate_id-ים מסוג OptionPool שקיבלו אירוע חדש, לשימוש בפרויקציה
    שרצה פעם אחת אחרי כל הבאטש (§3)."""
    table_outcomes = outcomes_by_table.get("ledger_events", {})
    new_objects = []
    touched_pool_ids: Set[str] = set()
    for index, row in enumerate(bundle["tables"].get("ledger_events", [])):
        outcome = table_outcomes.get(index)
        if outcome is None or outcome.status != NEW:
            continue
        values = _deserialize_row(LedgerEvent, row)
        values["actor_user_id"] = None
        new_objects.append(LedgerEvent(**values))
        if row.get("aggregate_type") == "OptionPool":
            touched_pool_ids.add(row["aggregate_id"])
    if new_objects:
        db.add_all(new_objects)
        db.flush()
    return len(new_objects), touched_pool_ids


def _write_audit_log(db: Session, bundle: dict, outcomes_by_table: Dict[str, Dict[int, RowOutcome]]) -> int:
    table_outcomes = outcomes_by_table.get("audit_log", {})
    new_objects = []
    for index, row in enumerate(bundle["tables"].get("audit_log", [])):
        outcome = table_outcomes.get(index)
        if outcome is None or outcome.status != NEW:
            continue
        values = _deserialize_row(AuditLog, row)
        values["actor_user_id"] = None
        new_objects.append(AuditLog(**values))
    if new_objects:
        db.add_all(new_objects)
        db.flush()
    return len(new_objects)


def _recompute_option_pool_projections(db: Session, pool_ids: Set[str]) -> None:
    """אחרי כל הבאטש, לא אירוע-אירוע (§3) - מונע דריפט בין העמודה המוטטת
    לבין קיפול ה-ledger כשפול שכבר קיים ביעד (SKIP_EXISTING, decision D)
    מקבל אירועים היסטוריים חדשים מהייבוא: השורה עצמה לא נדרסת, אבל היתרה
    המוטטת שלה חייבת לשקף את ההיסטוריה המלאה עכשיו - לא רק את מה שהייתה
    ב-snapshot של המקור בזמן הייצוא."""
    for pool_id in pool_ids:
        state = project(db, "OptionPool", pool_id)
        if state is None:
            continue
        pool = db.get(OptionPool, pool_id)
        if pool is None:
            continue
        pool.allocated_shares = state["allocated_shares"]
        pool.unallocated_shares = state["unallocated_shares"]
    if pool_ids:
        db.flush()


def commit(db: Session, bundle: dict, target_company_id: str) -> ImportCommitReport:
    """נקודת הכניסה של הכתיבה בפועל (PLAN.md §8 step 7, HANDOFF.md task #7).
    מריץ dry_run מחדש - ראו הערת המודול על כך למעלה. אם לא תקין, לא נוגע ב-DB
    בכלל (ImportCommitReport(valid=False), rows_written=0).

    notification_preferences/notification_dismissals לעולם לא נכתבות כאן -
    שורה חדשה בהן היא NOT_PORTABLE (ולא NEW) כבר ב-dry_run, ולכן לולאות הכתיבה
    למטה, שכולן פועלות רק על status == NEW, לא נוגעות בהן מעולם. ראו הפסקה
    המורחבת בראש הקובץ."""
    report = dry_run(db, bundle, target_company_id)
    if not report.valid:
        return ImportCommitReport(
            valid=False, rows_attempted=report.rows_attempted, rows_written=0,
            rows_skipped_existing=report.rows_skipped_existing,
            rows_not_portable=report.rows_not_portable,
            rows_failed=report.rows_failed, errors=report.errors,
        )

    outcomes_by_table: Dict[str, Dict[int, RowOutcome]] = {}
    for outcome in report.outcomes:
        outcomes_by_table.setdefault(outcome.table, {})[outcome.index] = outcome

    rows_written = 0
    rows_written += _write_normal_tables(db, bundle, outcomes_by_table, target_company_id)
    rows_written += _write_tax_tables(db, bundle, outcomes_by_table)
    ledger_written, touched_pool_ids = _write_ledger_events(db, bundle, outcomes_by_table)
    rows_written += ledger_written
    rows_written += _write_audit_log(db, bundle, outcomes_by_table)

    _recompute_option_pool_projections(db, touched_pool_ids)

    return ImportCommitReport(
        valid=True, rows_attempted=report.rows_attempted, rows_written=rows_written,
        rows_skipped_existing=report.rows_skipped_existing,
        rows_not_portable=report.rows_not_portable, rows_failed=0, errors=[],
    )
