"""מנוע התראות - מחשב התאמות על קריאה, לא שומר התראות ב-DB.

ההחלטה הזו מכוונת: אין בפרויקט scheduler/cron, וטבלת התראות שנוצרת ע"י job
היא מקור אמת שני לתאריכי דדליין - שבהכרח יסטה מהמנוע. לכן כל תאריך כאן נקרא
מ-DeterministicESOPEngine ולעולם לא מחושב מחדש. מה שכן נשמר הוא רק מצב המשתמש
(העדפות + סגירות), כי אותו אי אפשר לגזור.

המחיר: "לא נקרא" משמעו "לא נסגר ע"י המשתמש" - אין היסטוריה, והתראה שחדלה
להתאים (הדדליין עבר) פשוט נעלמת מהפיד.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from sqlalchemy.orm import Session

from backend.app.types import system_today_utc
from backend.app.models import (
    Employee, Grant, OptionPool, ExerciseRequest, ExerciseRequestStatus,
    NotificationPreference, NotificationDismissal, NOTIFICATION_DEFAULT_LEAD_DAYS,
)
from backend.app.services.engine import DeterministicESOPEngine

# הפיד מוגבל כדי שדאטה ותיק (הרבה מענקים שהבשילו מזמן) לא יטביע את המשתמש
# במאות שורות. המונה בכל זאת מדווח את המספר האמיתי - אחרת "50" היה נראה כמו
# העובדה במקום כמו תקרה.
MAX_FEED_ITEMS = 50


@dataclass
class NotificationItem:
    key: str
    rule: str
    entity_type: str
    entity_id: str
    title: str
    detail: str
    trigger_date: Optional[date]
    severity: str  # "info" | "warning" | "critical"


@dataclass
class NotificationFeed:
    items: list = field(default_factory=list)
    # ישויות שהמנוע לא הצליח להעריך (למשל מענק בלי לוח הבשלה). מדווחות בנפרד
    # במקום להפיל את כל הפיד ל-500 - התראות הן מסך משני, ולא סביר שנתון חסר
    # במענק אחד יחסום את כל הרשימה של המשתמש.
    degraded_entities: list = field(default_factory=list)
    total: int = 0


def _days_between(target: date, today: date) -> int:
    return (target - today).days


def _vested(grant: Grant, on_date: date) -> float:
    """הבשלה עם עצירה ביום העזיבה - אותה נקודת כניסה שה-endpoints משתמשים בה.
    בלי ה-cutoff הכלל "אירוע הבשלה מתקרב" היה מבטיח לעובד שעזב אופציות נוספות
    שלא יבשילו לו לעולם."""
    cutoff = DeterministicESOPEngine.vesting_cutoff_date(grant.employee, on_date)
    return DeterministicESOPEngine.calculate_vested_options(grant, grant.vesting_schedule, cutoff)


def _severity_for(days_left: int) -> str:
    if days_left <= 7:
        return "critical"
    if days_left <= 30:
        return "warning"
    return "info"


def _effective_preferences(db: Session, user_id: str) -> dict:
    """ברירות מחדל מ-NOTIFICATION_DEFAULT_LEAD_DAYS, נדרסות ע"י שורות המשתמש.
    משתמש בלי אף שורה מקבל את כל הכללים דלוקים - התראות הן opt-out, לא opt-in."""
    prefs = {rule: {"enabled": True, "lead_days": days}
             for rule, days in NOTIFICATION_DEFAULT_LEAD_DAYS.items()}
    for row in db.query(NotificationPreference).filter(NotificationPreference.user_id == user_id).all():
        if row.rule in prefs:
            prefs[row.rule] = {"enabled": bool(row.enabled), "lead_days": int(row.lead_days)}
    return prefs


def _dismissed_keys(db: Session, user_id: str) -> set:
    return {r.notification_key for r in
            db.query(NotificationDismissal.notification_key)
              .filter(NotificationDismissal.user_id == user_id).all()}


def make_key(rule: str, entity_id: str, trigger_date: Optional[date]) -> str:
    """מפתח דטרמיניסטי. שינוי ב-trigger_date מחזיר התראה שנסגרה - וזה רצוי:
    דדליין שזז הוא אירוע חדש שהמשתמש לא ראה."""
    return f"{rule}|{entity_id}|{trigger_date.isoformat() if trigger_date else 'none'}"


# ===================================================================
# כללים - כל אחד מקבל grant/request ומחזיר NotificationItem או None.
# אף כלל לא מחשב תאריך בעצמו: הכל דרך DeterministicESOPEngine.
# ===================================================================

def _rule_vesting_event_near(grant: Grant, today: date, lead_days: int) -> Optional[NotificationItem]:
    schedule = grant.vesting_schedule
    if not schedule or not schedule.total_months:
        return None
    vested_now = _vested(grant, today)
    if vested_now >= grant.total_options:
        return None
    # מחפש את היום הקרוב שבו הכמות שהבשילה גדלה - סורק קדימה עד חלון ההתרעה.
    for offset in range(1, lead_days + 1):
        future = date.fromordinal(today.toordinal() + offset)
        if _vested(grant, future) > vested_now:
            return NotificationItem(
                key=make_key("VESTING_EVENT_NEAR", grant.grant_id, future),
                rule="VESTING_EVENT_NEAR", entity_type="Grant", entity_id=grant.grant_id,
                title="אירוע הבשלה מתקרב",
                detail=f"מענק {grant.grant_id}: אופציות נוספות יבשילו ב-{future} (בעוד {offset} ימים).",
                trigger_date=future, severity=_severity_for(offset),
            )
    return None


def _rule_trustee_holding_ending(grant: Grant, today: date, lead_days: int) -> Optional[NotificationItem]:
    is_met, end_date = DeterministicESOPEngine.check_trustee_holding_period(grant, today)
    if is_met or not grant.trustee_deposit_date or not end_date:
        return None
    days_left = _days_between(end_date, today)
    if days_left < 0 or days_left > lead_days:
        return None
    return NotificationItem(
        key=make_key("TRUSTEE_HOLDING_ENDING", grant.grant_id, end_date),
        rule="TRUSTEE_HOLDING_ENDING", entity_type="Grant", entity_id=grant.grant_id,
        title="חסימת הנאמנות עומדת להסתיים",
        detail=f"מענק {grant.grant_id}: תקופת החסימה (סעיף 102) מסתיימת ב-{end_date} (בעוד {days_left} ימים).",
        trigger_date=end_date, severity=_severity_for(days_left),
    )


def _rule_ptew_closing(grant: Grant, employee: Employee, today: date, lead_days: int) -> Optional[NotificationItem]:
    is_within, deadline = DeterministicESOPEngine.check_post_termination_exercise_window(grant, employee, today)
    # deadline=None משמעו שאין הגבלה בכלל (עובד פעיל) - אין על מה להתריע.
    if deadline is None or not is_within:
        return None
    days_left = _days_between(deadline, today)
    if days_left < 0 or days_left > lead_days:
        return None
    return NotificationItem(
        key=make_key("PTEW_CLOSING", grant.grant_id, deadline),
        rule="PTEW_CLOSING", entity_type="Grant", entity_id=grant.grant_id,
        title="חלון המימוש לאחר עזיבה נסגר",
        detail=f"מענק {grant.grant_id}: אפשר להגיש בקשת מימוש עד {deadline} (בעוד {days_left} ימים).",
        trigger_date=deadline, severity=_severity_for(days_left),
    )


def _rule_fully_vested_unexercised(grant: Grant, today: date, lead_days: int,
                                    has_request: bool) -> Optional[NotificationItem]:
    schedule = grant.vesting_schedule
    if not schedule or has_request:
        return None
    if _vested(grant, today) < grant.total_options:
        return None
    # מוצא את היום שבו הושלמה ההבשלה, כדי למדוד כמה זמן עבר מאז.
    lo, hi = schedule.start_date.toordinal(), today.toordinal()
    while lo < hi:
        mid = (lo + hi) // 2
        if _vested(grant, date.fromordinal(mid)) >= grant.total_options:
            hi = mid
        else:
            lo = mid + 1
    fully_vested_on = date.fromordinal(lo)
    days_since = _days_between(today, fully_vested_on)
    if days_since < lead_days:
        return None
    return NotificationItem(
        key=make_key("FULLY_VESTED_UNEXERCISED", grant.grant_id, fully_vested_on),
        rule="FULLY_VESTED_UNEXERCISED", entity_type="Grant", entity_id=grant.grant_id,
        title="מענק הבשיל במלואו ולא מומש",
        detail=f"מענק {grant.grant_id}: הבשיל במלואו ב-{fully_vested_on} ({days_since} ימים) ואין עליו בקשת מימוש.",
        trigger_date=fully_vested_on, severity="info",
    )


def _rule_request_pending_too_long(req: ExerciseRequest, today: date, lead_days: int) -> Optional[NotificationItem]:
    if req.status != ExerciseRequestStatus.PENDING or not req.requested_at:
        return None
    requested_on = req.requested_at.date() if isinstance(req.requested_at, datetime) else req.requested_at
    days_waiting = _days_between(today, requested_on)
    if days_waiting < lead_days:
        return None
    return NotificationItem(
        key=make_key("REQUEST_PENDING_TOO_LONG", req.request_id, requested_on),
        rule="REQUEST_PENDING_TOO_LONG", entity_type="ExerciseRequest", entity_id=req.request_id,
        title="בקשת מימוש ממתינה לאישור",
        detail=f"בקשה {req.request_id} על {req.options_requested:.0f} אופציות ממתינה {days_waiting} ימים.",
        trigger_date=requested_on, severity=_severity_for(max(0, 30 - days_waiting)),
    )


# ===================================================================
# איסוף - הסקופ נקבע כאן ורק כאן, לפי התפקיד.
# ===================================================================

def _collect(db: Session, grants: list, requests: list, prefs: dict, today: date) -> tuple:
    items, degraded = [], []

    grant_ids = [g.grant_id for g in grants]
    grants_with_requests = set()
    if grant_ids:
        grants_with_requests = {
            r.grant_id for r in db.query(ExerciseRequest.grant_id)
            .filter(ExerciseRequest.grant_id.in_(grant_ids)).all()
        }

    for grant in grants:
        # כל מענק מחושב בנפרד: מענק עם נתונים חסרים מסומן כ-degraded ולא מפיל את
        # כל הפיד ל-500. (קודם זה כיסה גם את קריסות 29/2 המכוונות, שתוקנו.)
        try:
            produced = []
            if prefs["VESTING_EVENT_NEAR"]["enabled"]:
                produced.append(_rule_vesting_event_near(
                    grant, today, prefs["VESTING_EVENT_NEAR"]["lead_days"]))
            if prefs["TRUSTEE_HOLDING_ENDING"]["enabled"]:
                produced.append(_rule_trustee_holding_ending(
                    grant, today, prefs["TRUSTEE_HOLDING_ENDING"]["lead_days"]))
            if prefs["PTEW_CLOSING"]["enabled"] and grant.employee is not None:
                produced.append(_rule_ptew_closing(
                    grant, grant.employee, today, prefs["PTEW_CLOSING"]["lead_days"]))
            if prefs["FULLY_VESTED_UNEXERCISED"]["enabled"]:
                produced.append(_rule_fully_vested_unexercised(
                    grant, today, prefs["FULLY_VESTED_UNEXERCISED"]["lead_days"],
                    grant.grant_id in grants_with_requests))
            items.extend(i for i in produced if i is not None)
        except Exception:
            degraded.append(grant.grant_id)

    if prefs["REQUEST_PENDING_TOO_LONG"]["enabled"]:
        for req in requests:
            try:
                it = _rule_request_pending_too_long(
                    req, today, prefs["REQUEST_PENDING_TOO_LONG"]["lead_days"])
                if it is not None:
                    items.append(it)
            except Exception:
                degraded.append(req.request_id)

    return items, degraded


def _finalize(items: list, degraded: list, dismissed: set) -> NotificationFeed:
    live = [i for i in items if i.key not in dismissed]
    # ממוין לפי דחיפות אמיתית - הדדליין הקרוב ביותר קודם.
    live.sort(key=lambda i: (i.trigger_date or date.max, i.rule))
    return NotificationFeed(items=live[:MAX_FEED_ITEMS],
                             degraded_entities=sorted(set(degraded)),
                             total=len(live))


def for_admin(db: Session, company_id: str, user_id: str, today: date = None) -> NotificationFeed:
    today = today or system_today_utc()
    prefs, dismissed = _effective_preferences(db, user_id), _dismissed_keys(db, user_id)

    # סקופ: רק פולים של החברה הזו -> רק המענקים שלהם. עובד עם company_id=NULL
    # לא מגיע לכאן בכלל, כי אין לו מענק בפול של החברה.
    pool_ids = [p.pool_id for p in
                db.query(OptionPool.pool_id).filter(OptionPool.company_id == company_id).all()]
    grants = db.query(Grant).filter(Grant.pool_id.in_(pool_ids)).all() if pool_ids else []
    grant_ids = [g.grant_id for g in grants]
    requests = (db.query(ExerciseRequest).filter(ExerciseRequest.grant_id.in_(grant_ids)).all()
                if grant_ids else [])

    items, degraded = _collect(db, grants, requests, prefs, today)
    return _finalize(items, degraded, dismissed)


def for_trustee(db: Session, trustee_id: str, user_id: str, today: date = None) -> NotificationFeed:
    today = today or system_today_utc()
    prefs, dismissed = _effective_preferences(db, user_id), _dismissed_keys(db, user_id)

    grants = db.query(Grant).filter(Grant.trustee_id == trustee_id).all()
    grant_ids = [g.grant_id for g in grants]
    requests = (db.query(ExerciseRequest).filter(ExerciseRequest.grant_id.in_(grant_ids)).all()
                if grant_ids else [])

    items, degraded = _collect(db, grants, requests, prefs, today)
    return _finalize(items, degraded, dismissed)


def for_employee(db: Session, employee_id: str, user_id: str, today: date = None) -> NotificationFeed:
    today = today or system_today_utc()
    prefs, dismissed = _effective_preferences(db, user_id), _dismissed_keys(db, user_id)

    grants = db.query(Grant).filter(Grant.employee_id == employee_id).all()
    requests = db.query(ExerciseRequest).filter(ExerciseRequest.employee_id == employee_id).all()

    items, degraded = _collect(db, grants, requests, prefs, today)
    return _finalize(items, degraded, dismissed)
