"""ייצוא נתוני חברה (v0.9.1 שלב ב) - ההיקף המלא.

השלוחה הראשונה (Company+Employee בלבד) אימתה את התשתית המסוכנת - רישום
היסטוריה, הרשאת הורדה, אי-הגשה כקובץ סטטי. השלב הזה מוסיף את שאר הטבלאות,
CSV, וחבילות המס - בלי לשנות את הצורה שכבר אומתה.

**גבול הגנרי מול הדומייני**: TABLE_REGISTRY הוא מהלך אחיד - (db, scope) ->
רשימת שורות ORM - על טבלאות שנקבעות ע"י פילטר company_id ישיר או join יחיד.
שלוש חתירות דומייניות נשארות מחוץ לרישום כי הן לא "טבלה לפי company_id":
(1) LedgerEvent - נקבע לפי LedgerOwnership, לא עמודת company_id שאין לו.
(2) AuditLog - dispatch לפי entity_type, כי אין לו עמודת company_id בכלל.
(3) חבילות מס - natural key (לא company_id בכלל; ראו למטה) ומחושבות פעם אחת
    כי שלוש הטבלאות (packs/rates/brackets) חולקות את אותו natural key.

export_store/ - אותה מוסכמה בדיוק כמו document_store/: תיקייה מקומית, לא
ב-git, ולעולם לא מוגשת כקובץ סטטי - רק דרך endpoint מאומת.

**קריאה יחידה, לא ריבוי sessions**: run_export מקבל session אחד מה-endpoint
(אותו session שה-request כולו רץ עליו) ולא פותח sessions נוספים. SQLAlchemy
עם autocommit=False (database.py) פותח טרנזקציה אחת מרומזת בקריאה הראשונה
ומשאיר אותה פתוחה עד commit/rollback - ולכן כל השאילתות כאן משתפות תמונת מצב
עקבית אחת כל עוד אף אחת מהן לא עושה commit באמצע (ואף אחת כאן לא עושה). זה
פותר את חוסר-העקביות ש-PLAN.md §7 (סיכון 1, WAL) חשש ממנו, בלי BEGIN מפורש
שהיה מתנגש עם ניהול הטרנזקציה של ה-ORM.
"""

import csv
import io
import json
import os
import zipfile
from dataclasses import dataclass, field
from datetime import date as _date, datetime as _datetime
from enum import Enum as _Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from backend.app.models import (
    AuditLog, Company, Document, Employee, ExerciseRequest, ExerciseTaxRecord,
    Grant, IncomeTaxBracket, LedgerEvent, LedgerOwnership, NotificationDismissal,
    NotificationPreference, OptionPool, TaxRatesHistory, TaxRulePack, Trustee,
    User, VestingSchedule,
)

EXPORT_STORE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "export_store"

# גרסת *צורת* חבילת הייצוא (JSON/CSV) - עולה כשהפורמט משתנה בצורה לא-תואמת-
# לאחור, לא כשמתווספת טבלה חדשה לתחום הייצוא. שם export_schema_version ולא
# schema_version בכוונה: LedgerEvent.schema_version (models.py) מתאר את צורת
# ה-payload של אירוע *בודד* - מושג אחר, וקונפליקט שם היה יוצר מיפוי עמודות
# שקט בין ה-JSON של הייצוא ליומן האירועים המיוצא בתוכו.
EXPORT_SCHEMA_VERSION = 1

# ראו IncomeTaxBracket ב-models.py / backend/seed_data.py:1013 - כל שורת מס
# שנזרעת היום נושאת את הסימון הזה. חבילת ייצוא שמכילה שורה כזו מסמנת זאת
# ברמת ה-bundle (contains_demo_tax_data), לא רק בהערת קוד.
DEMO_TAX_SOURCE_SENTINEL = "DEMO-NOT-REAL-TAX-LAW"

_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _ensure_store_dir() -> None:
    os.makedirs(EXPORT_STORE_DIR, exist_ok=True)


