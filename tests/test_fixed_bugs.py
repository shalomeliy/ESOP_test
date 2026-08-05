"""בדיקות רגרסיה על הבאגים שהיו *מכוונים* עד לתיקון שלהם.

הקובץ הזה החליף את ``tests/test_intentional_bugs.py``. עד לתיקון, כל בדיקה שם
אישרה שהבאג עדיין משחזר; כאן כל בדיקה מאשרת את ההתנהגות **הנכונה** ומונעת
חזרה שקטה של אותו דפוס. המיפוי לבאגים המקוריים לפי המזהים ב-``QA_TESTBOOK.md`` (QA-050-2x/3x).

מי שרוצה את המערכת הבאגית בחזרה לצורכי תרגול: התג ``qa-buggy-baseline-v1``
מחזיק את הקוד וה-DB לפני התיקון. אין להחזיר באגים לתוך קו המוצר.
"""

from datetime import date

import pytest

from backend.app.models import EmployeeStatus
from backend.app.services.engine import (
    DeterministicESOPEngine, MissingVestingScheduleError, shift_months,
    CLAMP_BACK, ROLL_FORWARD,
)


# ===================================================================
# באגים #20 + #21 - 29 בפברואר
# ===================================================================

def test_feb29_vesting_start_no_longer_crashes_and_clamps_back(make_grant, make_schedule):
    """באג #20. start=2024-02-29 + cliff של 24 חודשים.

    חישוב ידני: 2026 אינה מעוברת, ולכן ה-cliff נסגר אחורה ל-2026-02-28.
    ב-2026-02-28 עצמו כבר עברנו את ה-cliff, וחלפו 24 חודשים מלאים:
    4800/48 = 100 לחודש -> 100 * 24 = 2400.
    """
    grant = make_grant(total_options=4800.0, grant_date=date(2024, 2, 29))
    schedule = make_schedule(start_date=date(2024, 2, 29), cliff_months=24,
                             total_months=48, paused_days_total=0)

    assert DeterministicESOPEngine.calculate_vested_options(
        grant, schedule, date(2026, 2, 27)) == 0.0, "יום לפני ה-cliff המוזז"
    assert DeterministicESOPEngine.calculate_vested_options(
        grant, schedule, date(2026, 2, 28)) == 2400.0
    assert DeterministicESOPEngine.calculate_vested_options(
        grant, schedule, date(2026, 3, 1)) == 2400.0


def test_feb29_trustee_deposit_no_longer_crashes_and_rolls_forward(make_grant):
    """באג #21. הפקדה ב-2024-02-29, חסימה של 24 חודשים.

    ROLL_FORWARD בכוונה, בשונה מה-cliff: 2026-02-29 אינו קיים, והתקופה
    הסטטוטורית לא מתקצרת - ולכן היא מסתיימת ב-2026-03-01. סגירה אחורה
    (28/2) הייתה מזכה במסלול רווח הון יום אחד לפני הזמן.
    """
    grant = make_grant(trustee_deposit_date=date(2024, 2, 29))

    met_before, end_date = DeterministicESOPEngine.check_trustee_holding_period(
        grant, date(2026, 2, 28))
    assert (met_before, end_date) == (False, date(2026, 3, 1))

    met_on, _ = DeterministicESOPEngine.check_trustee_holding_period(grant, date(2026, 3, 1))
    assert met_on is True


@pytest.mark.parametrize(
    "anchor, months, policy, expected",
    [
        (date(2024, 2, 29), 24, CLAMP_BACK, date(2026, 2, 28)),
        (date(2024, 2, 29), 24, ROLL_FORWARD, date(2026, 3, 1)),
        (date(2024, 1, 31), 1, CLAMP_BACK, date(2024, 2, 29)),   # 2024 מעוברת
        (date(2023, 1, 31), 1, CLAMP_BACK, date(2023, 2, 28)),
        (date(2023, 1, 31), 1, ROLL_FORWARD, date(2023, 3, 1)),
        (date(2022, 1, 1), 48, CLAMP_BACK, date(2026, 1, 1)),    # מקרה רגיל, ללא יום חסר
        (date(2024, 5, 31), 1, CLAMP_BACK, date(2024, 6, 30)),   # חודש בן 30
    ],
)
def test_shift_months_never_raises_on_missing_day(anchor, months, policy, expected):
    """שורש שני הבאגים היה בנייה ישירה של date(year, month, day). כאן כל
    הקומבינציות של יום-חסר מוחזרות במפורש, בשני הכיוונים."""
    assert shift_months(anchor, months, policy) == expected


# ===================================================================
# באג #12 - מענק בלי לוח הבשלה
# ===================================================================

def test_grant_without_vesting_schedule_raises_instead_of_reporting_zero(make_grant):
    """באג #12. קודם הוחזר 0.0, ולכן "אין נתון" ו"לא הבשיל כלום" נראו זהים
    לעובד. עכשיו זו חריגה מפורשת, וה-endpoints מסמנים vesting_data_missing."""
    old_grant = make_grant(total_options=5000.0, grant_date=date(2015, 1, 1))

    with pytest.raises(MissingVestingScheduleError):
        DeterministicESOPEngine.calculate_vested_options(old_grant, None, date(2026, 7, 28))


