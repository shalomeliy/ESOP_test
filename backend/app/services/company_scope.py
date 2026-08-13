"""Scope + table-registry משותפים לייצוא ולייבוא (v1.0.2, HANDOFF.md debt item 1).

עד כאן export.py ו-import_.py תיארו את אותן ~11 טבלאות "ליבה" (company_id/FK-
scoped) בשני מבנים עצמאיים: export.py::TABLE_REGISTRY {model, loader} מול
import_.py::_TABLE_SPECS {pk_field, scope_category, fk_checks} + עוד ארבעה
דיקטים נפרדים (_PK_COLUMN/_MODEL_BY_TABLE/_FORCE_COMPANY_ID_TABLES/
_NULLED_USER_COLUMNS/_AGGREGATE_TYPE_BY_TABLE). זו בדיוק הצורה שכבר גרמה לבאג
אמיתי: ShareClass/Shareholder/ShareIssuance נוספו ל-TABLE_REGISTRY של הייצוא
ונשכחו בהתחלה מ-_FORCE_COMPANY_ID_TABLES של הייבוא (v1.0.1) - bundle עם
company_id זר על שורת shareholders/share_issuances היה נכתב עם הערך מהקובץ,
לא נדרס לחברת היעד.

TableSpec אחד, TABLE_REGISTRY אחד - כל טבלה מוצהרת פעם אחת, שני הצדדים קוראים
מאותו מקום. companies/users/ledger_ownership/stock_prices_history נושאות
עמודת company_id בפועל אבל **לא** ב-TABLE_REGISTRY הזה - ראו SPECIAL_CASED_TABLES
למטה, כל אחת עם הסיבה שלה, ואינווריאנט קבוע ב-tests/test_project_invariants.py
שמוודא שאף טבלה חדשה עם עמודת company_id לא יכולה להישכח בשקט משני המקומות.
ledger_events/audit_log/notification_*/חבילות המס אין להן עמודת company_id
בכלל (dispatch לפי entity_type/user_id/natural key) - לא רלוונטיות לרישום הזה
כלל, ונשארות בקוד המיוחד שכבר קיים בשני הקבצים (export.py/import_.py).

הסדר בדיקט הזה טעון (load-bearing): זה בדיוק הסדר הטופולוגי ש-import_.py
חייב לשמור לכתיבה (share_classes לפני option_pools/shareholders,
share_classes+shareholders לפני share_issuances, employees/trustees לפני
grants, grants לפני vesting_schedules/documents/exercise_requests,
exercise_requests לפני exercise_tax_records) - v1.0.1 כבר תיקן פעם אחת סדר
שגוי כאן (share_classes אחרי option_pools). export.py לא תלוי בסדר הזה
לנכונות (הוא רק מייצא dict, לא כותב FK), אבל עכשיו יש רק סדר אחד לשמור.
"""

from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Set, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.app.models import (
    Document, DocumentAcknowledgmentWindowOverride, Employee, ExerciseRequest,
    ExerciseTaxRecord, Grant, OptionPool, ShareClass, ShareIssuance,
    Shareholder, Trustee, User, VestingSchedule,
)


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
    exercise_tax_record_ids: Set[str] = field(default_factory=set)
    share_class_ids: Set[str] = field(default_factory=set)
    shareholder_ids: Set[str] = field(default_factory=set)
    share_issuance_ids: Set[str] = field(default_factory=set)
    # v1.0.2 (debt item 2): רק כדי ש-export.py::_audit_log_in_scope יוכל למצוא
    # רשומות ביקורת על הישות הזו - אותה סיבה בדיוק שהוסיפה share_class_ids
    # וכו' ב-v1.0.1 (בלעדיה, יומן הביקורת היה חסר בשקט, לא דולף).
    document_acknowledgment_window_override_ids: Set[str] = field(default_factory=set)


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
    share_class_ids = _ids(db, ShareClass.share_class_id, ShareClass.company_id == company_id)
    shareholder_ids = _ids(db, Shareholder.shareholder_id, Shareholder.company_id == company_id)
    share_issuance_ids = _ids(db, ShareIssuance.share_issuance_id, ShareIssuance.company_id == company_id)
    document_acknowledgment_window_override_ids = _ids(
        db, DocumentAcknowledgmentWindowOverride.override_id,
        DocumentAcknowledgmentWindowOverride.company_id == company_id,
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
                        exercise_tax_record_ids=exercise_tax_record_ids,
                        share_class_ids=share_class_ids, shareholder_ids=shareholder_ids,
                        share_issuance_ids=share_issuance_ids,
                        document_acknowledgment_window_override_ids=document_acknowledgment_window_override_ids)