def _serialize_row(row) -> dict:
    """המרה גנרית של שורת ORM ל-dict מוכן ל-JSON: Enum -> value, date/datetime
    -> isoformat. לא clever - זו בדיוק אותה המרה שכל serializer ידני היה עושה
    בעצמו, רק פעם אחת לפי __table__.columns במקום פעם לכל מודל."""
    result = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        if isinstance(value, _Enum):
            value = value.value
        elif isinstance(value, (_date, _datetime)):
            value = value.isoformat()
        result[column.name] = value
    return result


# חבילות מס בלבד: pack_id מתחדש בכל seed/backfill (models.py, generate_uuid())
# ולא שורד בין שני מופעי DB - הזהות היחידה שמותר להתאים לפיה היא המפתח הטבעי
# (country_code, grant_type, effective_start_date), שכבר יושב על השורה עצמה.
# מייצוא מוסר את pack_id כדי שיבוא עתידי לא יתפתה להתאים לפיו.
_TAX_TABLES = {"tax_rule_packs", "tax_rates_history", "income_tax_brackets"}
_TAX_TABLE_EXCLUDED_COLUMNS = {"pack_id"}


def _serialize_tax_row(row) -> dict:
    return {k: v for k, v in _serialize_row(row).items() if k not in _TAX_TABLE_EXCLUDED_COLUMNS}


@dataclass
class CompanyScope:
    """מזהים בהיקף החברה, מחושבים פעם אחת ב-build_company_scope ומשמשים את כל
    ה-loaders - כדי שלוגיקת ה-scoping לא תוכפל (ותסתטה) בכל טבלה בנפרד,
    בדיוק דפוס P3 (QA_TESTBOOK.md) שכבר תיקן את documents.py/exercise_requests.py."""
    company_id: str
    pool_ids: Set[str] = field(default_factory=set)
    employee_ids: Set[str] = field(default_factory=set)
    trustee_ids: Set[str] = field(default_factory=set)
    grant_ids: Set[str] = field(default_factory=set)
    request_ids: Set[str] = field(default_factory=set)
    document_ids: Set[str] = field(default_factory=set)
    schedule_ids: Set[str] = field(default_factory=set)
    user_ids: Set[str] = field(default_factory=set)
    # שני האחרונים לא נדרשו לפני task #6 (services/import_.py) - שם build_company_scope
    # משמש גם כדי לבדוק "האם השורה הזו כבר שייכת לחברת היעד" בזמן ייבוא, לא רק לייצוא.
    exercise_tax_record_ids: Set[str] = field(default_factory=set)


def _ids(db: Session, column, *filters) -> Set[str]:
    return {row[0] for row in db.query(column).filter(*filters).all()}


def build_company_scope(db: Session, company_id: str) -> CompanyScope:
    pool_ids = _ids(db, OptionPool.pool_id, OptionPool.company_id == company_id)
    employee_ids = _ids(db, Employee.employee_id, Employee.company_id == company_id)
    trustee_ids = _ids(db, Trustee.trustee_id, Trustee.company_id == company_id)
    grant_ids = _ids(db, Grant.grant_id, Grant.pool_id.in_(pool_ids)) if pool_ids else set()
    request_ids = (_ids(db, ExerciseRequest.request_id, ExerciseRequest.grant_id.in_(grant_ids))
                   if grant_ids else set())
    document_ids = _ids(db, Document.document_id, Document.company_id == company_id)
    schedule_ids = (_ids(db, VestingSchedule.schedule_id, VestingSchedule.grant_id.in_(grant_ids))
                    if grant_ids else set())
    exercise_tax_record_ids = (
        _ids(db, ExerciseTaxRecord.record_id, ExerciseTaxRecord.request_id.in_(request_ids))
        if request_ids else set()
    )

    user_clauses = [User.company_id == company_id]
    if employee_ids:
        user_clauses.append(User.employee_id.in_(employee_ids))
    if trustee_ids:
        user_clauses.append(User.trustee_id.in_(trustee_ids))
    user_ids = _ids(db, User.user_id, or_(*user_clauses))

    return CompanyScope(company_id=company_id, pool_ids=pool_ids, employee_ids=employee_ids,
                        trustee_ids=trustee_ids, grant_ids=grant_ids, request_ids=request_ids,
                        document_ids=document_ids, schedule_ids=schedule_ids, user_ids=user_ids,
                        exercise_tax_record_ids=exercise_tax_record_ids)


