"""דוחות, ייצוא ו-BI (v1.1.0) - שבעה דוחות + דשבורד, מחושבים בזמן קריאה בלבד.

**אין persist של תוצאת דוח** - בדיוק כמו compute_cap_table_snapshot/NotificationFeed.
מה שכן נשמר (models.py::SavedReport) הוא רק *קונפיגורציית* דוח (סוג+פילטרים) שאדמין
ביקש לשמור - לא התוצאה עצמה, כדי שדוח שמור לעולם לא יציג נתונים מיושנים.

**כל שאילתה כאן נבנית מתוך CompanyScope בלבד** (build_company_scope, לרוב דרך
company_scope.TABLE_REGISTRY[...].loader הקיים) - אף שאילתה לא מסננת ישירות לפי
company_id על Grant/VestingSchedule/ExerciseRequest/ExerciseTaxRecord/AuditLog
(לאף אחת מהן אין עמודת company_id בכלל). זו בדיוק מחלקת הבאג שכבר יצרה IDOR
אמיתי ב-create_shareholder (v1.0.0) - שימוש חוזר ב-loaders הקיימים במקום לכתוב
שאילתת scope עצמאית מבטיח שאין שני מקורות אמת לאותה לוגיקת היקף.

**ReportResult אחיד לכל שבעת הדוחות** - columns (סדר עמודות דטרמיניסטי, כל שורה
ב-rows חייבת לכלול את כל המפתחות האלה, אף פעם לא חלקי) + rows (רשימת dict שטוחים,
המקור היחיד גם ל-JSON וגם ל-CSV וגם ל-PDF - אין שלוש צורות נתונים נפרדות לאותו
דוח) + summary (dict חופשי, JSON בלבד - לא מוצג ב-CSV/PDF) + disclosures (רשימת
מחרוזות שחובה להציג, למשל תיוג is_estimate בדוח #5 או אזהרת "לא נמדד" בדוח #7).
"""

import csv
import io
from dataclasses import dataclass, field
from bisect import bisect_right
from datetime import date
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from backend.app.models import (
    AuditLog, ExerciseRequestStatus, LEDGER_AGGREGATE_TYPES, LedgerEvent,
    LedgerOwnership, StockPricesHistory,
)
from backend.app.services.company_scope import CompanyScope, TABLE_REGISTRY as _CORE_TABLE_REGISTRY
from backend.app.services.documents import _grant_type_value, render_tabular_pdf
from backend.app.services.engine import (
    CLAMP_BACK, DeterministicESOPEngine, shift_months,
)
from backend.app.services.export import _escape_formula_cells
from backend.app.services import notifications as _notifications
from backend.app.types import business_date_of, business_today


@dataclass
class ReportResult:
    columns: List[str]
    rows: List[dict]
    summary: dict
    disclosures: List[str] = field(default_factory=list)


# כותרות ל-PDF - מקור אמת יחיד, גם ל-api/reports.py (שם קובע audit entity_id/
# file name) וגם לכאן. חייב להיות תואם 1:1 ל-SAVED_REPORT_TYPES (models.py).
REPORT_TITLES = {
    "POOL_STATUS": "Option Pool Status",
    "TRUSTEE_EXPOSURE": "Exposure by Trustee",
    "DEADLINE_RISK": "Employees at Deadline Risk",
    "EXERCISE_ACTIVITY": "Exercise Activity",
    "COMPENSATION_EXPENSE": "Estimated Compensation Expense (Non-GAAP)",
    "MOVEMENT": "Periodic Movement Report",
    "ASC718_READINESS": "ASC 718 Readiness Checklist",
}


def _loader(table_name: str):
    return _CORE_TABLE_REGISTRY[table_name].loader


# ===================================================================
# 1. סטטוס פולים
# ===================================================================

def build_pool_status(db: Session, scope: CompanyScope) -> ReportResult:
    pools = _loader("option_pools")(db, scope)
    columns = ["pool_id", "share_class_id", "total_shares", "allocated_shares", "unallocated_shares"]
    rows = [{
        "pool_id": p.pool_id, "share_class_id": p.share_class_id,
        "total_shares": p.total_shares, "allocated_shares": p.allocated_shares,
        "unallocated_shares": p.unallocated_shares,
    } for p in pools]
    summary = {
        "pool_count": len(pools),
        "total_shares": sum(p.total_shares for p in pools),
        "total_allocated": sum(p.allocated_shares for p in pools),
        "total_unallocated": sum(p.unallocated_shares for p in pools),
    }
    return ReportResult(columns=columns, rows=rows, summary=summary)