# ===================================================================
# Loaders גנריים (צד הייצוא בלבד): (db, scope) -> רשימת שורות ORM. כל אחד
# מסתמך רק על scope, לא על שאילתה עצמאית - כדי שלא יהיו שני מקורות אמת לאותו
# company_id. companies נשאר ב-export.py (שורש ה-scope, לא טבלת ליבה).
# ===================================================================

def _employees_in_scope(db: Session, scope: CompanyScope) -> list:
    return db.query(Employee).filter(Employee.company_id == scope.company_id).all()


def _option_pools_in_scope(db: Session, scope: CompanyScope) -> list:
    return db.query(OptionPool).filter(OptionPool.company_id == scope.company_id).all()


def _trustees_in_scope(db: Session, scope: CompanyScope) -> list:
    return db.query(Trustee).filter(Trustee.company_id == scope.company_id).all()


def _share_classes_in_scope(db: Session, scope: CompanyScope) -> list:
    return db.query(ShareClass).filter(ShareClass.company_id == scope.company_id).all()


def _shareholders_in_scope(db: Session, scope: CompanyScope) -> list:
    return db.query(Shareholder).filter(Shareholder.company_id == scope.company_id).all()


def _share_issuances_in_scope(db: Session, scope: CompanyScope) -> list:
    return db.query(ShareIssuance).filter(ShareIssuance.company_id == scope.company_id).all()


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


@dataclass
class TableSpec:
    """שדה אחד לכל צורך משני הצדדים - export.py צריך רק model+loader,
    import_.py צריך גם את השאר. model+loader הם היחידים בלי ברירת מחדל כי
    אלה נחוצים גם לחמש הטבלאות המיוחדות שexport.py מוסיף מעבר לרישום הזה
    (companies/ledger_events/audit_log/notification_*), שעבורן שאר השדות
    לא רלוונטיים ולא נקראים ע"י אף קורא."""
    model: type
    loader: Callable[[Session, CompanyScope], list]
    pk_field: Optional[str] = None            # import_.py בלבד
    scope_category: Optional[str] = None      # import_.py בלבד: attribute תואם על CompanyScope/batch_seen
    fk_checks: Tuple[Tuple[str, str], ...] = ()   # import_.py בלבד: (fk_field, referenced_scope_category)
    force_company_id: bool = False            # import_.py::_build_row בלבד
    nulled_user_columns: Tuple[str, ...] = () # import_.py::_build_row בלבד
    aggregate_type: Optional[str] = None      # import_.py::_record_ownership_for_new_row בלבד