# ===================================================================
# מגבלת גודל (PLAN.md decision 7) - v0.9.1 היא סינכרונית בכוונה, אין תור-
# עבודה/streaming (ראו FEATURE_SPEC.md). בלי גבול מפורש, חברה גדולה מספיק
# הייתה נתקעת ב-timeout שקט או בזיכרון חורג בלי שגיאה קריאה - כאן זה נכשל
# בגלוי *לפני* run_export, לא באמצעו.
# ===================================================================

class ExportTooLargeError(ValueError):
    """נזרק לפני שנפתח אף loader מלא (run_export) - ראו estimate_export_row_count.
    לא timeout/OOM שקט; כשל מפורש עם המספר בפועל, לפני שנקראה שורה אחת מלאה."""
    def __init__(self, row_count: int, limit: int):
        self.row_count = row_count
        self.limit = limit
        super().__init__(f"Export would include approximately {row_count} rows, "
                         f"exceeding the {limit}-row limit for this version")


# ניתן לעקיפה (ESOP_EXPORT_MAX_ROWS) כדי ש-QA יוכל לבדוק את שני הצדדים של
# הגבול בלי לזרוע עשרות אלפי שורות אמיתיות - לא "הערך הנכון" האחד והיחיד.
EXPORT_MAX_ROWS = int(os.environ.get("ESOP_EXPORT_MAX_ROWS", "50000"))


def estimate_export_row_count(db: Session, company_id: str) -> int:
    """סופר בלי לטעון (hydrate) שורה מלאה אחת - רק COUNT/מזהים, כדי שהבדיקה
    תיפול *לפני* run_export פותח את מנוע ההמרה המלא (Enum->value, date->iso
    על כל עמודה בכל שורה) על נתונים שבסוף יידחו ולא יישלחו לאף מקום."""
    scope = build_company_scope(db, company_id)
    count = (
        1  # החברה עצמה
        + len(scope.employee_ids) + len(scope.pool_ids) + len(scope.trustee_ids)
        + len(scope.grant_ids) + len(scope.schedule_ids) + len(scope.document_ids)
        + len(scope.request_ids)
    )

    if scope.request_ids:
        count += db.query(func.count(ExerciseTaxRecord.record_id)).filter(
            ExerciseTaxRecord.request_id.in_(scope.request_ids)).scalar()

    aggregate_ids = _ids(db, LedgerOwnership.aggregate_id, LedgerOwnership.company_id == company_id)
    if aggregate_ids:
        count += db.query(func.count(LedgerEvent.event_id)).filter(
            LedgerEvent.aggregate_id.in_(aggregate_ids)).scalar()

    for entity_type, entity_ids in {
        "Company": {company_id}, "Employee": scope.employee_ids, "Grant": scope.grant_ids,
        "TaxSimulation": scope.grant_ids, "ExerciseRequest": scope.request_ids,
        "Document": scope.document_ids, "VestingSchedule": scope.schedule_ids,
        "User": scope.user_ids,
    }.items():
        if entity_ids:
            count += db.query(func.count(AuditLog.audit_id)).filter(
                AuditLog.entity_type == entity_type, AuditLog.entity_id.in_(entity_ids)).scalar()

    if scope.user_ids:
        count += db.query(func.count(NotificationPreference.preference_id)).filter(
            NotificationPreference.user_id.in_(scope.user_ids)).scalar()
        count += db.query(func.count(NotificationDismissal.dismissal_id)).filter(
            NotificationDismissal.user_id.in_(scope.user_ids)).scalar()

    # חבילות מס: קטנות ורגילות (referenced data, לא לפי-עובד/מענק) - לא שווה
    # שאילתת natural-key מלאה רק בשביל אומדן; תמיד קטנות בהשוואה לשאר ההיקף.
    return count


