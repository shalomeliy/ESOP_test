"""TaxCalculationEngine - בחירת גרסת טבלת מס לפי תאריך המימוש, וחישוב הסכום.

הסיכון: מימוש שמתבצע היום על אירוע מס משנה קודמת חייב להשתמש בטבלה שהייתה
בתוקף *בתאריך המימוש*, לא בטבלה העדכנית. בחירת גרסה שגויה = סכום מס שגוי.

*** כל השיעורים כאן הם נתוני בדיקה מומצאים (ראו CLAUDE.md: הטבלאות במערכת הן
דמו לתרגול QA ולא חוק מס אמיתי). הבדיקה מאמתת את *המנוע*, לא את חוק המס. ***

הערה על השוואת סכומים: המנוע מחזיר float גולמי בלי עיגול לאגורות
(``gain * rate``), ולכן 100000 * 0.28 מחזיר 28000.000000000004 ולא 28000.0.
זהו ממצא אמיתי שדווח בנפרד (``calculate_vested_options`` כן מעגל ל-2 ספרות,
מנוע המס לא). הבדיקות משוות בדיוק של אגורה - הדרישה העסקית האמיתית - ולא
בשוויון float ביט-אחר-ביט, ש-IEEE754 לא מבטיח לאף אחד.
"""

from datetime import date

import pytest

from backend.app.models import IncomeTaxBracket, TaxRatesHistory
from backend.app.services.tax_engine import TaxCalculationEngine

COUNTRY = "IL"
FLAT_GRANT_TYPE = "IL_102_CAPITAL_GAINS"
PROGRESSIVE_GRANT_TYPE = "IL_102_WORK_INCOME"
SRC = "https://test.invalid/qa-fixture-not-a-real-tax-source"


def agorot(amount: float) -> float:
    """עיגול לאגורה - יחידת הכסף הקטנה ביותר שהמערכת יכולה להציג/לגבות."""
    return round(amount, 2)


@pytest.fixture
def flat_rates(db_session):
    """שתי גרסאות של שיעור שטוח: 25% מ-2020-01-01, 28% מ-2024-01-01."""
    db_session.add_all([
        TaxRatesHistory(tax_rule_id="TAXRULE-TEST-2020", country_code=COUNTRY,
                        grant_type=FLAT_GRANT_TYPE, effective_start_date=date(2020, 1, 1),
                        capital_gains_rate=0.25, official_source_url=SRC),
        TaxRatesHistory(tax_rule_id="TAXRULE-TEST-2024", country_code=COUNTRY,
                        grant_type=FLAT_GRANT_TYPE, effective_start_date=date(2024, 1, 1),
                        capital_gains_rate=0.28, official_source_url=SRC),
    ])
    db_session.flush()
    return db_session


@pytest.fixture
def progressive_brackets(db_session):
    """גרסת מדרגות אחת מ-2020-01-01: 0-10,000 @10% | 10,000-30,000 @20% | 30,000+ @45%."""
    db_session.add_all([
        IncomeTaxBracket(bracket_id="BR-TEST-1", country_code=COUNTRY,
                         grant_type=PROGRESSIVE_GRANT_TYPE,
                         effective_start_date=date(2020, 1, 1), bracket_order=1,
                         min_amount=0.0, max_amount=10000.0, rate=0.10,
                         official_source_url=SRC),
        IncomeTaxBracket(bracket_id="BR-TEST-2", country_code=COUNTRY,
                         grant_type=PROGRESSIVE_GRANT_TYPE,
                         effective_start_date=date(2020, 1, 1), bracket_order=2,
                         min_amount=10000.0, max_amount=30000.0, rate=0.20,
                         official_source_url=SRC),
        IncomeTaxBracket(bracket_id="BR-TEST-3", country_code=COUNTRY,
                         grant_type=PROGRESSIVE_GRANT_TYPE,
                         effective_start_date=date(2020, 1, 1), bracket_order=3,
                         min_amount=30000.0, max_amount=None, rate=0.45,
                         official_source_url=SRC),
    ])
    db_session.flush()
    return db_session


# ---------------------------------------------------------------- flat rate

def test_flat_rate_uses_the_version_in_force_on_the_exercise_date(flat_rates):
    """מימוש ב-2023-06-01, רווח 100,000.
    חישוב ידני: הגרסה שהייתה בתוקף אז היא זו של 2020-01-01 (25%), *לא* של 2024.
    100,000 * 0.25 = 25,000.
    """
    result = TaxCalculationEngine.calculate_tax(
        flat_rates, COUNTRY, FLAT_GRANT_TYPE, date(2023, 6, 1), 100000.0
    )
    assert result.method == "FLAT_RATE"
    assert agorot(result.tax_amount) == 25000.00
    assert result.effective_rate == 0.25
    assert result.table_effective_date == date(2020, 1, 1)


