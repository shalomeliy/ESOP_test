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