def assert_export_within_size_limit(db: Session, company_id: str) -> None:
    row_count = estimate_export_row_count(db, company_id)
    if row_count > EXPORT_MAX_ROWS:
        raise ExportTooLargeError(row_count, EXPORT_MAX_ROWS)


# ===================================================================
# Loaders גנריים: (db, scope) -> רשימת שורות ORM. כל אחד מסתמך רק על scope,
# לא על שאילתה עצמאית - כדי שלא יהיו שני מקורות אמת לאותו company_id.
# ===================================================================

def _companies_in_scope(db: Session, scope: CompanyScope) -> list:
    company = db.query(Company).filter(Company.company_id == scope.company_id).first()
    return [company] if company else []


def _employees_in_scope(db: Session, scope: CompanyScope) -> list:
    return db.query(Employee).filter(Employee.company_id == scope.company_id).all()


def _option_pools_in_scope(db: Session, scope: CompanyScope) -> list:
    return db.query(OptionPool).filter(OptionPool.company_id == scope.company_id).all()


def _trustees_in_scope(db: Session, scope: CompanyScope) -> list:
    return db.query(Trustee).filter(Trustee.company_id == scope.company_id).all()


def _grants_in_scope(db: Session, scope: CompanyScope) -> list:
    if not scope.grant_ids:
        return []
    return db.query(Grant).filter(Grant.grant_id.in_(scope.grant_ids)).all()


def _vesting_schedules_in_scope(db: Session, scope: CompanyScope) -> list:
    if not scope.schedule_ids:
        return []
    return db.query(VestingSchedule).filter(VestingSchedule.schedule_id.in_(scope.schedule_ids)).all()


def _documents_in_scope(db: Session, scope: CompanyScope) -> list:
    return db.query(Document).filter(Document.company_id == scope.company_id).all()


def _exercise_requests_in_scope(db: Session, scope: CompanyScope) -> list:
    if not scope.request_ids:
        return []
    return db.query(ExerciseRequest).filter(ExerciseRequest.request_id.in_(scope.request_ids)).all()


def _exercise_tax_records_in_scope(db: Session, scope: CompanyScope) -> list:
    """v0.9.1 שלב ב: זו הרשומה שהופכת את דוח ההתאמה (task #9) לאמיתי על
    מימוש שקרה בפועל, לא רק על סימולציה - ראו models.py::ExerciseTaxRecord."""
    if not scope.request_ids:
        return []
    return db.query(ExerciseTaxRecord).filter(ExerciseTaxRecord.request_id.in_(scope.request_ids)).all()


def _ledger_events_in_scope(db: Session, scope: CompanyScope) -> list:
    """LedgerEvent אין לו עמודת company_id - ההיקף נקבע דרך LedgerOwnership,
    בדיוק כמו ledger.py::_assert_ledger_ownership. סדר לפי (aggregate_id,
    sequence_no) ולא recorded_at: זה סדר הקיפול הקנוני (models.py, אותה הערה
    על uq_ledger_events_aggregate_seq), וגם מה שהופך CSV/JSON לדטרמיניסטי בין
    שתי הרצות ייצוא זהות."""
    aggregate_ids = _ids(db, LedgerOwnership.aggregate_id, LedgerOwnership.company_id == scope.company_id)
    if not aggregate_ids:
        return []
    return (
        db.query(LedgerEvent)
        .filter(LedgerEvent.aggregate_id.in_(aggregate_ids))
        .order_by(LedgerEvent.aggregate_id, LedgerEvent.sequence_no)
        .all()
    )