def test_flat_rate_switches_to_the_newer_version_after_it_takes_effect(flat_rates):
    """אותו רווח, מימוש ב-2024-06-01.
    חישוב ידני: כעת בתוקף גרסת 2024-01-01 (28%). 100,000 * 0.28 = 28,000.
    """
    result = TaxCalculationEngine.calculate_tax(
        flat_rates, COUNTRY, FLAT_GRANT_TYPE, date(2024, 6, 1), 100000.0
    )
    assert result.method == "FLAT_RATE"
    # 28000.000000000004 גולמי - ראו הערת ה-float ב-docstring של המודול.
    assert agorot(result.tax_amount) == 28000.00
    assert result.effective_rate == 0.28
    assert result.table_effective_date == date(2024, 1, 1)


def test_flat_rate_falls_back_visibly_when_no_rule_exists(db_session):
    """טבלה ריקה: המנוע לא קורס אלא מחזיר fallback *מסומן* (method עם סיומת
    _FALLBACK ו-table_effective_date=None), כדי שהמספר לא ייראה כמו חישוב תקין.
    חישוב ידני: 100,000 * 0.25 (שיעור רשת הביטחון) = 25,000.
    """
    result = TaxCalculationEngine.calculate_tax(
        db_session, COUNTRY, FLAT_GRANT_TYPE, date(2023, 6, 1), 100000.0
    )
    assert result.method == "FLAT_RATE_FALLBACK"
    assert result.effective_rate == 0.25
    assert agorot(result.tax_amount) == 25000.00
    assert result.table_effective_date is None


# -------------------------------------------------------------- progressive

def test_progressive_brackets_sum_correctly(progressive_brackets):
    """IL_102_WORK_INCOME ממוסה כהכנסת עבודה - מדרגות, לא שיעור שטוח.

    חישוב ידני על רווח של 50,000:
      מדרגה 1:      0 -> 10,000 = 10,000 @ 10%  =  1,000
      מדרגה 2: 10,000 -> 30,000 = 20,000 @ 20%  =  4,000
      מדרגה 3: 30,000 -> ומעלה  = 20,000 @ 45%  =  9,000   (50,000 - 30,000)
                                          סה"כ  = 14,000
      שיעור אפקטיבי = 14,000 / 50,000 = 0.28
    שימו לב שהשיעור האפקטיבי (28%) שווה במקרה לשיעור השטוח של 2024 - זו מקריות
    של המספרים בבדיקה, לא קשר אמיתי.
    """
    result = TaxCalculationEngine.calculate_tax(
        progressive_brackets, COUNTRY, PROGRESSIVE_GRANT_TYPE, date(2025, 3, 1), 50000.0
    )
    assert result.method == "PROGRESSIVE_BRACKETS"
    assert agorot(result.tax_amount) == 14000.00
    assert agorot(result.effective_rate) == 0.28
    assert result.table_effective_date == date(2020, 1, 1)


def test_progressive_zero_gain_is_zero_tax_and_no_division_by_zero(progressive_brackets):
    """רווח 0 (מימוש במחיר המימוש בדיוק): 0 מס, ושיעור אפקטיבי 0.0 -
    לא ZeroDivisionError ולא NaN.
    חישוב ידני: 0 @ 10% = 0; לא נותר דבר למדרגות הבאות.
    """
    result = TaxCalculationEngine.calculate_tax(
        progressive_brackets, COUNTRY, PROGRESSIVE_GRANT_TYPE, date(2025, 3, 1), 0.0
    )
    assert agorot(result.tax_amount) == 0.00
    assert result.effective_rate == 0.0


def test_progressive_grant_type_never_falls_through_to_flat(progressive_brackets, flat_rates):
    """גם כששיעור שטוח קיים ב-DB, IL_102_WORK_INCOME חייב ללכת למסלול המדרגות.
    אילו היה נופל למסלול השטוח, 50,000 * 0.28 היה נותן גם כן 14,000 - ולכן
    הבדיקה מסתמכת על method ולא רק על הסכום.
    """
    result = TaxCalculationEngine.calculate_tax(
        progressive_brackets, COUNTRY, PROGRESSIVE_GRANT_TYPE, date(2025, 3, 1), 50000.0
    )
    assert result.method == "PROGRESSIVE_BRACKETS"
