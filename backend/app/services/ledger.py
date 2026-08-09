"""Ledger מבוסס-אירועים (v0.6.0) - הוספת אירועים ושחזור מצב מהם.

עקרון העל: מצב עסקי (יתרת פול, סטטוס עובד, תאריך הפקדת נאמן, לוח הבשלה,
סטטוס בקשת מימוש) הוא *תוצר קיפול (fold)* של רצף אירועים append-only, לא
עמודה שמישהו עורך. הקובץ הזה הוא נקודת הכניסה היחידה לכתיבה וקריאה של אירועים -
שלב 3 (חיווט חמש נקודות המוטציה הקיימות) יקרא ל-``append_event`` במקום
לכתוב ישירות לעמודה.

*** מגבלה מוכרת, מקובלת בכוונה ***: ``_next_sequence_no`` מניח כותב יחיד
(SQLite, תהליך אחד). שני writers מקבילים שכותבים אירוע לאותה ישות בו-זמנית
עלולים להתנגש על אותו sequence_no ולקבל IntegrityError במקום להסתדר בשקט -
זו בחירה מכוונת (להיכשל בקול, לא לשקוט על דריפט), לא פער שנפתר בשלב הזה.
"""

import json
from datetime import date, datetime
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.types import utcnow
from backend.app.models import (
    LedgerEvent, LedgerOwnership, LEDGER_EVENT_TYPES, LEDGER_AGGREGATE_TYPES,
    LEDGER_SOURCE_LIVE,
)


# "לפני כל דבר אחר" - עוגן לאירועי בסיס שמייצגים תוצאה מצטברת ולא עובדה
# היסטורית בודדת (OptionPool: היתרה הנוכחית היא נטו של כמות לא-ידועה של הענקות
# עבר, ואין לה תאריך "נכון" יחיד). *** נמצא בפועל ***: backfill_ledger.py
# תיעד קודם effective_date=pool.created_at.date() - זמן יצירת השורה ב-DB, לא
# עובדה אמיתית - ומענק חי עם grant_date ישן מ-created_at (המצב הנפוץ) "הקדים"
# את הבסיס בקיפול, וה-POOL_ALLOCATED שלו התעלם בשקט (state עדיין None באותו
# רגע במיון). LEDGER_EPOCH מבטיח שהבסיס תמיד ראשון, בלי תלות בזמן יצירת השורה.
LEDGER_EPOCH = date.min


class UnknownLedgerEventType(ValueError):
    pass


class UnknownLedgerAggregateType(ValueError):
    pass


def _next_sequence_no(db: Session, aggregate_id: str) -> int:
    current_max = (
        db.query(func.max(LedgerEvent.sequence_no))
        .filter(LedgerEvent.aggregate_id == aggregate_id)
        .scalar()
    )
    return (current_max or 0) + 1


def append_event(
    db: Session,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict,
    effective_date: date,
    recorded_at: Optional[datetime] = None,
    actor_user_id: Optional[str] = None,
    source: str = LEDGER_SOURCE_LIVE,
    corrects_event_id: Optional[str] = None,
) -> LedgerEvent:
    """מוסיף אירוע יחיד. לא עושה commit - קורא לפונקציה הזו אחראי לעטוף אותה
    באותה טרנזקציה שגם כותבת את עדכון עמודת הפרויקציה (ראו הערת models.py)."""
    if event_type not in LEDGER_EVENT_TYPES:
        raise UnknownLedgerEventType(event_type)
    if aggregate_type not in LEDGER_AGGREGATE_TYPES:
        raise UnknownLedgerAggregateType(aggregate_type)

    event = LedgerEvent(
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=json.dumps(payload, default=str),
        effective_date=effective_date,
        recorded_at=recorded_at or utcnow(),
        actor_user_id=actor_user_id,
        sequence_no=_next_sequence_no(db, aggregate_id),
        source=source,
        corrects_event_id=corrects_event_id,
    )
    db.add(event)
    db.flush()  # שגיאת ייחוד על (aggregate_id, sequence_no) עולה כאן, לא בסוף הטרנזקציה
    return event


def record_ownership(
    db: Session,
    *,
    aggregate_id: str,
    aggregate_type: str,
    company_id: Optional[str] = None,
    trustee_id: Optional[str] = None,
    employee_id: Optional[str] = None,
) -> LedgerOwnership:
    """קובע את שורת הבעלות פעם אחת בלבד. אם כבר קיימת - מוחזרת כמו שהיא,
    immutable בהיקף v0.6.0 (ראו הערת LedgerOwnership ב-models.py)."""
    existing = db.get(LedgerOwnership, aggregate_id)
    if existing:
        return existing
    row = LedgerOwnership(
        aggregate_id=aggregate_id, aggregate_type=aggregate_type,
        company_id=company_id, trustee_id=trustee_id, employee_id=employee_id,
    )
    db.add(row)
    db.flush()
    return row


def events_for(
    db: Session,
    aggregate_id: str,
    *,
    as_of_effective_date: Optional[date] = None,
    as_of_knowledge_date: Optional[datetime] = None,
) -> list:
    """שולף את כל האירועים של ישות אחת, בסדר הקיפול הקנוני
    (effective_date, sequence_no), עם שני חתכי זמן עצמאיים ואופציונליים.

    as_of_effective_date="מה נכון בעולם עד לתאריך הזה" ו-as_of_knowledge_date=
    "מה המערכת ידעה עד לרגע הזה" - שתי שאלות אמיתיות ושונות, לכן שני פרמטרים
    נפרדים ולא אחד. שני הפרמטרים None => כל ההיסטוריה, כלומר "מה נכון עכשיו".
    """
    query = db.query(LedgerEvent).filter(LedgerEvent.aggregate_id == aggregate_id)
    if as_of_effective_date is not None:
        query = query.filter(LedgerEvent.effective_date <= as_of_effective_date)
    if as_of_knowledge_date is not None:
        query = query.filter(LedgerEvent.recorded_at <= as_of_knowledge_date)
    return query.order_by(LedgerEvent.effective_date, LedgerEvent.sequence_no).all()