# entity_type -> קבוצת ה-entity_id-ים שבהיקף. superset מכוון של audit.py
# (Company/Employee/Grant+TaxSimulation/ExerciseRequest) עם שתי תוספות
# (Document/VestingSchedule) ו-User (מזוהה דרך scope.user_ids, גם בלי
# שטבלת users עצמה מיוצאת - ראו build_company_scope).
def _audit_log_in_scope(db: Session, scope: CompanyScope) -> list:
    entity_ids_by_type = {
        "Company": {scope.company_id},
        "Employee": scope.employee_ids,
        "Grant": scope.grant_ids,
        "TaxSimulation": scope.grant_ids,
        "ExerciseRequest": scope.request_ids,
        "Document": scope.document_ids,
        "VestingSchedule": scope.schedule_ids,
        "User": scope.user_ids,
    }
    rows = []
    for entity_type, entity_ids in entity_ids_by_type.items():
        if not entity_ids:
            continue
        rows.extend(
            db.query(AuditLog)
            .filter(AuditLog.entity_type == entity_type, AuditLog.entity_id.in_(entity_ids))
            .all()
        )
    return rows


def _notification_preferences_in_scope(db: Session, scope: CompanyScope) -> list:
    if not scope.user_ids:
        return []
    return db.query(NotificationPreference).filter(NotificationPreference.user_id.in_(scope.user_ids)).all()


def _notification_dismissals_in_scope(db: Session, scope: CompanyScope) -> list:
    if not scope.user_ids:
        return []
    return db.query(NotificationDismissal).filter(NotificationDismissal.user_id.in_(scope.user_ids)).all()


@dataclass
class _TableSpec:
    model: type
    loader: Callable[[Session, CompanyScope], list]


# רישום הטבלאות ה"אחידות" - כל אחת נקבעת ע"י CompanyScope בלבד, בלי לוגיקה
# נוספת. LedgerOwnership/User/UserSession/StockPricesHistory לא ברשימה - שלוש
# ההחלטות המפורשות בתכנון (recompute-on-import, לא נתוני לקוח, לא נדרש
# כש-gain כבר נשמר על ExerciseTaxRecord).
TABLE_REGISTRY: Dict[str, _TableSpec] = {
    "companies": _TableSpec(Company, _companies_in_scope),
    "employees": _TableSpec(Employee, _employees_in_scope),
    "option_pools": _TableSpec(OptionPool, _option_pools_in_scope),
    "trustees": _TableSpec(Trustee, _trustees_in_scope),
    "grants": _TableSpec(Grant, _grants_in_scope),
    "vesting_schedules": _TableSpec(VestingSchedule, _vesting_schedules_in_scope),
    "documents": _TableSpec(Document, _documents_in_scope),
    "exercise_requests": _TableSpec(ExerciseRequest, _exercise_requests_in_scope),
    "exercise_tax_records": _TableSpec(ExerciseTaxRecord, _exercise_tax_records_in_scope),
    "ledger_events": _TableSpec(LedgerEvent, _ledger_events_in_scope),
    "audit_log": _TableSpec(AuditLog, _audit_log_in_scope),
    "notification_preferences": _TableSpec(NotificationPreference, _notification_preferences_in_scope),
    "notification_dismissals": _TableSpec(NotificationDismissal, _notification_dismissals_in_scope),
}

_MODEL_BY_TABLE: Dict[str, type] = {name: spec.model for name, spec in TABLE_REGISTRY.items()}
_MODEL_BY_TABLE.update({
    "tax_rule_packs": TaxRulePack,
    "tax_rates_history": TaxRatesHistory,
    "income_tax_brackets": IncomeTaxBracket,
})


# ===================================================================
# חבילות מס - natural key, לא company_id. ראו tax-domain-expert בתכנון
# (HANDOFF.md): pack_id לא שורד seed/backfill חדש, אז ההתאמה חייבת להיות לפי
# (country_code, grant_type, effective_start_date).
# ===================================================================

