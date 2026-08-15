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
from datetime import date as _date, datetime as _datetime
from enum import Enum as _Enum
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from backend.app.models import (
    AuditLog, Company, DocumentAcknowledgmentWindowOverride, Employee,
    ExerciseRequest, ExerciseTaxRecord, Grant, IncomeTaxBracket, LedgerEvent,
    LedgerOwnership, NotificationDismissal, NotificationPreference,
    TaxRatesHistory, TaxRulePack,
)
from backend.app.services.company_scope import (
    CompanyScope, TableSpec, build_company_scope,
    TABLE_REGISTRY as _CORE_TABLE_REGISTRY,
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


# CompanyScope/build_company_scope עברו ל-company_scope.py (v1.0.2, HANDOFF.md
# debt item 1) - משותפים עכשיו עם import_.py סימטרית, לא רק מיובאים ממנו.

def _ids(db: Session, column, *filters) -> Set[str]:
    """שכפול מכוון של company_scope.py::_ids - שימוש פרטי יחיד כאן
    (estimate_export_row_count), לא שווה חשיפה חוצת-מודולים בשביל שורה אחת."""
    return {row[0] for row in db.query(column).filter(*filters).all()}


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
        + len(scope.share_class_ids) + len(scope.shareholder_ids) + len(scope.share_issuance_ids)
        + len(scope.document_acknowledgment_window_override_ids)
    )

    if scope.request_ids:
        count += db.query(func.count(ExerciseTaxRecord.record_id)).filter(
            ExerciseTaxRecord.request_id.in_(scope.request_ids)).scalar()

    # JOIN ולא IN(...) - אותו תיקון כמו ב-_ledger_events_in_scope למטה (v1.1.1
    # פריט ב); זו השאילתה שמזינה את assert_export_within_size_limit, כלומר דווקא
    # החברה הגדולה שבשבילה המגבלה קיימת היא זו שהייתה שולחת הכי הרבה משתני bind.
    count += db.query(func.count(LedgerEvent.event_id)).join(
        LedgerOwnership, LedgerOwnership.aggregate_id == LedgerEvent.aggregate_id).filter(
        LedgerOwnership.company_id == company_id).scalar()

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


# employees/option_pools/trustees/share_classes/shareholders/share_issuances/
# grants/vesting_schedules/documents/exercise_requests/exercise_tax_records
# loaders עברו ל-company_scope.py (v1.0.2, HANDOFF.md debt item 1) - הן חלק
# מ-TABLE_REGISTRY המאוחד עכשיו, לא הגדרות מקומיות כאן.

def _document_acknowledgment_window_overrides_in_scope(db: Session, scope: CompanyScope) -> list:
    """v1.0.2 (debt item 2): מיוצאת לצפייה/גיבוי, אותה קטגוריה בדיוק כמו
    companies עצמה - לא ב-company_scope.TABLE_REGISTRY (לא מיובאת). שורה
    חדשה נוצרת תמיד דרך PUT /admin/company/acknowledgment-windows/{type},
    שכבר אוכף company_id=current_user.company_id בעצמו - אין צורך במנגנון
    הייבוא הגנרי (עם ה-fk_checks/force_company_id שלו) לטבלת הגדרות בגודל
    כזה, ובלי import מנעים גם את שאלת ה-UNIQUE(company_id, template_type)
    שהיה חייב להיחסם בזמן ייבוא batch (הטבלה היחידה עם UNIQUE נוסף על ה-PK)."""
    return (
        db.query(DocumentAcknowledgmentWindowOverride)
        .filter(DocumentAcknowledgmentWindowOverride.company_id == scope.company_id)
        .all()
    )

def _ledger_events_in_scope(db: Session, scope: CompanyScope) -> list:
    """LedgerEvent אין לו עמודת company_id - ההיקף נקבע דרך LedgerOwnership,
    בדיוק כמו ledger.py::_assert_ledger_ownership. סדר לפי (aggregate_id,
    sequence_no) ולא recorded_at: זה סדר הקיפול הקנוני (models.py, אותה הערה
    על uq_ledger_events_aggregate_seq), וגם מה שהופך CSV/JSON לדטרמיניסטי בין
    שתי הרצות ייצוא זהות.

    JOIN ולא IN(...) - אותו תיקון בדיוק כמו reports.py::_ledger_movements_in_scope
    (v1.1.1 פריט ב), ובמכוון בשני המקומות: ה-docstring שם מצהיר שהוא "מראה" של
    הפונקציה הזו, ותיקון צד אחד היה הופך את ההצהרה לשקרית. כאן זה אף חשוב יותר -
    אין פילטר תאריכים, כלומר נטענים *כל* האירועים של החברה."""
    return (
        db.query(LedgerEvent)
        .join(LedgerOwnership, LedgerOwnership.aggregate_id == LedgerEvent.aggregate_id)
        .filter(LedgerOwnership.company_id == scope.company_id)
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
        # v1.0.1: cap_table.py כותב audit rows לשלושת אלה מ-v1.0.0 - בלעדי
        # השורות האלה כאן, ייצוא היה משמיט את יומן הביקורת של החברה על
        # הפעולות שלה בטבלת ההון בשקט (חסר, לא דליפה).
        "ShareClass": scope.share_class_ids,
        "Shareholder": scope.shareholder_ids,
        "ShareIssuance": scope.share_issuance_ids,
        # v1.0.2: אותה סיבה בדיוק - company.py כותב audit rows על override של
        # חלון אישור פר-סוג-מסמך.
        "DocumentAcknowledgmentWindowOverride": scope.document_acknowledgment_window_override_ids,
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


# רישום הטבלאות המלא לצורך ייצוא: 11 טבלאות הליבה מ-company_scope.py (משותפות
# עם import_.py, v1.0.2 - HANDOFF.md debt item 1) + 6 טבלאות שנשארות מיוחדות
# לצד הייצוא בלבד: companies (שורש ה-scope, לא טבלת ליבה); ledger_events/
# audit_log/notification_preferences/notification_dismissals (אין להן עמודת
# company_id בכלל - dispatch עצמאי לא-גנרי, ראו ה-loaders למעלה); ו-
# document_acknowledgment_window_overrides (v1.0.2 debt item 2 - יש לה עמודת
# company_id, אבל מיוצאת-בלבד/לא-מיובאת בכוונה, ראו הלוודר שלה למעלה).
# LedgerOwnership/User/UserSession/StockPricesHistory לא ברשימה בכלל (לא
# מיוצאות ולא מיובאות) - שלוש ההחלטות המפורשות בתכנון (recompute-on-import,
# לא נתוני לקוח, לא נדרש כש-gain כבר נשמר על ExerciseTaxRecord) - ראו גם
# company_scope.SPECIAL_CASED_TABLES.
TABLE_REGISTRY: Dict[str, TableSpec] = {
    "companies": TableSpec(Company, _companies_in_scope),
    **_CORE_TABLE_REGISTRY,
    "ledger_events": TableSpec(LedgerEvent, _ledger_events_in_scope),
    "audit_log": TableSpec(AuditLog, _audit_log_in_scope),
    "notification_preferences": TableSpec(NotificationPreference, _notification_preferences_in_scope),
    "notification_dismissals": TableSpec(NotificationDismissal, _notification_dismissals_in_scope),
    "document_acknowledgment_window_overrides": TableSpec(
        DocumentAcknowledgmentWindowOverride, _document_acknowledgment_window_overrides_in_scope,
    ),
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
