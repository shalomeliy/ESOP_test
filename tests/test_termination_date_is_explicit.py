"""תאריך סיום ההעסקה מגיע מהאדמין, לא מהשעון - סגירת החוב מ-v0.9.1 שלב א.

שלב א העביר את ``delete_employee`` מ-``date.today()`` ל-``business_today()``
והסיר בכך את התלות באזור הזמן של המארח, אבל השאיר את הפגם העמוק פתוח: עזיבה
מדווחת כמעט תמיד בדיעבד, ולכן *כל* שעון קובע כאן זכות כספית לפי מתי אדמין
הספיק להיכנס למערכת. ``termination_date`` מזין שני חישובים כספיים - דדליין
חלון המימוש (``termination_date + window_days``) ונקודת העצירה של ההבשלה
(``min(target_date, termination_date)``) - ונרשם ל-``ledger_events``, טבלה
append-only שהטריגר חוסם עליה כל UPDATE.

הבדיקות כאן הן על *מקור* התאריך, לא על החישוב שנגזר ממנו: החישוב מכוסה
ב-test_post_termination_window.py, והשעון עצמו ב-test_business_day_clock.py.

מיפוי: docs/qa/v0.9.1.md, QA-091-24 עד QA-091-27.
"""

import json
from datetime import date, timedelta

import pytest

from backend.app.auth import hash_password
from backend.app.models import (
    Company, Employee, EmployeeStatus, Grant, GrantType, LedgerEvent, OptionPool,
    User, UserRole, UserSession, VestingSchedule,
)
from backend.app.types import utcnow

API = "/api/v1"

# תאריך עבר מובהק, ורחוק מכל ערך שהשעון היה מייצר - כך שבדיקה שעוברת מוכיחה
# שהערך הגיע מהקלט ולא מ-business_today() שבמקרה הסכים איתו.
LAST_DAY = date(2025, 3, 17)


@pytest.fixture
def admin_world(db_session):
    """שני עובדים באותה חברה: אחד עם מענק (מסלול עזיבה) ואחד בלי (מחיקה מלאה).
    שניהם נדרשים - ההבחנה ביניהם היא בדיוק מה שקובע אם התאריך חובה."""
    db = db_session
    db.add(Company(company_id="C-TD", name="TermDate Ltd", country_code="IL"))
    db.flush()
    db.add(OptionPool(pool_id="P-TD", company_id="C-TD", total_shares=10000.0,
                      allocated_shares=1200.0, unallocated_shares=8800.0))
    db.add(Employee(employee_id="E-GRANTED", company_id="C-TD", first_name="Noa", last_name="Bar",
                    email="granted@termdate.example", country_code="IL",
                    status=EmployeeStatus.ACTIVE, hire_date=date(2021, 1, 1),
                    birth_date=date(1990, 1, 1)))
    db.add(Employee(employee_id="E-BARE", company_id="C-TD", first_name="Omri", last_name="Gal",
                    email="bare@termdate.example", country_code="IL",
                    status=EmployeeStatus.ACTIVE, hire_date=date(2021, 1, 1),
                    birth_date=date(1990, 1, 1)))
    db.flush()
    db.add(Grant(grant_id="G-TD", employee_id="E-GRANTED", pool_id="P-TD",
                 grant_date=date(2022, 1, 1), grant_type=GrantType.IL_102_CAPITAL_GAINS,
                 total_options=1200.0, exercise_price=1.0, currency="USD",
                 post_termination_window_days=90))
    db.add(VestingSchedule(schedule_id="S-TD", grant_id="G-TD", start_date=date(2022, 1, 1),
                           cliff_months=12, total_months=48, paused_days_total=0))

    pw_hash, salt = hash_password("Demo1234!")
    db.add(User(user_id="U-TD", username="admin@termdate.example", password_hash=pw_hash,
                password_salt=salt, role=UserRole.COMPANY_ADMIN, is_active=True,
                company_id="C-TD"))
    db.flush()
    db.add(UserSession(token="tok-td", user_id="U-TD", expires_at=utcnow() + timedelta(hours=1)))
    db.flush()
    return {"Authorization": "Bearer tok-td"}


def test_terminating_without_a_date_is_refused(client, db_session, admin_world):
    """QA-091-24. בלי תאריך אין ניחוש: 400, והעובד נשאר ACTIVE.

    זו הבדיקה שהייתה נכשלת לפני התיקון - קודם הבקשה הזו הצליחה והשעון סיפק
    את התאריך בשקט."""
    response = client.delete(f"{API}/admin/employees/E-GRANTED", headers=admin_world)

    assert response.status_code == 400
    assert "termination_date" in response.json()["detail"]

    employee = db_session.get(Employee, "E-GRANTED")
    assert employee.status == EmployeeStatus.ACTIVE, "בקשה שנדחתה לא אמורה לשנות סטטוס"
    assert employee.termination_date is None