# ===================================================================
# הבשלה נעצרת בעזיבה (השורש של דריפט הפול בבאג #5)
# ===================================================================

def test_vesting_stops_at_termination_date(make_grant, make_schedule, make_employee):
    """עובד שעזב ב-2024-01-01 לא ממשיך להבשיל אחר כך.

    חישוב ידני: start=2022-01-01, 100 אופציות לחודש. ב-2024-01-01 חלפו 24
    חודשים -> 2400. גם ב-2026-07-01 (כשלמעשה חלפו 54 חודשים) הכמות נשארת
    2400, כי ה-cutoff הוא יום העזיבה.
    """
    grant = make_grant(total_options=4800.0, grant_date=date(2022, 1, 1))
    schedule = make_schedule(start_date=date(2022, 1, 1), cliff_months=12,
                             total_months=48, paused_days_total=0)
    leaver = make_employee(status=EmployeeStatus.TERMINATED,
                           termination_date=date(2024, 1, 1))

    cutoff = DeterministicESOPEngine.vesting_cutoff_date(leaver, date(2026, 7, 1))
    assert cutoff == date(2024, 1, 1)
    assert DeterministicESOPEngine.calculate_vested_options(grant, schedule, cutoff) == 2400.0


def test_boomerang_active_employee_keeps_vesting_despite_historic_termination(
        make_grant, make_schedule, make_employee):
    """תרחיש #10: עובד שעזב וחזר. הסטטוס (ACTIVE) קובע ולא termination_date
    ההיסטורי - אחרת עובד שחזר לעבודה היה קופא על ההבשלה של פעם."""
    grant = make_grant(total_options=4800.0, grant_date=date(2022, 1, 1))
    schedule = make_schedule(start_date=date(2022, 1, 1), cliff_months=12,
                             total_months=48, paused_days_total=0)
    boomerang = make_employee(status=EmployeeStatus.ACTIVE,
                              termination_date=date(2023, 5, 1))

    assert DeterministicESOPEngine.vesting_cutoff_date(boomerang, date(2026, 1, 1)) == date(2026, 1, 1)
    assert DeterministicESOPEngine.calculate_vested_options(
        grant, schedule, date(2026, 1, 1)) == 4800.0


def test_boomerang_active_employee_is_not_restricted_by_historic_termination(
        make_grant, make_employee):
    """שמירת רגרסיה על התנהגות שהייתה נכונה מלכתחילה (תרחיש #10).

    זו הבדיקה שתיפול אם מישהו "יתקן" את הקוד לבדוק termination_date לפני
    status - ואז עובד שחזר לעבודה יאבד את זכות המימוש שלו.
    """
    grant = make_grant(post_termination_window_days=90)
    boomerang = make_employee(status=EmployeeStatus.ACTIVE,
                              termination_date=date(2023, 5, 1))

    assert DeterministicESOPEngine.check_post_termination_exercise_window(
        grant, boomerang, date(2026, 7, 28)
    ) == (True, None)


# ===================================================================
# באגים שלא היו במפה והתגלו בזמן התיקון
# ===================================================================

def test_cliff_not_divisible_by_twelve_is_computed_in_months(make_grant, make_schedule):
    """הקוד הקודם חישב ``year + cliff_months // 12`` - כלומר cliff של 6 חודשים
    התאפס לגמרי (cliff = תאריך ההתחלה), ו-18 חודשים הפך ל-12.

    חישוב ידני ל-cliff=6: start=2022-01-01 -> cliff=2022-07-01. ב-2022-06-30
    עדיין 0; ב-2022-07-01 חלפו 6 חודשים -> 100*6 = 600.
    """
    grant = make_grant(total_options=4800.0, grant_date=date(2022, 1, 1))
    schedule = make_schedule(start_date=date(2022, 1, 1), cliff_months=6,
                             total_months=48, paused_days_total=0)

    assert DeterministicESOPEngine.calculate_vested_options(
        grant, schedule, date(2022, 6, 30)) == 0.0
    assert DeterministicESOPEngine.calculate_vested_options(
        grant, schedule, date(2022, 7, 1)) == 600.0


def test_mid_month_start_does_not_vest_a_month_early(make_grant, make_schedule):
    """הפרש חודשים קלנדרי זיכה חודש שלם ברגע שמספר החודש התחלף.

    חישוב ידני: start=2022-01-15, cliff=1 חודש, 48 חודשים, 100 לחודש.
    ב-2022-02-14 טרם חלף החודש הראשון -> 0 (קודם: 100, 14 יום מוקדם מדי).
    ב-2022-02-15 חלף חודש אחד מלא -> 100.
    """
    grant = make_grant(total_options=4800.0, grant_date=date(2022, 1, 15))
    schedule = make_schedule(start_date=date(2022, 1, 15), cliff_months=1,
                             total_months=48, paused_days_total=0)

    assert DeterministicESOPEngine.calculate_vested_options(
        grant, schedule, date(2022, 2, 14)) == 0.0
    assert DeterministicESOPEngine.calculate_vested_options(
        grant, schedule, date(2022, 2, 15)) == 100.0