def _tax_natural_keys_in_scope(db: Session, scope: CompanyScope) -> Set[Tuple[str, str]]:
    """כל צמד (country_code, grant_type) שמענקי החברה בפועל עשויים להזדקק לו -
    לא כל הצירופים הקיימים בעולם, רק אלה שהעובד+המענק שלה בפועל יוצרים."""
    if not scope.grant_ids:
        return set()
    rows = (
        db.query(Employee.country_code, Grant.grant_type)
        .join(Grant, Grant.employee_id == Employee.employee_id)
        .filter(Grant.grant_id.in_(scope.grant_ids))
        .distinct()
        .all()
    )
    return {(country, gt.value if hasattr(gt, "value") else gt) for country, gt in rows}


def _export_tax_scope_cutoff(db: Session, scope: CompanyScope) -> Optional[_date]:
    """התאריך המאוחר ביותר שבאמת קרה בנתוני החברה (מענק אחרון שנוצר, בקשת
    מימוש אחרונה שהוגשה) - לא "היום" של השעון. חבילת מס מתאריך מאוחר יותר
    לא הייתה יכולה לחול על שום דבר שכבר קרה בפועל בחברה הזו, ובלעדי הגבול
    הזה ייצוא היה סוחב לפח כל גרסה עתידית של הטבלה, כולל כזו שנוספה
    *אחרי* הייצוא עצמו. אינו תאריך-מס על טרנזקציה בודדת (ראו
    test_the_clock_is_never_the_source_of_a_tax_date) - זה גבול סקופ לייצוא,
    לא הכרעה איזה כלל חל על מימוש ספציפי; לכן אינו נגזר מהשעון בכלל, רק
    מנתונים שכבר נשמרו."""
    candidates = []
    if scope.grant_ids:
        latest_grant = db.query(func.max(Grant.grant_date)).filter(Grant.grant_id.in_(scope.grant_ids)).scalar()
        if latest_grant:
            candidates.append(latest_grant)
    if scope.request_ids:
        latest_request = (
            db.query(func.max(ExerciseRequest.requested_at))
            .filter(ExerciseRequest.request_id.in_(scope.request_ids))
            .scalar()
        )
        if latest_request:
            candidates.append(latest_request.date())
    return max(candidates) if candidates else None


def _tax_reference_data_in_scope(db: Session, scope: CompanyScope):
    """מחזיר (packs, rates, brackets, contains_demo_tax_data) - מחושב פעם אחת
    כי שלוש הטבלאות חולקות natural key ואת אותו דגל דמו."""
    keys = _tax_natural_keys_in_scope(db, scope)
    if not keys:
        return [], [], [], False

    cutoff = _export_tax_scope_cutoff(db, scope)
    packs: list = []
    for country_code, grant_type in keys:
        q = db.query(TaxRulePack).filter(TaxRulePack.country_code == country_code,
                                         TaxRulePack.grant_type == grant_type)
        if cutoff is not None:
            q = q.filter(TaxRulePack.effective_start_date <= cutoff)
        packs.extend(q.all())

    natural_keys = {(p.country_code, p.grant_type, p.effective_start_date) for p in packs}
    rates: list = []
    brackets: list = []
    for country_code, grant_type, eff_date in natural_keys:
        rates.extend(
            db.query(TaxRatesHistory)
            .filter_by(country_code=country_code, grant_type=grant_type, effective_start_date=eff_date)
            .all()
        )
        brackets.extend(
            db.query(IncomeTaxBracket)
            .filter_by(country_code=country_code, grant_type=grant_type, effective_start_date=eff_date)
            .all()
        )

    contains_demo = any(
        row.official_source_url == DEMO_TAX_SOURCE_SENTINEL for row in (*packs, *rates, *brackets)
    )
    return packs, rates, brackets, contains_demo