# ===================================================================
# 2. חשיפה לפי נאמן
# ===================================================================

def build_trustee_exposure(db: Session, scope: CompanyScope) -> ReportResult:
    """אותה צורת שאילתה בדיוק כמו notifications.py::for_trustee (מענקים לפי
    trustee_id, בקשות מימוש לפי grant_id), אבל על *כל* הנאמנים בהיקף החברה
    בבת אחת, לא נאמן יחיד. estimated_value נשאר None בכוונה - שדה שמור לגרסה
    עתידית (v1.4.0, הערכות שווי), לא מומצא כאן."""
    trustees = _loader("trustees")(db, scope)
    grants = _loader("grants")(db, scope)
    requests = _loader("exercise_requests")(db, scope)
    employees_by_id = {e.employee_id: e for e in _loader("employees")(db, scope)}
    today = business_today()

    grants_by_trustee: Dict[str, list] = {}
    for g in grants:
        if g.trustee_id:
            grants_by_trustee.setdefault(g.trustee_id, []).append(g)

    approved_by_grant: Dict[str, float] = {}
    for r in requests:
        if r.status == ExerciseRequestStatus.APPROVED:
            approved_by_grant[r.grant_id] = approved_by_grant.get(r.grant_id, 0.0) + r.options_requested

    columns = ["trustee_id", "name", "registration_number", "employee_count", "grant_count",
               "total_options", "vested_options", "unvested_options", "exercised_options",
               "unexercised_vested_options", "estimated_value"]
    rows = []
    degraded: List[str] = []
    for t in trustees:
        t_grants = grants_by_trustee.get(t.trustee_id, [])
        employee_ids = set()
        total_options = vested_total = exercised_total = 0.0
        for g in t_grants:
            employee_ids.add(g.employee_id)
            total_options += g.total_options
            exercised_total += approved_by_grant.get(g.grant_id, 0.0)
            if not g.vesting_schedule:
                # אין לוח הבשלה - "לא הבשיל כלום" ו"לא ידוע" הם שני מצבים שונים
                # (engine.py::MissingVestingScheduleError) - מסומן ומדולג, לא נספר כ-0.
                degraded.append(g.grant_id)
                continue
            employee = employees_by_id.get(g.employee_id)
            cutoff = DeterministicESOPEngine.vesting_cutoff_date(employee, today)
            vested_total += DeterministicESOPEngine.calculate_vested_options(g, g.vesting_schedule, cutoff)

        unvested_total = max(0.0, total_options - vested_total)
        unexercised_vested = max(0.0, vested_total - exercised_total)
        rows.append({
            "trustee_id": t.trustee_id, "name": t.name, "registration_number": t.registration_number,
            "employee_count": len(employee_ids), "grant_count": len(t_grants),
            "total_options": total_options, "vested_options": round(vested_total, 2),
            "unvested_options": round(unvested_total, 2), "exercised_options": exercised_total,
            "unexercised_vested_options": round(unexercised_vested, 2),
            "estimated_value": None,
        })

    summary = {"trustee_count": len(trustees), "degraded_grant_ids": sorted(set(degraded))}
    disclosures = [
        "estimated_value is a reserved field for a future version (blocked on v1.4.0 valuations) "
        "and is always null in this version - no dollar value is computed or implied here.",
    ]
    return ReportResult(columns=columns, rows=rows, summary=summary, disclosures=disclosures)


# ===================================================================
# 3. עובדים בסיכון דדליין
# ===================================================================

