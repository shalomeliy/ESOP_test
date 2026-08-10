"""השעון העסקי מול UTC - הרגרסיה ח1/ח2 של v0.9.1 שלב א.

שלב א העביר גבולות מזכים ו-``effective_date`` מ-``date.today()`` ל-UTC. התיקון
הזה היה נכון בכיוונו (תאריך המארח אינו דטרמיניסטי) אבל שגוי ביעדו: ישראל
*לפני* UTC, ולכן בין 00:00 ל-03:00 בירושלים תאריך ה-UTC הוא אתמול.

הפער בין הבדיקות כאן לבין ``test_post_termination_window.py`` מכוון: שם נבדק
*החישוב* (90 יום מתאריך העזיבה), כאן נבדק **מאיזה שעון מגיע ה-``check_date``**
שמוזן לאותו חישוב. הפער הזה בדיוק הוא מה שאיפשר לח1 לעבור סוויטה ירוקה.

מיפוי: docs/qa/v0.9.1.md, QA-091-16 עד QA-091-20.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from backend.app import types as types_module
from backend.app.api import exercise_requests as exercise_requests_module
from backend.app.auth import hash_password
from backend.app.models import (
    Company, Employee, EmployeeStatus, Grant, GrantType, LedgerEvent, OptionPool,
    User, UserRole, UserSession, VestingSchedule,
)
from backend.app.types import utcnow

API = "/api/v1"

# עזיבה ב-10/01/2025 + 90 יום = 10/04/2025. החישוב הידני מלא ב-
# test_post_termination_window.py::test_terminated_last_allowed_day.
TERMINATION = date(2025, 1, 10)
DEADLINE = date(2025, 4, 10)


def _freeze_utc(monkeypatch, instant_utc: datetime) -> None:
    """מקפיא את *הרגע* (UTC), לא את התאריך.

    זה העיקר: שתי פונקציות השעון מקבלות את אותו רגע פיזי בדיוק ונחלקות רק על
    השאלה באיזה אזור זמן קוראים אותו. הקפאת תאריך הייתה מניחה את המסקנה.
    """

    class _FrozenDatetime:
        @staticmethod
        def now(tz=None):
            return instant_utc.astimezone(tz) if tz else instant_utc.replace(tzinfo=None)

    monkeypatch.setattr(types_module, "datetime", _FrozenDatetime)


# ---------------------------------------------------------------------------
# QA-091-16/17: סמנטיקת השעון עצמה
# ---------------------------------------------------------------------------

def test_utc_clock_reports_yesterday_at_01_00_jerusalem(monkeypatch):
    """זה ח1 מילה במילה: 30/08/2025 22:00Z הוא 31/08 בשעה 01:00 בירושלים (UTC+3
    בקיץ). עובד שהדדליין שלו 30/08 היה מתקבל למחרת, כי ה-UTC עוד "אתמול"."""
    instant = datetime(2025, 8, 30, 22, 0, tzinfo=timezone.utc)
    assert instant.astimezone(types_module.BUSINESS_TIMEZONE).hour == 1, "הנחת UTC+3 בקיץ"

    _freeze_utc(monkeypatch, instant)
    assert types_module.system_today_utc() == date(2025, 8, 30)
    assert types_module.business_today() == date(2025, 8, 31)


def test_utc_clock_crosses_a_tax_year_backwards(monkeypatch):
    """זה ח2, והנזק שם חמור יותר: 31/12/2026 22:30Z הוא 01/01/2027 בשעה 00:30
    בירושלים (UTC+2 בחורף). אירוע כזה נרשם ב-``effective_date`` של **שנת המס
    הקודמת**, בטבלה append-only שהטריגר ``trg_ledger_events_no_update`` חוסם
    עליה כל UPDATE - כלומר בלתי ניתן לתיקון בדיעבד."""
    instant = datetime(2026, 12, 31, 22, 30, tzinfo=timezone.utc)
    assert instant.astimezone(types_module.BUSINESS_TIMEZONE).hour == 0, "הנחת UTC+2 בחורף"

    _freeze_utc(monkeypatch, instant)
    assert types_module.system_today_utc().year == 2026
    assert types_module.business_today() == date(2027, 1, 1)


# ---------------------------------------------------------------------------
# חיווט דרך ה-API. הבדיקות הבאות מחליפות את ``routes.business_today`` בלבד -
# ולכן חזרה ל-``system_today_utc()`` באתר הקריאה מפילה אותן, כי התיקון פשוט
# לא יחול. זה הכיסוי שהיה חסר: לפני כן החזרת האתרים ל-date.today() לא הפילה דבר.
# ---------------------------------------------------------------------------

@pytest.fixture
def terminated_world(db_session):
    """עובד שעזב ב-10/01/2025, עם מענק מוּבשל היטב עד הדדליין."""
    db = db_session
    db.add(Company(company_id="C-BD", name="BizDay Ltd", country_code="IL"))
    db.flush()
    db.add(OptionPool(pool_id="P-BD", company_id="C-BD", total_shares=100000.0,
                      allocated_shares=4800.0, unallocated_shares=95200.0))
    db.add(Employee(employee_id="E-BD", company_id="C-BD", first_name="Dana", last_name="Levi",
                    email="e-bd@bizday.example", country_code="IL",
                    status=EmployeeStatus.TERMINATED, hire_date=date(2020, 1, 1),
                    birth_date=date(1990, 1, 1), termination_date=TERMINATION))
    db.flush()
    db.add(Grant(grant_id="G-BD", employee_id="E-BD", pool_id="P-BD",
                 grant_date=date(2022, 1, 1), grant_type=GrantType.IL_102_CAPITAL_GAINS,
                 total_options=4800.0, exercise_price=1.0, currency="USD",
                 post_termination_window_days=90))
    db.add(VestingSchedule(schedule_id="S-BD", grant_id="G-BD", start_date=date(2022, 1, 1),
                           cliff_months=12, total_months=48, paused_days_total=0))

    pw_hash, salt = hash_password("Demo1234!")
    db.add(User(user_id="U-BD", username="e-bd@bizday.example", password_hash=pw_hash,
                password_salt=salt, role=UserRole.EMPLOYEE, is_active=True, employee_id="E-BD"))
    db.flush()
    db.add(UserSession(token="tok-bd", user_id="U-BD", expires_at=utcnow() + timedelta(hours=1)))
    db.flush()
    return {"Authorization": "Bearer tok-bd"}


def _submit(client, headers):
    return client.post(f"{API}/employee/exercise-requests", headers=headers,
                       json={"grant_id": "G-BD", "options_to_exercise": 100})


def test_window_is_open_on_the_deadline_day_itself(client, terminated_world, monkeypatch):
    """QA-091-18. יום הדדליין עצמו עדיין מותר (התנאי הוא ``<=``)."""
    monkeypatch.setattr(exercise_requests_module, "business_today", lambda: DEADLINE)
    assert _submit(client, terminated_world).status_code == 200


def test_window_is_closed_the_day_after_the_deadline(client, terminated_world, monkeypatch):
    """QA-091-19 - **הבדיקה שח1 נכשל בה.** ב-11/04 בשעה 01:00 בירושלים ה-UTC
    עדיין מראה 10/04, ולכן הגרסה שלפני התיקון הייתה מקבלת את הבקשה."""
    monkeypatch.setattr(exercise_requests_module, "business_today", lambda: DEADLINE + timedelta(days=1))
    response = _submit(client, terminated_world)
    assert response.status_code == 400
    assert str(DEADLINE) in response.json()["detail"]


def test_ledger_effective_date_is_the_business_day(client, db_session, terminated_world,
                                                   monkeypatch):
    """QA-091-20 - ח2. ``effective_date`` של האירוע חייב להיות יום העסקים, ולא
    תאריך ה-UTC של אותו רגע. זו הרשומה שאי אפשר לתקן בדיעבד."""
    monkeypatch.setattr(exercise_requests_module, "business_today", lambda: DEADLINE)
    assert _submit(client, terminated_world).status_code == 200

    event = (db_session.query(LedgerEvent)
             .filter(LedgerEvent.event_type == "EXERCISE_REQUEST_SUBMITTED")
             .one())
    assert event.effective_date == DEADLINE
    # recorded_at נשאר UTC בכוונה: הוא ממד ה*ידיעה*, לא ממד ה*תוקף*. איחודם
    # לשעון אחד הוא בדיוק מה ששלב א עשה, והוא מוחק את ההבחנה הבי-טמפורלית.
    assert event.recorded_at.tzinfo is not None


# ---------------------------------------------------------------------------
# QA-091-22: הצד השני של אותו גבול - חותמת *מאוחסנת* שנקראת בשעון הלא נכון.
# ---------------------------------------------------------------------------

def test_a_stored_utc_timestamp_is_read_as_its_business_day():
    """בקשה שהוגשה ב-01:00 בירושלים נשמרת עם חותמת UTC של *אתמול*.

    ``.date()`` עליה היה מחזיר את היום הקודם, והשוואתה מול ``business_today()``
    הייתה סופרת יום המתנה מיותר בהתראת "בקשה ממתינה יותר מדי". זהו ערבוב
    שעונים בכיוון ההפוך לח1/ח2: השעון תקין, וה*המרה* היא שחסרה.
    """
    stored = datetime(2025, 8, 30, 22, 0, tzinfo=timezone.utc)  # 31/08 01:00 בירושלים
    assert stored.date() == date(2025, 8, 30)
    assert types_module.business_date_of(stored) == date(2025, 8, 31)


def test_pending_too_long_does_not_count_an_extra_day_of_waiting():
    """QA-091-23 - החיווט של אותו גבול, בכלל ההתראה עצמו.

    בקשה שהוגשה ב-31/08 בשעה 01:00 בירושלים, נבדקת ב-06/09 עם סף של 7 ימים:
    ההמתנה בפועל היא **6 ימים**, ולכן ההתראה אינה אמורה לפעול. עם ``.date()``
    על החותמת האגורה היא נקראת כ-30/08, ההמתנה נספרת כ-7, וההתראה קופצת יום
    מוקדם. הבדיקה נופלת בדיוק על הגבול הזה.
    """
    from backend.app.models import ExerciseRequest, ExerciseRequestStatus
    from backend.app.services import notifications as notif

    req = ExerciseRequest(request_id="R-BD", grant_id="G-BD", employee_id="E-BD",
                          options_requested=100.0, status=ExerciseRequestStatus.PENDING,
                          requested_at=datetime(2025, 8, 30, 22, 0, tzinfo=timezone.utc))

    assert notif._rule_request_pending_too_long(req, date(2025, 9, 6), 7) is None
    # ויום אחד אחר כך היא כן פועלת - הגבול קיים, הוא רק במקום הנכון.
    assert notif._rule_request_pending_too_long(req, date(2025, 9, 7), 7) is not None
