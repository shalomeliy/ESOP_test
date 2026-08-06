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

v0.7.0: שיטת החישוב כבר לא if קשיח - נקראת מ-TaxRulePack.calculation_method
(ראו tax_engine.py). לכן כל fixture כאן יוצרת גם TaxRulePack, לא רק את שורות
הפירוט - בדיוק כמו backfill_tax_rule_packs.py/seed_data.py המעודכנים.
"""

from datetime import date

import pytest

from backend.app.models import IncomeTaxBracket, TaxRatesHistory, TaxRulePack
from backend.app.services.tax_engine import TaxCalculationEngine, MissingTaxRuleError

COUNTRY = "IL"
FLAT_GRANT_TYPE = "IL_102_CAPITAL_GAINS"
PROGRESSIVE_GRANT_TYPE = "IL_102_WORK_INCOME"
SRC = "https://test.invalid/qa-fixture-not-a-real-tax-source"


def agorot(amount: float) -> float:
    """עיגול לאגורה - יחידת הכסף הקטנה ביותר שהמערכת יכולה להציג/לגבות."""
    return round(amount, 2)


def _pack(db, grant_type, eff_date, method):
    pack = TaxRulePack(country_code=COUNTRY, grant_type=grant_type, effective_start_date=eff_date,
                       calculation_method=method, official_source_url=SRC)
    db.add(pack)
    db.flush()
    return pack.pack_id


@pytest.fixture
def flat_rates(db_session):
    """שתי גרסאות של שיעור שטוח: 25% מ-2020-01-01, 28% מ-2024-01-01."""
    pack_2020 = _pack(db_session, FLAT_GRANT_TYPE, date(2020, 1, 1), "FLAT_RATE")
    pack_2024 = _pack(db_session, FLAT_GRANT_TYPE, date(2024, 1, 1), "FLAT_RATE")
    db_session.add_all([
        TaxRatesHistory(tax_rule_id="TAXRULE-TEST-2020", country_code=COUNTRY,
                        grant_type=FLAT_GRANT_TYPE, effective_start_date=date(2020, 1, 1),
                        capital_gains_rate=0.25, official_source_url=SRC, pack_id=pack_2020),
        TaxRatesHistory(tax_rule_id="TAXRULE-TEST-2024", country_code=COUNTRY,
                        grant_type=FLAT_GRANT_TYPE, effective_start_date=date(2024, 1, 1),
                        capital_gains_rate=0.28, official_source_url=SRC, pack_id=pack_2024),
    ])
    db_session.flush()
    return db_session


@pytest.fixture
def progressive_brackets(db_session):
    """גרסת מדרגות אחת מ-2020-01-01: 0-10,000 @10% | 10,000-30,000 @20% | 30,000+ @45%."""
    pack_id = _pack(db_session, PROGRESSIVE_GRANT_TYPE, date(2020, 1, 1), "PROGRESSIVE_BRACKETS")
    db_session.add_all([
        IncomeTaxBracket(bracket_id="BR-TEST-1", country_code=COUNTRY,
                         grant_type=PROGRESSIVE_GRANT_TYPE,
                         effective_start_date=date(2020, 1, 1), bracket_order=1,
                         min_amount=0.0, max_amount=10000.0, rate=0.10,
                         official_source_url=SRC, pack_id=pack_id),
        IncomeTaxBracket(bracket_id="BR-TEST-2", country_code=COUNTRY,
                         grant_type=PROGRESSIVE_GRANT_TYPE,
                         effective_start_date=date(2020, 1, 1), bracket_order=2,
                         min_amount=10000.0, max_amount=30000.0, rate=0.20,
                         official_source_url=SRC, pack_id=pack_id),
        IncomeTaxBracket(bracket_id="BR-TEST-3", country_code=COUNTRY,
                         grant_type=PROGRESSIVE_GRANT_TYPE,
                         effective_start_date=date(2020, 1, 1), bracket_order=3,
                         min_amount=30000.0, max_amount=None, rate=0.45,
                         official_source_url=SRC, pack_id=pack_id),
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
    assert result.pack_id is not None


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


# ===================================================================
# v0.7.0: כשל מפורש במקום fallback שקט. הטסט הישן
# (test_flat_rate_falls_back_visibly_when_no_rule_exists) הוחלף בכוונה -
# זו בדיוק ההחלטה המתוכננת של הגרסה הזו, לא היחלשות בדיקה.
# ===================================================================

def test_never_modeled_combination_raises_with_that_reason(db_session):
    """שילוב (מדינה, סוג מענק) שאין לו אף חבילת כלל בכלל - לא רק "לא ב-DB
    בתאריך הזה", אלא מעולם לא נבנה."""
    with pytest.raises(MissingTaxRuleError) as exc_info:
        TaxCalculationEngine.calculate_tax(
            db_session, COUNTRY, FLAT_GRANT_TYPE, date(2023, 6, 1), 100000.0
        )
    assert exc_info.value.reason == MissingTaxRuleError.NEVER_MODELED
    assert exc_info.value.country_code == COUNTRY
    assert exc_info.value.grant_type == FLAT_GRANT_TYPE