def build_deadline_risk(db: Session, scope: CompanyScope, company_id: str, user_id: str) -> ReportResult:
    """קורא ל-notifications.py::for_admin ישירות ומקבץ מחדש את הפריטים לפי
    עובד/מענק - **לא** משחזר אף אחד מחמשת כללי הדדליין עצמם (דרישה מפורשת של
    reporting-engineer). מסונן לפי העדפות/סגירות ההתראה *של האדמין המבקש* -
    אותה סמנטיקה בדיוק כמו for_admin עצמה, לא באג."""
    feed = _notifications.for_admin(db, company_id, user_id)
    grants_by_id = {g.grant_id: g for g in _loader("grants")(db, scope)}
    requests_by_id = {r.request_id: r for r in _loader("exercise_requests")(db, scope)}
    employees_by_id = {e.employee_id: e for e in _loader("employees")(db, scope)}

    def _employee_id_for(item) -> Optional[str]:
        if item.entity_type == "Grant":
            g = grants_by_id.get(item.entity_id)
            return g.employee_id if g else None
        if item.entity_type == "ExerciseRequest":
            r = requests_by_id.get(item.entity_id)
            return r.employee_id if r else None
        return None

    columns = ["employee_id", "employee_name", "rule", "entity_type", "entity_id",
               "title", "detail", "trigger_date", "severity"]
    rows = []
    for item in feed.items:
        employee_id = _employee_id_for(item)
        employee = employees_by_id.get(employee_id) if employee_id else None
        employee_name = f"{employee.first_name} {employee.last_name}" if employee else None
        rows.append({
            "employee_id": employee_id, "employee_name": employee_name, "rule": item.rule,
            "entity_type": item.entity_type, "entity_id": item.entity_id, "title": item.title,
            "detail": item.detail, "trigger_date": item.trigger_date, "severity": item.severity,
        })

    by_employee: Dict[str, list] = {}
    for row in rows:
        by_employee.setdefault(row["employee_id"] or "UNASSIGNED", []).append(row)

    summary = {
        "total_open_items": feed.total, "shown": len(feed.items),
        "degraded_entities": feed.degraded_entities, "by_employee": by_employee,
    }
    disclosures = [
        "Deadline rules are computed by services/notifications.py and are not reimplemented here; "
        "the feed is filtered by the requesting admin's own notification preferences and dismissals, "
        "same as the notification center itself.",
    ]
    return ReportResult(columns=columns, rows=rows, summary=summary, disclosures=disclosures)


# ===================================================================
# 4. פעילות מימוש בתקופה
# ===================================================================

def _date_in_range(d: Optional[date], date_from: Optional[date], date_to: Optional[date]) -> bool:
    if d is None:
        return False
    if date_from and d < date_from:
        return False
    if date_to and d > date_to:
        return False
    return True


def build_exercise_activity(db: Session, scope: CompanyScope,
                            date_from: Optional[date], date_to: Optional[date]) -> ReportResult:
    """נכלל אם *requested_at או reviewed_at* נופל בטווח (לא רק אחד מהם) - בקשה
    שהוגשה לפני התקופה אך אושרה/נדחתה בתוכה היא עדיין "פעילות" של התקופה הזו.
    business_date_of ולא .date() - אותה סיבה בדיוק כמו notifications.py::
    _rule_request_pending_too_long (UtcDateTime גולמי מול יום עסקים)."""
    requests = _loader("exercise_requests")(db, scope)

    columns = ["request_id", "grant_id", "employee_id", "options_requested", "status",
               "requested_on", "reviewed_on", "reviewed_by_user_id"]
    rows = []
    for r in requests:
        requested_on = business_date_of(r.requested_at) if r.requested_at else None
        reviewed_on = business_date_of(r.reviewed_at) if r.reviewed_at else None
        if not (_date_in_range(requested_on, date_from, date_to)
                or _date_in_range(reviewed_on, date_from, date_to)):
            continue
        status_value = r.status.value if hasattr(r.status, "value") else r.status
        rows.append({
            "request_id": r.request_id, "grant_id": r.grant_id, "employee_id": r.employee_id,
            "options_requested": r.options_requested, "status": status_value,
            "requested_on": requested_on, "reviewed_on": reviewed_on,
            "reviewed_by_user_id": r.reviewed_by_user_id,
        })

    by_status: Dict[str, int] = {}
    for row in rows:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1

    summary = {"request_count": len(rows), "by_status": by_status,
              "date_from": date_from, "date_to": date_to}
    return ReportResult(columns=columns, rows=rows, summary=summary)


# ===================================================================
# 5. הוצאת שכר משוערת (שווי מהותי משוער, לא GAAP)
# ===================================================================

NO_PRICE_DATA = "NO_PRICE_DATA"
NO_PRECEDING_PRICE = "NO_PRECEDING_PRICE"
CURRENCY_MISMATCH = "CURRENCY_MISMATCH"

