"""v0.7.0 שלב 1 - טבלת TaxRulePack, אילוצי הייחוד החדשים, וגיבוי חד-פעמי.

הסיכון שהבדיקות האלה סוגרות: היום אין שום דבר שמונע שתי שורות עם אותו
(country_code, grant_type, effective_start_date) ב-TaxRatesHistory/
IncomeTaxBracket - ו-tax_engine.py הקיים בוחר ביניהן עם
``.order_by(...).first()`` בלי מפתח מיון משני, כלומר בחירה לא-דטרמיניסטית.
זה נמצא בסקירת התכנון (מומחה המס) כסיכון אמיתי, לא תיאורטי בלבד.

מיפוי ל-QA_TESTBOOK.md: QA-070-01 עד QA-070-08.
"""

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from backend.app.models import IncomeTaxBracket, TaxRatesHistory, TaxRulePack
from backend.backfill_tax_rule_packs import backfill

COUNTRY = "IL"
FLAT_GRANT_TYPE = "IL_102_CAPITAL_GAINS"
PROGRESSIVE_GRANT_TYPE = "IL_102_WORK_INCOME"
SRC = "https://test.invalid/qa-fixture-not-a-real-tax-source"


# ===================================================================
# QA-070-01..02: אילוץ הייחוד על tax_rule_packs עצמה
# ===================================================================

def test_tax_rule_pack_created_with_expected_fields(db_session):
    pack = TaxRulePack(country_code=COUNTRY, grant_type=FLAT_GRANT_TYPE,
                       effective_start_date=date(2020, 1, 1),
                       calculation_method="FLAT_RATE", official_source_url=SRC)
    db_session.add(pack)
    db_session.flush()
    assert pack.pack_id is not None
    assert pack.calculation_method == "FLAT_RATE"


def test_duplicate_tax_rule_pack_same_key_is_rejected(db_session):
    db_session.add(TaxRulePack(country_code=COUNTRY, grant_type=FLAT_GRANT_TYPE,
                               effective_start_date=date(2020, 1, 1),
                               calculation_method="FLAT_RATE", official_source_url=SRC))
    db_session.flush()

    db_session.add(TaxRulePack(country_code=COUNTRY, grant_type=FLAT_GRANT_TYPE,
                               effective_start_date=date(2020, 1, 1),
                               calculation_method="FLAT_RATE", official_source_url=SRC))
    with pytest.raises(IntegrityError):
        db_session.flush()


# ===================================================================
# QA-070-03..05: אילוצי הייחוד על שתי הטבלאות הקיימות - זו בדיקת הרגרסיה
# על הפער שנמצא בתכנון (אין היום דטרמיניזם מובטח בבחירת גרסה כפולה)
# ===================================================================

def test_duplicate_tax_rates_history_same_key_is_rejected(db_session):
    db_session.add(TaxRatesHistory(tax_rule_id="TAX-1", country_code=COUNTRY,
                                   grant_type=FLAT_GRANT_TYPE, effective_start_date=date(2020, 1, 1),
                                   capital_gains_rate=0.25, official_source_url=SRC))
    db_session.flush()

    db_session.add(TaxRatesHistory(tax_rule_id="TAX-2", country_code=COUNTRY,
                                   grant_type=FLAT_GRANT_TYPE, effective_start_date=date(2020, 1, 1),
                                   capital_gains_rate=0.30, official_source_url=SRC))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_two_tax_rates_history_rows_cannot_share_one_pack_id(db_session):
    """נמצא בסקירת קוד עצמאית: היחס בין pack ל-TaxRatesHistory הוא 1:1 -
    tax_engine.py._calculate_flat משתמש ב-.first(), אז שתי שורות עם אותו
    pack_id היו נבחרות בלי סדר מובטח, בדיוק מחלקת הבאג ש-v0.7.0 סוגר."""
    pack = TaxRulePack(country_code=COUNTRY, grant_type=FLAT_GRANT_TYPE,
                       effective_start_date=date(2020, 1, 1),
                       calculation_method="FLAT_RATE", official_source_url=SRC)
    db_session.add(pack)
    db_session.flush()

    db_session.add(TaxRatesHistory(tax_rule_id="TAX-A", country_code=COUNTRY,
                                   grant_type=FLAT_GRANT_TYPE, effective_start_date=date(2020, 1, 1),
                                   capital_gains_rate=0.25, official_source_url=SRC, pack_id=pack.pack_id))
    db_session.flush()

    db_session.add(TaxRatesHistory(tax_rule_id="TAX-B", country_code=COUNTRY,
                                   grant_type=FLAT_GRANT_TYPE, effective_start_date=date(2025, 1, 1),
                                   capital_gains_rate=0.30, official_source_url=SRC, pack_id=pack.pack_id))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_duplicate_income_tax_bracket_same_key_and_order_is_rejected(db_session):
    db_session.add(IncomeTaxBracket(bracket_id="B-1", country_code=COUNTRY,
                                    grant_type=PROGRESSIVE_GRANT_TYPE, effective_start_date=date(2020, 1, 1),
                                    bracket_order=0, min_amount=0, max_amount=100, rate=0.1,
                                    official_source_url=SRC))
    db_session.flush()

    db_session.add(IncomeTaxBracket(bracket_id="B-2", country_code=COUNTRY,
                                    grant_type=PROGRESSIVE_GRANT_TYPE, effective_start_date=date(2020, 1, 1),
                                    bracket_order=0, min_amount=0, max_amount=200, rate=0.2,
                                    official_source_url=SRC))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_same_version_different_bracket_order_is_allowed(db_session):
    """גבול הבדיקה: כמה מדרגות לגיטימיות לאותה גרסה (bracket_order שונה)
    לא נחסמות ע"י האילוץ - הוא חוסם רק כפילות אמיתית."""
    db_session.add(IncomeTaxBracket(bracket_id="B-1", country_code=COUNTRY,
                                    grant_type=PROGRESSIVE_GRANT_TYPE, effective_start_date=date(2020, 1, 1),
                                    bracket_order=0, min_amount=0, max_amount=100, rate=0.1,
                                    official_source_url=SRC))
    db_session.add(IncomeTaxBracket(bracket_id="B-2", country_code=COUNTRY,
                                    grant_type=PROGRESSIVE_GRANT_TYPE, effective_start_date=date(2020, 1, 1),
                                    bracket_order=1, min_amount=100, max_amount=None, rate=0.2,
                                    official_source_url=SRC))
    db_session.flush()  # לא אמור לזרוק


