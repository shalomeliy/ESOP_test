"""באגים *מכוונים* ששתולים במערכת לצורכי תרגול QA.

מקור האמת: ``qa_bug_accounts.md`` בשורש הפרויקט (לכל באג יש שם חשבון login
לשחזור ידני דרך הפורטלים). הבדיקות כאן מאשרות שהבאג **עדיין משחזר** - הן לא
מתקנות ולא מכסות אותו. אם אחת מהן נכשלת, המשמעות היא שמישהו "תיקן" באג שנועד
להישאר, ותרגיל ה-QA איבד את המקרה הזה.

הכלל: ``@pytest.mark.intentional_bug`` על כל בדיקה בקובץ הזה, פרט לזו שמסומנת
במפורש כשמירת רגרסיה על התנהגות *נכונה*.
"""

from datetime import date

import pytest

from backend.app.models import EmployeeStatus
from backend.app.services.engine import DeterministicESOPEngine


@pytest.mark.intentional_bug
def test_feb29_vesting_start_crashes_on_non_leap_cliff_year(make_grant, make_schedule):
    """באג #20 ב-qa_bug_accounts.md (bug.feb29@buglab.example, EMP-BUG-FEB29-1).

    ``calculate_vested_options`` בונה את תאריך ה-cliff עם
    ``date(adjusted_start.year + cliff_months // 12, adjusted_start.month,
    adjusted_start.day)`` - העברת יום/חודש כפי שהם, בלי טיפול ב-29 בפברואר.
    start=2024-02-29 (שנה מעוברת) + cliff של 24 חודשים -> ניסיון לבנות
    date(2026, 2, 29). 2026 אינה מעוברת -> ValueError, וה-endpoint
    GET /employee/dashboard/EMP-BUG-FEB29-1 מחזיר 500.

    התיקון הנכון (כשיוחלט לתקן) הוא clamp ל-28 בפברואר או ל-1 במרץ - החלטה
    עסקית, לא טכנית. עד אז: הבאג נשאר, והבדיקה שומרת שהוא נשאר.
    """
    grant = make_grant(total_options=4800.0, grant_date=date(2024, 2, 29))
    schedule = make_schedule(start_date=date(2024, 2, 29), cliff_months=24,
                             total_months=48, paused_days_total=0)

    with pytest.raises(ValueError, match="day is out of range|must be in range"):
        DeterministicESOPEngine.calculate_vested_options(grant, schedule, date(2026, 3, 1))


@pytest.mark.intentional_bug
def test_feb29_trustee_deposit_crashes_the_same_way(make_grant):
    """*** מופע נוסף של אותה מחלקת באג, שאינו מתועד ב-qa_bug_accounts.md. ***

    ``check_trustee_holding_period`` בונה את סוף החסימה בדיוק באותה צורה:
    ``date(deposit.year + 2, deposit.month, deposit.day)``. הפקדה אצל נאמן
    ב-2024-02-29 (תאריך חוקי לחלוטין, שנאמן יכול להזין דרך
    POST /trustee/grants/{grant_id}/deposit) -> date(2026, 2, 29) -> ValueError.

    הבדיקה מסומנת intentional_bug כי מדובר באותו דפוס קוד מכוון, אבל דווח
    בנפרד: זה נתיב הגעה נוסף שלא נמצא במפת הבאגים.
    """
    grant = make_grant(trustee_deposit_date=date(2024, 2, 29))

    with pytest.raises(ValueError, match="day is out of range|must be in range"):
        DeterministicESOPEngine.check_trustee_holding_period(grant, date(2026, 3, 1))


@pytest.mark.intentional_bug
def test_grant_without_vesting_schedule_is_permanently_unvested(make_grant):
    """באג #12 ב-qa_bug_accounts.md (EMP-UNEXERCISED-1, unex@company.com).

    מענק ישן (2015) בלי VestingSchedule כלל. ``calculate_vested_options``
    מחזיר 0.0 בענף ``if not schedule`` - כלומר עובד עם מענק בן עשור רואה
    vested=0 לצמיתות, בלי שום אזהרה שהנתון חסר ולא שהאופציות לא הבשילו.
    שני המצבים האלה נראים זהים למשתמש, וזו הבעיה.
    """
    old_grant = make_grant(total_options=5000.0, grant_date=date(2015, 1, 1))

    assert DeterministicESOPEngine.calculate_vested_options(
        old_grant, None, date(2026, 7, 28)
    ) == 0.0


def test_boomerang_active_employee_is_not_restricted_by_historic_termination(
        make_grant, make_employee):
    """שמירת רגרסיה על התנהגות **נכונה** - לא באג (ולכן בלי המרקר).

    תרחיש #10 ב-qa_bug_accounts.md (EMP-REHIRE-1, rehire1@boomerang.example):
    עובד שעזב וחזר. ``termination_date`` נשאר היסטורי (2023-05-01) גם אחרי
    שהוא ACTIVE שוב. הקוד בודק קודם כל את ה-*סטטוס*, ולכן מחזיר (True, None) -
    אין חלון PTEW על עובד פעיל, נכון.

    זו בדיוק הבדיקה שתיפול אם מישהו "יתקן" את הקוד לבדוק termination_date לפני
    status - ואז עובד שחזר לעבודה יאבד את זכות המימוש שלו. בגלל זה היא כאן.
    """
    grant = make_grant(post_termination_window_days=90)
    boomerang = make_employee(status=EmployeeStatus.ACTIVE,
                              termination_date=date(2023, 5, 1))

    assert DeterministicESOPEngine.check_post_termination_exercise_window(
        grant, boomerang, date(2026, 7, 28)
    ) == (True, None)