COMPENSATION_EXPENSE_BASIS = (
    "Estimated Intrinsic Value (Not GAAP Expense) - computed from the nearest preceding "
    "recorded stock price at grant date"
)


class _PriceLookup:
    """מחיר אחרון שנרשם *עד ועד בכלל* on_or_before - לעולם לא מחיר שנרשם אחרי
    תאריך ההענקה (look-ahead bias אסור, ראו PLAN v1.1.0), ולעולם לא נופל חזרה
    ל-exercise_price כתחליף FMV.

    האינווריאנט זהה לגמרי לקודמו (`_nearest_preceding_price`, שאילתה למענק);
    מה שהשתנה הוא *מתי* משלמים עליו - טעינה אחת לחברה במקום שאילתה פר-מענק
    (v1.1.1 פריט ב: 251 שאילתות על הדאטה החי, בשני קוראים).

    **הכרעת tie-break, שלא הייתה מוגדרת קודם:** ל-`ORDER BY price_date DESC
    LIMIT 1` אין שובר-שוויון, ולכן כששתי שורות חלקו price_date SQLite בחר
    ביניהן שרירותית - כולל fmv_price שונה. כאן הבחירה מוצהרת: השורה האחרונה
    לפי (price_date, price_id) עולה.

    *** מה שזה **אינו**: "המחיר שנרשם אחרון". *** price_id הוא
    `default=generate_uuid` (models.py), כלומר UUID4 אקראי - ה-price_id הגבוה
    אינו הרשומה החדשה יותר, הוא פשוט מחרוזת שממיינת גבוה. הרווח כאן הוא
    **דטרמיניזם בלבד**: אותה קלט תמיד מחזיר אותו מחיר, במקום בחירה שתלויה
    בתוכנית הביצוע של SQLite. אין בסכימה עמודת מועד-כתיבה, ולכן "אחרון גובר"
    אינו ניתן למימוש בלי עמודה חדשה - וזו הכרעה לגרסה, לא ל-patch."""

    def __init__(self, rows: List[StockPricesHistory]):
        self._rows = rows
        self._dates = [r.price_date for r in rows]

    @property
    def has_any(self) -> bool:
        """"לחברה אין שום מחיר רשום" (NO_PRICE_DATA) מול "יש מחירים אבל אף אחד
        לא קודם למענק הזה" (NO_PRECEDING_PRICE) - שתי סיבות החרגה נפרדות שאסור
        להן להתמוסס לאחת (ראו build_compensation_expense). נגזר מאותה טעינה,
        ולכן אין יותר שאילתת-קיום נפרדת לכל דוח."""
        return bool(self._rows)

    def at(self, on_or_before: date) -> Optional[StockPricesHistory]:
        # bisect_right ולא bisect_left: התאריך עצמו נכלל ("עד ועד בכלל").
        i = bisect_right(self._dates, on_or_before)
        return self._rows[i - 1] if i else None


def _price_lookup(db: Session, company_id: str) -> _PriceLookup:
    return _PriceLookup(
        db.query(StockPricesHistory)
        .filter(StockPricesHistory.company_id == company_id)
        .order_by(StockPricesHistory.price_date, StockPricesHistory.price_id)
        .all()
    )