def run_export(db: Session, company_id: str) -> dict:
    """בונה את חבילת הייצוא כ-dict אחד. כל טבלה ב-TABLE_REGISTRY נטענת לפי
    scope שכבר חושב; חבילות המס מחושבות בנפרד (natural key, לא scope ישיר).

    קורא זה חייב לקרוא ל-assert_export_within_size_limit קודם (ראו api/export.py) -
    לא נבדק כאן כדי שאפשר יהיה להוכיח בבדיקה שהבדיקה קרתה *לפני* שהפונקציה
    הזו נקראה בכלל, לא רק לפני שהיא סיימה.

    אין כאן מגבלת עומק JSON (decision 9): זו מגבלה על *קלט לא-ידוע* (קובץ
    ייבוא שמישהו העלה), לא על הפלט הזה - צורת ה-bundle כאן קבועה וסגורה
    (bundle -> tables -> שם טבלה -> רשימת dict-ים שטוחים), נבנית ע"י הקוד
    הזה בלבד ולא ע"י קלט חוץ, ולכן אין וקטור שבו העומק שלה יכול לחרוג.
    המגבלה האמיתית שייכת לצד הייבוא (PLAN.md §8 step 6), שם ה-JSON מגיע
    מבחוץ."""
    scope = build_company_scope(db, company_id)

    tables = {
        table_name: [_serialize_row(row) for row in spec.loader(db, scope)]
        for table_name, spec in TABLE_REGISTRY.items()
    }

    packs, rates, brackets, contains_demo = _tax_reference_data_in_scope(db, scope)
    tables["tax_rule_packs"] = [_serialize_tax_row(row) for row in packs]
    tables["tax_rates_history"] = [_serialize_tax_row(row) for row in rates]
    tables["income_tax_brackets"] = [_serialize_tax_row(row) for row in brackets]

    return {
        "export_schema_version": EXPORT_SCHEMA_VERSION,
        "company_id": company_id,
        "contains_demo_tax_data": contains_demo,
        "tables": tables,
    }


def write_export_json(bundle: dict, run_id: str) -> str:
    """כותב את החבילה כ-JSON ל-export_store/, ומחזיר נתיב *יחסי* - אותה
    מוסכמה כמו Document.file_path, כדי שהתיקייה תישאר ניידת בין מחשבים."""
    _ensure_store_dir()
    relative_path = f"{run_id}.json"
    full_path = EXPORT_STORE_DIR / relative_path
    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, indent=2)
    return relative_path


def read_export_json(relative_path: str) -> dict:
    full_path = EXPORT_STORE_DIR / relative_path
    with open(full_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _escape_formula_cells(row: dict) -> dict:
    """אם תא ב-CSV מתחיל ב-=/+/-/@, Excel מפרש אותו כנוסחה בפתיחה - וקטור
    הזרקה ידוע (CSV injection) שרלוונטי גם לייצוא של הנתונים שלך, לא רק
    לייבוא. תו ' מוביל מנטרל את הפרשנות כנוסחה בלי לשנות את הערך הנראה לעין."""
    escaped = {}
    for key, value in row.items():
        if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
            value = "'" + value
        escaped[key] = value
    return escaped


def render_bundle_as_csv_zip(bundle: dict) -> bytes:
    """ממיר bundle קיים (JSON, כבר נקרא מהאחסון) לקובץ CSV אחד לכל טבלה, בתוך
    zip - דאמפ יחסי, לא קובץ שטוח יחיד: מייצג נכון one-to-many בלי להמציא
    מפתח מיזוג. לא נשמר בנפרד ב-export_store/ בכוונה: אותה חבילת JSON היא
    התוכן היחיד המאוחסן, וה-CSV הוא ייצוג נגזר בזמן ההורדה - שני קבצים לכל
    ייצוא היו שני מקורות אמת לאותם נתונים."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for table_name, rows in bundle["tables"].items():
            model = _MODEL_BY_TABLE.get(table_name)
            if model is not None:
                columns = [c.name for c in model.__table__.columns
                          if not (table_name in _TAX_TABLES and c.name in _TAX_TABLE_EXCLUDED_COLUMNS)]
            else:
                columns = list(rows[0].keys()) if rows else []

            csv_buffer = io.StringIO()
            writer = csv.DictWriter(csv_buffer, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(_escape_formula_cells(row))
            zf.writestr(f"{table_name}.csv", csv_buffer.getvalue())
    return buffer.getvalue()