def test_the_supplied_date_is_the_one_stored(client, db_session, admin_world):
    """QA-091-25. התאריך שנשמר הוא זה שסופק, ולא היום."""
    response = client.delete(f"{API}/admin/employees/E-GRANTED", headers=admin_world,
                             params={"termination_date": str(LAST_DAY)})

    assert response.status_code == 200
    assert response.json()["deleted"] == "soft"

    employee = db_session.get(Employee, "E-GRANTED")
    assert employee.status == EmployeeStatus.TERMINATED
    assert employee.termination_date == LAST_DAY


def test_the_ledger_event_carries_the_supplied_date(client, db_session, admin_world):
    """QA-091-26. גם ``effective_date`` וגם ה-payload נושאים את התאריך שסופק.

    זו הרשומה שאי אפשר לתקן: ``trg_ledger_events_no_update`` חוסם כל UPDATE,
    ולכן תאריך שגוי כאן נשאר שגוי לתמיד."""
    client.delete(f"{API}/admin/employees/E-GRANTED", headers=admin_world,
                  params={"termination_date": str(LAST_DAY)})

    event = (db_session.query(LedgerEvent)
             .filter(LedgerEvent.aggregate_id == "E-GRANTED",
                     LedgerEvent.event_type == "EMPLOYEE_STATUS_CHANGED")
             .one())
    assert event.effective_date == LAST_DAY
    assert json.loads(event.payload)["termination_date"] == str(LAST_DAY)


def test_hard_delete_still_needs_no_date(client, db_session, admin_world):
    """QA-091-27. הדרישה חלה רק על מסלול העזיבה. לעובד בלי מענקים אין תאריך
    סיום שמזין חישוב כלשהו, ודרישת תאריך שם הייתה טקס ריק."""
    response = client.delete(f"{API}/admin/employees/E-BARE", headers=admin_world)

    assert response.status_code == 200
    assert response.json()["deleted"] == "hard"
    assert db_session.get(Employee, "E-BARE") is None


# ===================================================================
# v1.0.1 (debt item 3) - ולידציית termination_date מול hire_date/העתיד.
# החוב שנרשם ב-HANDOFF.md: "הוספת כלל לנתיב אחד בלבד הייתה יוצרת בדיוק את
# P3" - ולכן הבדיקות כאן פרמטריות זהות על *שני* הנתיבים (DELETE .../employees
# ו-PATCH .../status) בבת אחת, כדי ששני הנתיבים לא יוכלו לסחוף שוב.
# ===================================================================

def _delete_response(client, headers, employee_id, termination_date):
    return client.delete(f"{API}/admin/employees/{employee_id}", headers=headers,
                         params={"termination_date": str(termination_date)})


def _status_response(client, headers, employee_id, termination_date):
    return client.patch(f"{API}/admin/employees/{employee_id}/status", headers=headers,
                        json={"status": "TERMINATED", "effective_date": str(termination_date),
                              "return_unvested_to_pool": False})


_TERMINATION_DATE_BOUNDARY_CASES = [
    # (label, anchor "hire"/"today", offset-in-days-from-anchor, expected_status)
    ("before_hire_date", "hire", -1, 400),
    ("on_hire_date_boundary_allowed", "hire", 0, 200),
    ("on_today_boundary_allowed", "today", 0, 200),
    ("one_day_in_the_future", "today", 1, 400),
    ("years_in_the_future", "today", 3650, 400),
]


@pytest.mark.parametrize("endpoint", ["delete", "status"])
@pytest.mark.parametrize(
    "label,anchor,offset_days,expected_status", _TERMINATION_DATE_BOUNDARY_CASES,
    ids=[c[0] for c in _TERMINATION_DATE_BOUNDARY_CASES],
)
def test_termination_date_boundary_is_enforced_identically_on_both_endpoints(
        client, db_session, admin_world, endpoint, label, anchor, offset_days, expected_status):
    """QA-101 (v1.0.1). E-GRANTED (hire_date=2021-01-01, ראו admin_world) על שני
    הנתיבים, עם אותם חמשת המקרים בדיוק - כדי שנקודת ה-id של הבדיקה תוכיח שהיא
    בדיוק אותו תרחיש בשני הנתיבים, לא שני תרחישים דומים במקרה."""
    from backend.app.types import business_today

    employee = db_session.get(Employee, "E-GRANTED")
    anchor_date = employee.hire_date if anchor == "hire" else business_today()
    termination_date = anchor_date + timedelta(days=offset_days)

    if endpoint == "delete":
        response = _delete_response(client, admin_world, "E-GRANTED", termination_date)
    else:
        response = _status_response(client, admin_world, "E-GRANTED", termination_date)

    assert response.status_code == expected_status, response.text
    if expected_status == 400:
        assert "termination_date" in response.json()["detail"]
