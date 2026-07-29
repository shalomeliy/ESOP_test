"""חלון מימוש לאחר עזיבה (PTEW).

הסיכון: שני מקורות לאורך החלון - תנאי התוכנית פר-מענק
(``grant.post_termination_window_days``, ברירת מחדל 90) מול הכלל הקבוע של 365 יום
במקרה פטירה. אם הבחירה ביניהם שגויה, יורש של עובד שנפטר מאבד את המענק תשעה
חודשים לפני הזמן.
"""

from datetime import date

import pytest

from backend.app.models import EmployeeStatus
from backend.app.services.engine import DeterministicESOPEngine

TERMINATION = date(2025, 1, 10)


@pytest.fixture
def grant_90d(make_grant):
    return make_grant(post_termination_window_days=90)


def test_terminated_last_allowed_day(grant_90d, make_employee):
    """חישוב ידני של 2025-01-10 + 90 יום (2025 אינה שנה מעוברת):
    ינואר נותרו 21 יום (עד 31/1) -> נשארו 69
    פברואר 28 יום -> נשארו 41 (ב-28/2)
    מרץ 31 יום -> נשארו 10 (ב-31/3)
    +10 -> 2025-04-10 = הדדליין.
    התנאי הוא check_date <= deadline, ולכן היום עצמו עדיין מותר.
    """
    emp = make_employee(status=EmployeeStatus.TERMINATED, termination_date=TERMINATION)
    allowed, deadline = DeterministicESOPEngine.check_post_termination_exercise_window(
        grant_90d, emp, date(2025, 4, 10)
    )
    assert allowed is True
    assert deadline == date(2025, 4, 10)


def test_terminated_one_day_after_deadline_is_closed(grant_90d, make_employee):
    """2025-04-11 - יום אחד אחרי הדדליין. החלון סגור."""
    emp = make_employee(status=EmployeeStatus.TERMINATED, termination_date=TERMINATION)
    allowed, deadline = DeterministicESOPEngine.check_post_termination_exercise_window(
        grant_90d, emp, date(2025, 4, 11)
    )
    assert allowed is False
    assert deadline == date(2025, 4, 10)


def test_deceased_365_day_rule_overrides_the_grants_90_days(grant_90d, make_employee):
    """אותו תאריך עזיבה בדיוק, אבל DECEASED: הכלל הקבוע של 365 יום גובר על
    90 הימים של המענק.
    חישוב ידני: 2025-01-10 + 365 יום. 2025 אינה מעוברת, ולכן 365 יום = בדיוק
    שנה קלנדרית -> 2026-01-10.
    """
    emp = make_employee(status=EmployeeStatus.DECEASED, termination_date=TERMINATION)
    allowed, deadline = DeterministicESOPEngine.check_post_termination_exercise_window(
        grant_90d, emp, date(2025, 4, 11)
    )
    assert deadline == date(2026, 1, 10)
    # אותו תאריך בדיוק שסגר את החלון ל-TERMINATED - כאן עדיין פתוח.
    assert allowed is True


def test_active_employee_has_no_window_at_all(grant_90d, make_employee):
    """עובד פעיל - אין הגבלת זמן, ולכן deadline=None."""
    emp = make_employee(status=EmployeeStatus.ACTIVE, termination_date=None)
    assert DeterministicESOPEngine.check_post_termination_exercise_window(
        grant_90d, emp, date(2030, 1, 1)
    ) == (True, None)


def test_terminated_without_termination_date_is_not_restricted(grant_90d, make_employee):
    """סטטוס TERMINATED בלי תאריך עזיבה: אין ממה לספור 90 יום, ולכן לא חוסמים.
    זו החלטת fail-open מכוונת בקוד - מתועדת כאן כדי שכל שינוי בה ייראה ב-diff."""
    emp = make_employee(status=EmployeeStatus.TERMINATED, termination_date=None)
    assert DeterministicESOPEngine.check_post_termination_exercise_window(
        grant_90d, emp, date(2030, 1, 1)
    ) == (True, None)
