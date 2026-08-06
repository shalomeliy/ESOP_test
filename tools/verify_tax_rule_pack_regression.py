"""חד-פעמי, לא בתוך pytest בכוונה: בדיקת רגרסיה שמשווה את בחירת גרסת כלל המס
*הישנה* (הלוגיקה שהייתה קיימת ב-tax_engine.py לפני v0.7.0, משוחזרת כאן ידנית -
לא נקראת מהקוד החי, כי הקוד החי כבר לא מכיל אותה) מול הבחירה *החדשה*
(TaxRulePack), על כל שילוב (מדינה, סוג מענק, תאריך) שקיים בפועל בדאטה הזרוע.

למה סקריפט נפרד ולא test רגיל: זו לא בדיקת יחידה על דאטה מומצא - זו הוכחה
אמפירית שהמעבר ל-TaxRulePack לא שינה אף תוצאה עבור אף מענק אמיתי שכבר קיים.
מומלץ להריץ מחדש בכל פעם ש-tax_engine.py או backfill_tax_rule_packs.py משתנים
מהותית, מול עותק טרי של esop_database.db - **אף פעם לא מול הקובץ עצמו**.

שימוש:
    cp esop_database.db esop_database.regression_check.db
    ESOP_DATABASE_URL="sqlite:///./esop_database.regression_check.db" \
        python -m alembic upgrade head
    ESOP_DATABASE_URL="sqlite:///./esop_database.regression_check.db" \
        python -m backend.backfill_tax_rule_packs
    ESOP_DATABASE_URL="sqlite:///./esop_database.regression_check.db" \
        python -m tools.verify_tax_rule_pack_regression
    rm esop_database.regression_check.db*

נמצא: הרצה אחרונה (2026-08-06, מול עותק מלא של esop_database.db באותו מועד) -
82 שילובים ייחודיים, 246 בדיקות (3 תאריכי בדיקה על כל שילוב), 0 אי-התאמות,
36 מקרים ששתי הגרסאות מסכימות "אין כלל שחל". ראו QA_TESTBOOK.md QA-070-19.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from backend.app.database import SessionLocal
from backend.app.models import Grant, Employee, TaxRatesHistory, IncomeTaxBracket
from backend.app.services.tax_engine import TaxCalculationEngine, MissingTaxRuleError


def _old_style_version_lookup(db, country: str, grant_type: str, probe_date: date):
    """בדיוק אותה צורת שאילתה שהייתה ב-tax_engine.py *לפני* v0.7.0 - הכי-עדכני
    שחל, ישירות מול טבלאות הפירוט, בלי TaxRulePack באמצע."""
    if grant_type == "IL_102_WORK_INCOME":
        row = (
            db.query(IncomeTaxBracket.effective_start_date)
            .filter(IncomeTaxBracket.grant_type == grant_type,
                    IncomeTaxBracket.country_code == country,
                    IncomeTaxBracket.effective_start_date <= probe_date)
            .order_by(IncomeTaxBracket.effective_start_date.desc())
            .first()
        )
    else:
        row = (
            db.query(TaxRatesHistory.effective_start_date)
            .filter(TaxRatesHistory.grant_type == grant_type,
                    TaxRatesHistory.country_code == country,
                    TaxRatesHistory.effective_start_date <= probe_date)
            .order_by(TaxRatesHistory.effective_start_date.desc())
            .first()
        )
    return row[0] if row else None


def run(db) -> dict:
    combos = set()
    for grant in db.query(Grant).all():
        emp = db.query(Employee).filter(Employee.employee_id == grant.employee_id).first()
        if not emp:
            continue
        gt = grant.grant_type.value if hasattr(grant.grant_type, "value") else grant.grant_type
        combos.add((emp.country_code, gt, grant.grant_date))

    print(f"{len(combos)} distinct (country, grant_type, grant_date) combos from real grants")

    stats = {"checked": 0, "mismatches": 0, "never_modeled_misses": 0, "no_rule_for_date_misses": 0}

    for country, grant_type, grant_date_val in combos:
        for probe_date in (grant_date_val, grant_date_val + timedelta(days=30), date.today()):
            stats["checked"] += 1
            old_date = _old_style_version_lookup(db, country, grant_type, probe_date)

            try:
                result = TaxCalculationEngine.calculate_tax(db, country, grant_type, probe_date, 100000.0)
                new_date = result.table_effective_date
            except MissingTaxRuleError as e:
                new_date = None
                if e.reason == MissingTaxRuleError.NEVER_MODELED:
                    stats["never_modeled_misses"] += 1
                else:
                    stats["no_rule_for_date_misses"] += 1

            if old_date != new_date:
                stats["mismatches"] += 1
                print(f"MISMATCH: {country}/{grant_type} @ {probe_date}: old={old_date} new={new_date}")

    return stats


def main():
    db = SessionLocal()
    try:
        stats = run(db)
        print(f"\n{stats}")
        if stats["mismatches"]:
            print("FAILED - see MISMATCH lines above")
            sys.exit(1)
        print("OK - old and new rule-version selection agree on every real combination")
    finally:
        db.close()


if __name__ == "__main__":
    main()