def _parse_date(value: Optional[str]) -> Optional[date]:
    """JSON אין לו סוג תאריך - append_event כותב תאריך כ-ISO string
    (json.dumps(default=str)), ובלי הפענוח הזה project() היה מחזיר מחרוזת
    במקום date בכל שדה תאריך, שנראה זהה בעין אבל לא שווה ל-== מול עמודת ה-DB
    האמיתית (נתפס ע"י test_replay_equivalence_for_every_aggregate_type)."""
    if not value:
        return None
    return date.fromisoformat(value)


# ===================================================================
# קיפול (fold) - לכל סוג צובר יש פונקציה משלו. "ESTABLISHED" הוא אירוע בסיס
# (snapshot); כל שאר הסוגים הם דלתא שמצטברת מעליו. שלב 1 מייצר רק אירועי
# בסיס (מגיבוי) - אירועי הדלתא (POOL_ALLOCATED וכו') מחווטים בשלב 3.
# ===================================================================

def project_option_pool(events: list) -> Optional[dict]:
    state = None
    for e in events:
        p = json.loads(e.payload)
        if e.event_type == "POOL_BALANCE_ESTABLISHED":
            state = {"allocated_shares": p["allocated_shares"],
                     "unallocated_shares": p["unallocated_shares"],
                     "total_shares": p["total_shares"]}
        elif e.event_type == "POOL_ALLOCATED" and state is not None:
            state["allocated_shares"] += p["amount"]
            state["unallocated_shares"] -= p["amount"]
        elif e.event_type == "POOL_UNVEST_RETURNED" and state is not None:
            state["allocated_shares"] -= p["amount"]
            state["unallocated_shares"] += p["amount"]
    return state


def project_employee(events: list) -> Optional[dict]:
    state = None
    for e in events:
        p = json.loads(e.payload)
        if e.event_type == "EMPLOYEE_STATE_ESTABLISHED":
            state = {"status": p["status"], "termination_date": _parse_date(p.get("termination_date"))}
        elif e.event_type == "EMPLOYEE_STATUS_CHANGED" and state is not None:
            state["status"] = p["status"]
            state["termination_date"] = _parse_date(p.get("termination_date"))
    return state


def project_grant(events: list) -> Optional[dict]:
    """Grant.trustee_deposit_date הוא השדה היחיד על Grant שמשתנה אי-פעם;
    שאר השדות (total_options, exercise_price וכו') נקבעים ב-GRANT_CREATED
    ולעולם לא מוטטים במקום אחר."""
    state = None
    for e in events:
        p = json.loads(e.payload)
        if e.event_type == "GRANT_CREATED":
            state = {"trustee_deposit_date": _parse_date(p.get("trustee_deposit_date"))}
        elif e.event_type == "TRUSTEE_DEPOSIT_CONFIRMED" and state is not None:
            state["trustee_deposit_date"] = _parse_date(p["deposit_date"])
    return state


def project_vesting_schedule(events: list) -> Optional[dict]:
    state = None
    for e in events:
        p = json.loads(e.payload)
        if e.event_type == "VESTING_SCHEDULE_ESTABLISHED":
            state = {"start_date": _parse_date(p["start_date"]), "cliff_months": p["cliff_months"],
                     "total_months": p["total_months"], "paused_days_total": p["paused_days_total"]}
        elif e.event_type == "VESTING_PAUSE_RECORDED" and state is not None:
            state["paused_days_total"] += p["days"]
    return state


def project_exercise_request(events: list) -> Optional[dict]:
    state = None
    for e in events:
        p = json.loads(e.payload)
        if e.event_type == "EXERCISE_REQUEST_SUBMITTED":
            state = {"status": "PENDING", "options_requested": p["options_requested"]}
        elif e.event_type == "EXERCISE_REQUEST_DECIDED" and state is not None:
            state["status"] = p["status"]
    return state


PROJECTORS = {
    "OptionPool": project_option_pool,
    "Employee": project_employee,
    "Grant": project_grant,
    "VestingSchedule": project_vesting_schedule,
    "ExerciseRequest": project_exercise_request,
}


def project(
    db: Session,
    aggregate_type: str,
    aggregate_id: str,
    *,
    as_of_effective_date: Optional[date] = None,
    as_of_knowledge_date: Optional[datetime] = None,
) -> Optional[dict]:
    """נקודת הכניסה הגנרית: שולף אירועים ומקפל אותם, לפי סוג הצובר.
    מחזיר None אם אין בכלל אירועים (ישות שלא קיימת ב-ledger, למשל לפני
    שהגיבוי רץ - ראו R-060 ב-QA_TESTBOOK.md)."""
    if aggregate_type not in PROJECTORS:
        raise UnknownLedgerAggregateType(aggregate_type)
    events = events_for(db, aggregate_id, as_of_effective_date=as_of_effective_date,
                        as_of_knowledge_date=as_of_knowledge_date)
    return PROJECTORS[aggregate_type](events)
