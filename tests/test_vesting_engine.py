"""DeterministicESOPEngine.calculate_vested_options - גבולות תאריכים.

הסיכון האמיתי כאן הוא לא "הפונקציה מחזירה מספר" אלא הקצוות: יום לפני ה-cliff,
יום ה-cliff עצמו, אמצע הלוח, וסיום מלא. טעות של יום אחד בקצה = כסף אמיתי.
"""

from datetime import date

import pytest

from backend.app.services.engine import DeterministicESOPEngine

# תרחיס הבסיס לכל הקובץ: 4800 אופציות, תחילת הבשלה 2022-01-01,
# cliff של 12 חודשים, 48 חודשים בסך הכל => 100 אופציות לחודש.
START = date(2022, 1, 1)
TOTAL_OPTIONS = 4800.0
CLIFF_MONTHS = 12
TOTAL_MONTHS = 48
PER_MONTH = TOTAL_OPTIONS / TOTAL_MONTHS  # 100.0


@pytest.fixture
def grant(make_grant):
    return make_grant(total_options=TOTAL_OPTIONS, grant_date=START)


@pytest.fixture
def schedule(make_schedule):
    return make_schedule(start_date=START, cliff_months=CLIFF_MONTHS,
                         total_months=TOTAL_MONTHS, paused_days_total=0)


@pytest.mark.parametrize(
    "target_date, expected, worked_example",
    [
        (
            date(2022, 12, 31),
            0.0,
            "יום אחד לפני ה-cliff (2023-01-01). לפי סעיף 102 וכל תוכנית אופציות "
            "רגילה - לפני ה-cliff לא הבשילה אף אופציה: 0.",
        ),
        (
            date(2023, 1, 1),
            1200.0,
            "יום ה-cliff עצמו. months_passed = (2023-2022)*12 + (1-1) = 12. "
            "4800/48 = 100 לחודש; 100 * 12 = 1200.",
        ),
        (
            date(2025, 7, 1),
            4200.0,
            "אמצע הלוח. months_passed = (2025-2022)*12 + (7-1) = 36 + 6 = 42. "
            "100 * 42 = 4200.",
        ),
        (
            date(2026, 1, 1),
            4800.0,
            "סיום מלא. months_passed = (2026-2022)*12 + (1-1) = 48 >= total_months=48, "
            "ולכן כל 4800 האופציות הבשילו (ולא 100*48 מעוגל - הענף מחזיר total_options).",
        ),
    ],
    ids=["day-before-cliff", "cliff-day", "mid-schedule", "fully-vested"],
)
def test_vesting_at_date_boundaries(grant, schedule, target_date, expected, worked_example):
    """חישוב ידני מתועד בכל מקרה בפרמטרים (worked_example)."""
    result = DeterministicESOPEngine.calculate_vested_options(grant, schedule, target_date)
    assert result == expected, worked_example


def test_vesting_never_exceeds_total_after_schedule_end(grant, schedule):
    """הרבה אחרי סוף הלוח עדיין לא עוברים את total_options - אינווריאנט בסיסי."""
    assert DeterministicESOPEngine.calculate_vested_options(
        grant, schedule, date(2035, 1, 1)
    ) == TOTAL_OPTIONS


def test_paused_days_push_the_cliff_past_the_original_cliff_date(grant, make_schedule):
    """חופשה ללא תשלום / הקפאה: paused_days_total=30.

    חישוב ידני: adjusted_start = 2022-01-01 + 30 ימים = 2022-01-31.
    cliff_date = date(2022 + 12//12, 1, 31) = 2023-01-31.
    בתאריך 2023-01-15 עוד לא הגענו ל-cliff המוזז -> 0.0,
    למרות שבלוח ללא הקפאה כבר היו מבשילות 1200 אופציות ב-2023-01-01.
    """
    paused = make_schedule(start_date=START, cliff_months=CLIFF_MONTHS,
                           total_months=TOTAL_MONTHS, paused_days_total=30)

    assert DeterministicESOPEngine.calculate_vested_options(
        grant, paused, date(2023, 1, 15)
    ) == 0.0

    # ולראיה שההזזה היא הסיבה, ולא משהו אחר: הלוח הלא-מוקפא כן מבשיל באותו תאריך.
    unpaused = make_schedule(start_date=START, cliff_months=CLIFF_MONTHS,
                             total_months=TOTAL_MONTHS, paused_days_total=0)
    assert DeterministicESOPEngine.calculate_vested_options(
        grant, unpaused, date(2023, 1, 15)
    ) == 1200.0