def build_compensation_expense(db: Session, scope: CompanyScope) -> ReportResult:
    """*** דוח לא-GAAP, לא חישוב הוצאה חשבונאית אמיתי (ראו ASC718_READINESS
    לחלופה - checklist בלבד, בלי מספר) ***. שלושת נימוקי ההחרגה מוצגים בנפרד
    ולא מתמוססים ל-"N/A" גנרי אחד - ראו PLAN v1.1.0.

    אין מושג "מענק שבוטל/הופסק" בקודבייס הזה בכלל: ל-Grant אין עמודת status/
    cancelled (נבדק מול models.py) - services/cap_table.py, שהתכנון הפנה
    אליו כ"אותה לוגיקת הכללה/החרגה", גם הוא לא מסנן Grant לפי סטטוס עובד/מענק;
    ההחרגה היחידה שם היא "לפול אין היסטוריית ledger" (מסומן ב-warnings, לא
    מחריג בשקט). לכן: כל מענק בהיקף החברה נכלל כאן, כפוף רק לשלוש סיבות
    ההחרגה של התאמת המחיר למעלה - זו העברה נאמנה של "לעולם לא 0/None שקרי
    בשקט", לא החלטה חדשה שהומצאה כאן.
    """
    grants = _loader("grants")(db, scope)
    prices = _price_lookup(db, scope.company_id)

    columns = ["grant_id", "pool_id", "grant_type", "total_options", "exercise_price", "currency",
               "matched_price_date", "fmv_at_grant_date", "contribution", "exclusion_reason",
               "is_estimate", "basis"]
    rows = []
    exclusion_counts = {NO_PRICE_DATA: 0, NO_PRECEDING_PRICE: 0, CURRENCY_MISMATCH: 0}
    by_pool: Dict[str, float] = {}
    by_tax_track: Dict[str, float] = {}
    total_contribution = 0.0

    for g in grants:
        grant_type_value = _grant_type_value(g)
        row = {
            "grant_id": g.grant_id, "pool_id": g.pool_id, "grant_type": grant_type_value,
            "total_options": g.total_options, "exercise_price": g.exercise_price,
            "currency": g.currency, "matched_price_date": None, "fmv_at_grant_date": None,
            "contribution": None, "exclusion_reason": None,
            "is_estimate": True, "basis": COMPENSATION_EXPENSE_BASIS,
        }

        if not prices.has_any:
            row["exclusion_reason"] = NO_PRICE_DATA
            exclusion_counts[NO_PRICE_DATA] += 1
            rows.append(row)
            continue

        price_row = prices.at(g.grant_date)
        if price_row is None:
            row["exclusion_reason"] = NO_PRECEDING_PRICE
            exclusion_counts[NO_PRECEDING_PRICE] += 1
            rows.append(row)
            continue

        if (g.currency or "USD") != (price_row.currency or "USD"):
            row["exclusion_reason"] = CURRENCY_MISMATCH
            row["matched_price_date"] = price_row.price_date
            row["fmv_at_grant_date"] = price_row.fmv_price
            exclusion_counts[CURRENCY_MISMATCH] += 1
            rows.append(row)
            continue

        contribution = max(0.0, price_row.fmv_price - g.exercise_price) * g.total_options
        row["matched_price_date"] = price_row.price_date
        row["fmv_at_grant_date"] = price_row.fmv_price
        row["contribution"] = round(contribution, 2)
        rows.append(row)

        by_pool[g.pool_id] = by_pool.get(g.pool_id, 0.0) + contribution
        by_tax_track[grant_type_value] = by_tax_track.get(grant_type_value, 0.0) + contribution
        total_contribution += contribution

    summary = {
        "is_estimate": True, "basis": COMPENSATION_EXPENSE_BASIS,
        "total_contribution": round(total_contribution, 2),
        "by_pool": {k: round(v, 2) for k, v in by_pool.items()},
        "by_tax_track": {k: round(v, 2) for k, v in by_tax_track.items()},
        "exclusion_counts": exclusion_counts,
        "included_grant_count": len(grants) - sum(exclusion_counts.values()),
        "excluded_grant_count": sum(exclusion_counts.values()),
    }
    disclosures = [
        COMPENSATION_EXPENSE_BASIS,
        "Grant has no cancellation/termination status in this codebase - all grants in scope are "
        "included, subject only to the price-matching exclusion reasons above (NO_PRICE_DATA / "
        "NO_PRECEDING_PRICE / CURRENCY_MISMATCH), mirroring cap_table.py's rule of never silently "
        "treating missing data as zero.",
    ]
    return ReportResult(columns=columns, rows=rows, summary=summary, disclosures=disclosures)


# ===================================================================
# 6. דוח תנועה תקופתי
# ===================================================================

_AUDIT_ONLY_ENTITY_TYPES = ("Document", "ExerciseTaxRecord", "ShareClass", "Shareholder")