# הסדר כאן טעון - ראו הערת המודול למעלה. share_classes לפני option_pools
# (share_class_id fk_check) ולפני shareholders/share_issuances; employees/
# trustees לפני grants; share_classes+shareholders לפני share_issuances;
# grants לפני vesting_schedules/documents/exercise_requests; exercise_requests
# לפני exercise_tax_records.
TABLE_REGISTRY: Dict[str, TableSpec] = {
    "share_classes": TableSpec(
        model=ShareClass, loader=_share_classes_in_scope,
        pk_field="share_class_id", scope_category="share_class_ids",
        force_company_id=True,
    ),
    "option_pools": TableSpec(
        model=OptionPool, loader=_option_pools_in_scope,
        pk_field="pool_id", scope_category="pool_ids",
        fk_checks=(("share_class_id", "share_class_ids"),),
        force_company_id=True, aggregate_type="OptionPool",
    ),
    "employees": TableSpec(
        model=Employee, loader=_employees_in_scope,
        pk_field="employee_id", scope_category="employee_ids",
        force_company_id=True, aggregate_type="Employee",
    ),
    "trustees": TableSpec(
        model=Trustee, loader=_trustees_in_scope,
        pk_field="trustee_id", scope_category="trustee_ids",
        force_company_id=True,
    ),
    "shareholders": TableSpec(
        model=Shareholder, loader=_shareholders_in_scope,
        pk_field="shareholder_id", scope_category="shareholder_ids",
        fk_checks=(("employee_id", "employee_ids"),),
        force_company_id=True,
    ),
    "share_issuances": TableSpec(
        model=ShareIssuance, loader=_share_issuances_in_scope,
        pk_field="share_issuance_id", scope_category="share_issuance_ids",
        fk_checks=(("shareholder_id", "shareholder_ids"), ("share_class_id", "share_class_ids")),
        force_company_id=True, aggregate_type="ShareIssuance",
    ),
    "grants": TableSpec(
        model=Grant, loader=_grants_in_scope,
        pk_field="grant_id", scope_category="grant_ids",
        fk_checks=(("pool_id", "pool_ids"), ("employee_id", "employee_ids"), ("trustee_id", "trustee_ids")),
        aggregate_type="Grant",
    ),
    "vesting_schedules": TableSpec(
        model=VestingSchedule, loader=_vesting_schedules_in_scope,
        pk_field="schedule_id", scope_category="schedule_ids",
        fk_checks=(("grant_id", "grant_ids"),),
        aggregate_type="VestingSchedule",
    ),
    "documents": TableSpec(
        model=Document, loader=_documents_in_scope,
        pk_field="document_id", scope_category="document_ids",
        fk_checks=(("grant_id", "grant_ids"), ("employee_id", "employee_ids"), ("trustee_id", "trustee_ids")),
        force_company_id=True,
        nulled_user_columns=("acknowledged_by_user_id", "created_by_user_id"),
    ),
    "exercise_requests": TableSpec(
        model=ExerciseRequest, loader=_exercise_requests_in_scope,
        pk_field="request_id", scope_category="request_ids",
        fk_checks=(("grant_id", "grant_ids"), ("employee_id", "employee_ids")),
        aggregate_type="ExerciseRequest",
        nulled_user_columns=("reviewed_by_user_id",),
    ),
    "exercise_tax_records": TableSpec(
        model=ExerciseTaxRecord, loader=_exercise_tax_records_in_scope,
        pk_field="record_id", scope_category="exercise_tax_record_ids",
        fk_checks=(("request_id", "request_ids"),),
    ),
}


# טבלאות עם עמודת company_id בפועל (models.py) שאינן ב-TABLE_REGISTRY למעלה -
# כל אחת בכוונה, לא נשכחה. tests/test_project_invariants.py אוכף שכל טבלה
# חדשה עם עמודת company_id תופיע כאן או ב-TABLE_REGISTRY - אין דרך שלישית
# להישאר בשקט מחוץ לשניהם.
SPECIAL_CASED_TABLES = {
    "companies": ("שורש ה-scope עצמו - לעולם לא נכתב ע\"י ייבוא; company_id "
                  "של המאשר קובע את היעד, לא שורה בקובץ (import_.py docstring)."),
    "users": ("לעולם לא מיוצאת/מיובאת (decision 1, import_.py) - כל FK אליה "
              "מאופס תמיד בכתיבה."),
    "ledger_ownership": ("לעולם לא מיובאת כטבלה - נבנית מחדש מ-record_ownership "
                         "על כל שורה חדשה (import_.py::_record_ownership_for_new_row)."),
    "stock_prices_history": ("מחוץ להיקף הייצוא/ייבוא כליל (export.py docstring: "
                             "recompute-on-import, לא נתוני לקוח)."),
    "document_acknowledgment_window_overrides": ("v1.0.2 - מיוצאת לצפייה/גיבוי "
        "(כמו companies) אבל לא מיובאת; שורה חדשה נוצרת רק דרך "
        "PUT /admin/company/acknowledgment-windows/{type}, שכבר אוכפת "
        "company_id=current_user.company_id בעצמה."),
    "saved_reports": ("v1.1.0 - קונפיגורציית דוח שמורה (סוג+פילטרים) של admin "
        "בודד, לא דאטה עסקי ליבתי כמו grants/cap table. קרובה מבחינה מושגית "
        "ל'נוחות עבודה אישית' - saved filter - כמו "
        "notification_preferences/notification_dismissals (שאין להן בכלל "
        "עמודת company_id ישירה, ולכן אינן ברשימה הזו כלל). לא אמורה לנדוד "
        "עם ייצוא/ייבוא מלא של חברה בין סביבות: owner_user_id מצביע על "
        "משתמש ספציפי ב-users, וה-users עצמה כבר SPECIAL_CASED (לעולם לא "
        "מיוצאת/מיובאת) - ייבוא saved_reports היה אומר או לאפס owner_user_id "
        "(ואז 'פרטי' הופך חסר-משמעות, ראו is_private) או להשאיר הפניה "
        "ל-user_id שלא קיים ביעד. ראו PLAN v1.1.0, סעיף 'מודל דאטה'."),
}
