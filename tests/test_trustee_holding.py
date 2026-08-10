"""חסימת נאמנות של סעיף 102 - שנתיים מיום ההפקדה אצל הנאמן.

הקצה המסוכן: היום שלפני תום התקופה מול היום עצמו. אישור מימוש יום אחד מוקדם
מדי מפיל את המענק ממסלול רווח הון למסלול הכנסת עבודה - הפרש מס אמיתי.
"""

from datetime import date

import pytest

from backend.app.services.engine import DeterministicESOPEngine

DEPOSIT = date(2023, 6, 15)
EXPECTED_END = date(2025, 6, 15)  # deposit.year + 2, אותו יום ואותו חודש


@pytest.fixture
def deposited_grant(make_grant):
    return make_grant(trustee_deposit_date=DEPOSIT)


def test_day_before_holding_period_ends_is_not_met(deposited_grant):
    """2025-06-14 = יום אחד לפני תום השנתיים -> עדיין חסום, והדדליין המוחזר
    הוא 2025-06-15 (כדי שה-UI יוכל להציג ממתי מותר)."""
    met, end_date = DeterministicESOPEngine.check_trustee_holding_period(
        deposited_grant, date(2025, 6, 14)
    )
    assert (met, end_date) == (False, EXPECTED_END)


def test_exact_end_date_is_met(deposited_grant):
    """2025-06-15 עצמו - התנאי הוא check_date >= end_date, כלומר היום עצמו כבר מותר."""
    met, end_date = DeterministicESOPEngine.check_trustee_holding_period(
        deposited_grant, date(2025, 6, 15)
    )
    assert met is True
    assert end_date == EXPECTED_END


def test_no_deposit_means_not_met_and_no_real_deadline(make_grant):
    """מענק שמעולם לא הופקד אצל נאמן: החסימה לא התקיימה, והתאריך המוחזר הוא
    check_date עצמו (אין תאריך סיום אמיתי לחשב ממנו)."""
    check_date = date(2026, 1, 1)
    met, returned = DeterministicESOPEngine.check_trustee_holding_period(
        make_grant(trustee_deposit_date=None), check_date
    )
    assert (met, returned) == (False, check_date)

# ===================================================================
# העוגן הסטטוטורי (v0.9.1). אומת 09/08/2026 מול שכפול הפקודה ושלושה מקורות
# מקצועיים - לא מול נוסח ראשוני. ראו הדוקסטרינג ב-engine.py.
# ===================================================================

def test_the_anchor_is_the_later_of_grant_and_deposit(make_grant):
    """102(א): "24 חודשים מיום שבו הוקצו המניות **והופקדו** בידי נאמן" - היום
    שבו התקיימו שני התנאים, ולא המוקדם מביניהם.

    ה-API אוסר היום הפקדה לפני ההענקה, ולכן הבדיקה בונה את המענק ישירות: היא
    מגינה על *הכלל המסי*, שאינו תלוי בכלל הקלט שבמקרה מסתיר אותו. ספירה מ-
    grant_date לבדו הייתה מסיימת ב-2024-01-01, כלומר מאשרת מסלול רווח הון
    כשנה וחצי מוקדם מדי - ולפי 102(ב)(4) זה מסווג את מלוא ההטבה כהכנסת עבודה.
    """
    grant = make_grant(grant_date=date(2022, 1, 1), trustee_deposit_date=date(2023, 6, 15))

    met, end_date = DeterministicESOPEngine.check_trustee_holding_period(grant, date(2024, 1, 2))

    assert end_date == date(2025, 6, 15)
    assert met is False, "ההפקדה המאוחרת היא שקובעת, ולכן ב-2024 התקופה טרם תמה"


def test_a_deposit_recorded_before_the_grant_does_not_shorten_the_period(make_grant):
    """נתונים היסטוריים מלפני תיקון ה-backdating של v0.6.0 יכולים להכיל
    הפקדה שקדמה להענקה. ``max()`` מחזיק גם שם: העוגן נשאר ההענקה, ולא
    מתקצרת התקופה בזכות רשומה שגויה."""
    grant = make_grant(grant_date=date(2023, 6, 15), trustee_deposit_date=date(2022, 1, 1))

    _, end_date = DeterministicESOPEngine.check_trustee_holding_period(grant, date(2024, 1, 1))

    assert end_date == date(2025, 6, 15)