def _ledger_movements_in_scope(db: Session, scope: CompanyScope, date_a: date, date_b: date) -> list:
    """מראה export.py::_ledger_events_in_scope (היקף לפי LedgerOwnership, לא
    שאילתה עצמאית) - עם תוספת טווח על effective_date (Date טהור, לא UtcDateTime
    - אין כאן סוגיית אזור-זמן/ח1-ח2, אפשר לסנן ישירות ב-DB).

    JOIN ולא IN(...) מרשימת aggregate_ids בזיכרון (v1.1.1 פריט ב): הרשימה גדלה
    עם החברה (788 היום) וכל id נשלח כמשתנה bind נפרד. אין שכפול שורות כי
    aggregate_id הוא ה-PK של LedgerOwnership, ולכן גם חוזה המיון למטה נשמר -
    הוא על עמודות LedgerEvent ולא נוגע ב-join."""
    return (
        db.query(LedgerEvent)
        .join(LedgerOwnership, LedgerOwnership.aggregate_id == LedgerEvent.aggregate_id)
        .filter(LedgerOwnership.company_id == scope.company_id,
               LedgerEvent.effective_date >= date_a, LedgerEvent.effective_date <= date_b)
        .order_by(LedgerEvent.aggregate_id, LedgerEvent.sequence_no)
        .all()
    )


