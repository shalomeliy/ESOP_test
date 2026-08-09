"""נאמנות חותמות זמן בין כתיבה לקריאה (P6), v0.9.1.

כל בדיקה כאן נופלת בלי ``backend/app/types.py``. הן קיימות כי הכשל שהן מכסות
**אינו זורק חריגה** בקוד הישן: SQLite משמיט את ההיסט בשקט ומחזיר תשובה שגויה
על שאלה היסטורית. בדיקה שרק מוודאת "לא קרס" הייתה עוברת גם לפני התיקון.
"""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import StatementError

from backend.app import models
from backend.app.auth import (
    MAX_FAILED_LOGIN_ATTEMPTS,
    hash_password,
    register_failed_login,
)
from backend.app.services.ledger import append_event, events_for
from backend.app.types import ensure_utc, utcnow

JERUSALEM = timezone(timedelta(hours=3))


def _append(db, recorded_at):
    return append_event(
        db,
        event_type="POOL_BALANCE_ESTABLISHED",
        aggregate_type="OptionPool",
        aggregate_id="POOL-UTC-1",
        payload={"total": 1000},
        effective_date=date(2026, 1, 1),
        recorded_at=recorded_at,
    )


# ---------------------------------------------------------------------------
# הכשל המרכזי: חתך ידיעה עם היסט
# ---------------------------------------------------------------------------

def test_knowledge_cutoff_with_offset_excludes_an_event_recorded_later(db_session):
    """אירוע נרשם ב-10:00 UTC. חתך של 12:00+03:00 הוא 09:00 UTC - כלומר *לפני*
    האירוע, ולכן חייב להחזיר ריק.

    בקוד הישן ההיסט נמחק בשקט, החתך נקרא כ-12:00 ""UTC"", והאירוע הוחזר כאילו
    המערכת כבר ידעה עליו. זו התשובה השגויה והשקטה שכל הגרסה הזו קיימת בשבילה.
    """
    _append(db_session, datetime(2026, 8, 9, 10, 0, tzinfo=timezone.utc))
    db_session.flush()

    cutoff = datetime(2026, 8, 9, 12, 0, tzinfo=JERUSALEM)  # = 09:00 UTC
    assert events_for(db_session, "POOL-UTC-1", as_of_knowledge_date=cutoff) == []


def test_the_same_wall_clock_in_utc_does_include_it(db_session):
    """הצד השני של אותו מטבע - בלעדיו הבדיקה למעלה הייתה עוברת גם אם החתך
    פשוט לא מחזיר כלום אף פעם."""
    _append(db_session, datetime(2026, 8, 9, 10, 0, tzinfo=timezone.utc))
    db_session.flush()

    cutoff = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)  # אחרי האירוע
    assert len(events_for(db_session, "POOL-UTC-1", as_of_knowledge_date=cutoff)) == 1


# ---------------------------------------------------------------------------
# הטיפוס עצמו
# ---------------------------------------------------------------------------

def test_a_naive_timestamp_is_rejected_and_not_guessed(db_session):
    """naive בכתיבה נדחה. ניחוש כאן היה מחזיר את השגיאה השקטה דרך הדלת האחורית."""
    with pytest.raises((StatementError, ValueError)):
        _append(db_session, datetime(2026, 8, 9, 10, 0))
        db_session.flush()


def test_a_non_utc_timestamp_is_converted_and_not_truncated(db_session):
    """12:00+03:00 חייב לחזור כ-09:00 UTC - אותו רגע בזמן.

    זה בדיוק מה ש-``DateTime(timezone=True)`` *לא* עושה ב-SQLite: הוא היה
    שומר 12:00 ומחזיר 12:00, כלומר מזיז את האירוע בשלוש שעות.
    """
    _append(db_session, datetime(2026, 8, 9, 12, 0, tzinfo=JERUSALEM))
    db_session.flush()
    db_session.expire_all()

    (event,) = events_for(db_session, "POOL-UTC-1")
    assert event.recorded_at == datetime(2026, 8, 9, 9, 0, tzinfo=timezone.utc)


def test_reads_come_back_aware(db_session):
    """בלי זה, כל השוואה במעלה הזרם חוזרת להיות naive מול aware."""
    _append(db_session, utcnow())
    db_session.flush()
    db_session.expire_all()

    (event,) = events_for(db_session, "POOL-UTC-1")
    assert event.recorded_at.tzinfo is not None
    assert event.recorded_at.utcoffset() == timedelta(0)


def test_ensure_utc_reads_naive_client_input_as_utc():
    """הגבול היחיד שבו פירוש naive מותר - קלט חיצוני, במקום גלוי אחד."""
    assert ensure_utc(datetime(2026, 8, 9, 12, 0)) == datetime(
        2026, 8, 9, 12, 0, tzinfo=timezone.utc
    )
    assert ensure_utc(datetime(2026, 8, 9, 12, 0, tzinfo=JERUSALEM)) == datetime(
        2026, 8, 9, 9, 0, tzinfo=timezone.utc
    )
    assert ensure_utc(None) is None


# ---------------------------------------------------------------------------
# נעילת חשבון - הבקרה שנכשלה *פתוח*
# ---------------------------------------------------------------------------

def test_account_lockout_is_actually_persisted(db_session):
    """הבדיקה חייבת לשלוף מחדש מה-DB ולא להסתכל על האובייקט בזיכרון.

    ההשמה ל-``locked_until`` אינה זורקת; ה-``commit`` כן. rollback של אותה
    טרנזקציה מוחק גם את מונה הכשלונות שהועלה באותה פונקציה - כלומר החשבון
    לעולם לא ננעל, וניחושי סיסמה בלתי מוגבלים. בדיקה שבוחנת את האובייקט
    בזיכרון עוברת בדיוק בזמן שהבקרה מושבתת.
    """
    password_hash, salt = hash_password("irrelevant")
    user = models.User(
        user_id="U-LOCK-1",
        username="lock@test.example",
        password_hash=password_hash,
        password_salt=salt,
        role=models.UserRole.COMPANY_ADMIN,
        is_active=True,
        must_change_password=False,
        failed_login_attempts=0,
    )
    db_session.add(user)
    db_session.flush()

    for _ in range(MAX_FAILED_LOGIN_ATTEMPTS):
        register_failed_login(db_session, user)

    db_session.expire_all()
    stored = db_session.get(models.User, "U-LOCK-1")
    assert stored.failed_login_attempts == MAX_FAILED_LOGIN_ATTEMPTS
    assert stored.locked_until is not None, "החשבון לא ננעל - הבקרה נכשלה פתוח"
    assert stored.locked_until > utcnow()