def test_exercise_date_before_earliest_rule_raises_with_that_reason(flat_rates):
    """השילוב כן קיים (יש חבילות מ-2020 ומ-2024), אבל תאריך המימוש קודם לכל
    גרסה שקיימת - פער תאריך, לא "מעולם לא נבנה"."""
    with pytest.raises(MissingTaxRuleError) as exc_info:
        TaxCalculationEngine.calculate_tax(
            flat_rates, COUNTRY, FLAT_GRANT_TYPE, date(2019, 12, 31), 100000.0
        )
    assert exc_info.value.reason == MissingTaxRuleError.NO_RULE_EFFECTIVE_AS_OF_DATE


def test_invalid_calculation_method_raises_with_that_reason(db_session):
    """calculation_method הוא String חופשי (לא נאכף ב-DB, כמו LEDGER_EVENT_TYPES) -
    ערך משובש חייב להיתפס בזמן הקריאה, לא ליפול בשקט למסלול השטוח כברירת מחדל."""
    db_session.add(TaxRulePack(country_code=COUNTRY, grant_type=FLAT_GRANT_TYPE,
                               effective_start_date=date(2020, 1, 1),
                               calculation_method="SOMETHING_ELSE", official_source_url=SRC))
    db_session.flush()
    with pytest.raises(MissingTaxRuleError) as exc_info:
        TaxCalculationEngine.calculate_tax(
            db_session, COUNTRY, FLAT_GRANT_TYPE, date(2023, 6, 1), 100000.0
        )
    assert exc_info.value.reason == MissingTaxRuleError.INVALID_CALCULATION_METHOD


def test_pack_with_no_matching_flat_rate_row_raises_data_integrity_reason(db_session):
    """חבילה קיימת (נניח נוצרה בטעות בלי לינוק אליה שורת TaxRatesHistory
    תואמת) - זה לא "אין כלל", זה חוסר עקביות פנימי בדאטה. לא אמור לקרות
    בפועל (seed/backfill תמיד יוצרים את שתיהן יחד), אבל המנוע לא אמור
    להתרסק עם AttributeError על None אם זה בכל זאת יקרה."""
    pack_id = _pack(db_session, FLAT_GRANT_TYPE, date(2020, 1, 1), "FLAT_RATE")
    with pytest.raises(MissingTaxRuleError) as exc_info:
        TaxCalculationEngine.calculate_tax(
            db_session, COUNTRY, FLAT_GRANT_TYPE, date(2023, 6, 1), 100000.0
        )
    assert exc_info.value.reason == MissingTaxRuleError.PACK_HAS_NO_DETAIL_ROWS


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


def test_pack_with_no_matching_brackets_raises_data_integrity_reason(db_session):
    """כמו הגרסה השטוחה: חבילה מדורגת קיימת אבל בלי אף שורת מדרגה מקושרת."""
    _pack(db_session, PROGRESSIVE_GRANT_TYPE, date(2020, 1, 1), "PROGRESSIVE_BRACKETS")
    with pytest.raises(MissingTaxRuleError) as exc_info:
        TaxCalculationEngine.calculate_tax(
            db_session, COUNTRY, PROGRESSIVE_GRANT_TYPE, date(2025, 3, 1), 50000.0
        )
    assert exc_info.value.reason == MissingTaxRuleError.PACK_HAS_NO_DETAIL_ROWS