# ===================================================================
# QA-070-06..08: גיבוי חד-פעמי (backfill_tax_rule_packs.py)
# ===================================================================

def test_backfill_creates_one_pack_per_flat_rate_version(db_session):
    db_session.add_all([
        TaxRatesHistory(tax_rule_id="TAX-2020", country_code=COUNTRY, grant_type=FLAT_GRANT_TYPE,
                        effective_start_date=date(2020, 1, 1), capital_gains_rate=0.25,
                        official_source_url=SRC),
        TaxRatesHistory(tax_rule_id="TAX-2025", country_code=COUNTRY, grant_type=FLAT_GRANT_TYPE,
                        effective_start_date=date(2025, 1, 1), capital_gains_rate=0.28,
                        official_source_url=SRC),
    ])
    db_session.flush()

    counts = backfill(db_session)
    assert counts["flat_packs"] == 2
    assert counts["flat_rows_linked"] == 2

    row_2020 = db_session.query(TaxRatesHistory).filter_by(tax_rule_id="TAX-2020").first()
    pack_2020 = db_session.query(TaxRulePack).filter_by(pack_id=row_2020.pack_id).first()
    assert pack_2020.calculation_method == "FLAT_RATE"
    assert pack_2020.effective_start_date == date(2020, 1, 1)


def test_backfill_creates_one_pack_per_progressive_version_shared_across_brackets(db_session):
    """כל המדרגות של אותה גרסה (אותו תאריך תוקף) חייבות להצביע לאותו pack_id -
    לא pack נפרד לכל מדרגה."""
    for order, min_amt, max_amt, rate in [(0, 0, 100, 0.1), (1, 100, None, 0.2)]:
        db_session.add(IncomeTaxBracket(
            bracket_id=f"B-{order}", country_code=COUNTRY, grant_type=PROGRESSIVE_GRANT_TYPE,
            effective_start_date=date(2020, 1, 1), bracket_order=order,
            min_amount=min_amt, max_amount=max_amt, rate=rate, official_source_url=SRC))
    db_session.flush()

    counts = backfill(db_session)
    assert counts["progressive_packs"] == 1
    assert counts["bracket_rows_linked"] == 2

    rows = db_session.query(IncomeTaxBracket).all()
    assert rows[0].pack_id == rows[1].pack_id
    pack = db_session.query(TaxRulePack).filter_by(pack_id=rows[0].pack_id).first()
    assert pack.calculation_method == "PROGRESSIVE_BRACKETS"


def test_backfill_uses_bracket_order_zero_source_url_as_the_packs_source(db_session):
    """הכרעה מפורשת מהתכנון: אם שורות המדרגות של אותה גרסה חלוקות ב-source_url
    (מצב לא-עקבי שקיים תיאורטית בדאטה היום), הגיבוי לוקח את זה של
    bracket_order=0 - בדיוק מה ש-tax_engine.py הקיים כבר קורא, לא ניסיון
    "לתקן" חוסר עקביות בשקט."""
    db_session.add_all([
        IncomeTaxBracket(bracket_id="B-0", country_code=COUNTRY, grant_type=PROGRESSIVE_GRANT_TYPE,
                         effective_start_date=date(2020, 1, 1), bracket_order=0,
                         min_amount=0, max_amount=100, rate=0.1, official_source_url="SOURCE-A"),
        IncomeTaxBracket(bracket_id="B-1", country_code=COUNTRY, grant_type=PROGRESSIVE_GRANT_TYPE,
                         effective_start_date=date(2020, 1, 1), bracket_order=1,
                         min_amount=100, max_amount=None, rate=0.2, official_source_url="SOURCE-B"),
    ])
    db_session.flush()

    backfill(db_session)
    pack = db_session.query(TaxRulePack).first()
    assert pack.official_source_url == "SOURCE-A"