def _audit_only_movements_in_scope(db: Session, scope: CompanyScope) -> list:
    """מראה export.py::_audit_log_in_scope, מוגבל לארבע הישויות שאין להן כיסוי
    ledger בכלל (Document/ExerciseTaxRecord/ShareClass/Shareholder). טווח
    התאריכים מסונן אחרי הטעינה (business_date_of), לא ב-BETWEEN גולמי על
    occurred_at (UtcDateTime) - אותו לקח בדיוק כמו business_date_of בשאר
    הקודבייס: השוואת timestamp גולמי מול תאריך קלנדרי היא ההנחה ש-ח1/ח2 תיקנו."""
    entity_ids_by_type = {
        "Document": scope.document_ids,
        "ExerciseTaxRecord": scope.exercise_tax_record_ids,
        "ShareClass": scope.share_class_ids,
        "Shareholder": scope.shareholder_ids,
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


def build_movement(db: Session, scope: CompanyScope, date_a: date, date_b: date) -> ReportResult:
    ledger_rows = _ledger_movements_in_scope(db, scope, date_a, date_b)
    audit_rows = _audit_only_movements_in_scope(db, scope)

    columns = ["source", "aggregate_or_entity_type", "id", "event_or_action", "effective_or_occurred_date"]
    rows = []
    for e in ledger_rows:
        rows.append({
            "source": "LEDGER", "aggregate_or_entity_type": e.aggregate_type, "id": e.aggregate_id,
            "event_or_action": e.event_type, "effective_or_occurred_date": e.effective_date,
        })
    audit_movement_count = 0
    for a in audit_rows:
        occurred_on = business_date_of(a.occurred_at)
        if not (date_a <= occurred_on <= date_b):
            continue
        audit_movement_count += 1
        rows.append({
            "source": "AUDIT_LOG", "aggregate_or_entity_type": a.entity_type, "id": a.entity_id,
            "event_or_action": a.action, "effective_or_occurred_date": occurred_on,
        })

    trustee_disclosure = (
        "Trustee changes are not tracked by the ledger or the audit log in this version - no "
        "trustee movement data is available for any period (not an empty result, an untracked one)."
    )
    summary = {
        "date_from": date_a, "date_to": date_b,
        "ledger_covered_types": sorted(LEDGER_AGGREGATE_TYPES),
        "audit_only_types": list(_AUDIT_ONLY_ENTITY_TYPES),
        "ledger_event_count": len(ledger_rows),
        "audit_only_event_count": audit_movement_count,
        "trustees": {"tracked": False, "message": trustee_disclosure},
    }
    return ReportResult(columns=columns, rows=rows, summary=summary, disclosures=[trustee_disclosure])


# ===================================================================
# 7. רשימת מוכנות ASC 718 (checklist בלבד - בלי אף מספר כספי)
# ===================================================================

def build_asc718_readiness(db: Session, scope: CompanyScope) -> ReportResult:
    grants = _loader("grants")(db, scope)
    prices = _price_lookup(db, scope.company_id)

    columns = ["grant_id", "pool_id", "has_vesting_schedule", "has_preceding_stock_price",
               "has_exercise_price_recorded"]
    rows = []
    for g in grants:
        has_price = prices.has_any and prices.at(g.grant_date) is not None
        rows.append({
            "grant_id": g.grant_id, "pool_id": g.pool_id,
            "has_vesting_schedule": g.vesting_schedule is not None,
            "has_preceding_stock_price": has_price,
            "has_exercise_price_recorded": g.exercise_price is not None,
        })

    ready_count = sum(
        1 for r in rows
        if r["has_vesting_schedule"] and r["has_preceding_stock_price"] and r["has_exercise_price_recorded"]
    )
    summary = {"grant_count": len(rows), "fully_ready_count": ready_count}
    disclosures = [
        "This is a readiness checklist only - no dollar figure is computed or implied anywhere in "
        "this report (hard compliance constraint, not a style choice). Amortization method and "
        "forfeiture assumptions are unmodeled and unverified in this codebase; 'ready' here means "
        "only that the three input flags above are present.",
    ]
    return ReportResult(columns=columns, rows=rows, summary=summary, disclosures=disclosures)


# ===================================================================
# דשבורד - JSON בלבד (אין CSV/PDF, ראו api/reports.py)
# ===================================================================

# אופק קבוע וסופי לעקומת ההבשלה הצפויה - "מהיום קדימה" הוא בלתי-חסום עקרונית;
# 36 חודש (3 שנים) הוא גבול מפורש כדי שהחישוב יישאר חסום וודאי (O(מענקים ×
# נקודות)), לא "הערך הנכון" היחיד - ניתן להרחבה בגרסה עתידית עם פרמטר מפורש.
DASHBOARD_VESTING_HORIZON_MONTHS = 36


def _monthly_grid(start: date, horizon_months: int) -> List[date]:
    """רשת של 1-בחודש, מהחודש הנוכחי ועד horizon_months קדימה (כולל). עוגן על
    היום הראשון בחודש - לעולם לא פוגע ביום חסר (29/2 וכו', ראו shift_months)."""
    anchor = date(start.year, start.month, 1)
    return [shift_months(anchor, i, CLAMP_BACK) for i in range(horizon_months + 1)]


def build_dashboard(db: Session, scope: CompanyScope, today: Optional[date] = None) -> dict:
    today = today or business_today()
    grants = _loader("grants")(db, scope)
    employees_by_id = {e.employee_id: e for e in _loader("employees")(db, scope)}

    counts: Dict[str, int] = {}
    for g in grants:
        key = _grant_type_value(g)
        counts[key] = counts.get(key, 0) + 1
    total = len(grants)
    tax_track_breakdown = [
        {"grant_type": k, "count": v, "pct_of_total": round(100.0 * v / total, 2) if total else 0.0}
        for k, v in sorted(counts.items())
    ]

    degraded: List[str] = []
    points = []
    for point_date in _monthly_grid(today, DASHBOARD_VESTING_HORIZON_MONTHS):
        cumulative = 0.0
        for g in grants:
            if not g.vesting_schedule:
                if g.grant_id not in degraded:
                    degraded.append(g.grant_id)
                continue
            employee = employees_by_id.get(g.employee_id)
            cutoff = DeterministicESOPEngine.vesting_cutoff_date(employee, point_date)
            cumulative += DeterministicESOPEngine.calculate_vested_options(g, g.vesting_schedule, cutoff)
        points.append({"as_of": point_date, "cumulative_vested": round(cumulative, 2)})

    return {
        "as_of": today,
        "total_grants_in_scope": total,
        "tax_track_breakdown": tax_track_breakdown,
        "forward_vesting_curve": points,
        "vesting_curve_horizon_months": DASHBOARD_VESTING_HORIZON_MONTHS,
        "degraded_grant_ids": sorted(set(degraded)),
    }


# ===================================================================
# רינדור CSV/PDF - משותף לכל שבעת הדוחות (לא לדשבורד, ראו למעלה).
# ===================================================================

def rows_to_csv_bytes(columns: List[str], rows: List[dict]) -> bytes:
    """_escape_formula_cells מיובא כפי שהוא מ-export.py, לא משוכפל - אותה
    הגנה מפני הזרקת נוסחת CSV חלה כאן על שדות טקסט חופשי (שמות עובדים וכו')."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore", restval="")
    writer.writeheader()
    for row in rows:
        writer.writerow(_escape_formula_cells(row))
    return buffer.getvalue().encode("utf-8")


def _stringify_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def rows_to_pdf_bytes(title: str, columns: List[str], rows: List[dict], disclosures: List[str]) -> bytes:
    table_rows = [[_stringify_cell(row.get(c)) for c in columns] for row in rows]
    return render_tabular_pdf(title, columns, table_rows, disclosures=disclosures)
