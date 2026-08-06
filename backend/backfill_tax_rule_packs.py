"""חד-פעמי: מגבה (backfill) שורות TaxRulePack עבור שילובי (מדינה, סוג מענק,
תאריך תוקף) שכבר קיימים ב-TaxRatesHistory/IncomeTaxBracket, ומקשר אליהן.

מריצים פעם אחת, אחרי המיגרציה שיוצרת את tax_rule_packs ואת pack_id הנוסף
בשתי הטבלאות הקיימות. לא עושה drop_all/create_all - זה סקריפט תוספתי על
סכמה קיימת עם דאטה אמיתי (אם קיים), לא בנייה מאפס. ראו FEATURE_SPEC.md v0.7.0
ו-QA_TESTBOOK.md.

*** לא לרוץ פעמיים על אותו DB *** - הסקריפט בודק זאת ומסרב אם כבר יש שורות
tax_rule_packs, כדי לא ליצור כפילויות (ואת ה-UniqueConstraint על השלישייה
היה שובר בכל מקרה בניסיון שני).

הכרעה מפורשת (לא שקטה): IncomeTaxBracket עלול תיאורטית להכיל official_source_url
לא-אחיד בין שורות המדרגות של אותה גרסה. tax_engine.py הקיים תמיד קורא רק את
bracket_order=0 (ראו _calculate_progressive) - הגיבוי משתמש באותה שורה בדיוק
כמקור לחבילה, כדי לא לשנות התנהגות קיימת, לא כדי "לתקן" חוסר עקביות שאולי קיים.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from backend.app.database import SessionLocal
from backend.app.models import TaxRatesHistory, IncomeTaxBracket, TaxRulePack


def backfill(db) -> dict:
    counts = {"flat_packs": 0, "progressive_packs": 0, "flat_rows_linked": 0, "bracket_rows_linked": 0}

    flat_keys = (
        db.query(TaxRatesHistory.country_code, TaxRatesHistory.grant_type, TaxRatesHistory.effective_start_date)
        .distinct()
        .all()
    )
    for country_code, grant_type, eff_date in flat_keys:
        rows = (
            db.query(TaxRatesHistory)
            .filter(TaxRatesHistory.country_code == country_code,
                    TaxRatesHistory.grant_type == grant_type,
                    TaxRatesHistory.effective_start_date == eff_date)
            .all()
        )
        pack = TaxRulePack(
            country_code=country_code, grant_type=grant_type, effective_start_date=eff_date,
            calculation_method="FLAT_RATE", official_source_url=rows[0].official_source_url,
        )
        db.add(pack)
        db.flush()
        counts["flat_packs"] += 1
        for row in rows:
            row.pack_id = pack.pack_id
            counts["flat_rows_linked"] += 1

    bracket_keys = (
        db.query(IncomeTaxBracket.country_code, IncomeTaxBracket.grant_type, IncomeTaxBracket.effective_start_date)
        .distinct()
        .all()
    )
    for country_code, grant_type, eff_date in bracket_keys:
        rows = (
            db.query(IncomeTaxBracket)
            .filter(IncomeTaxBracket.country_code == country_code,
                    IncomeTaxBracket.grant_type == grant_type,
                    IncomeTaxBracket.effective_start_date == eff_date)
            .order_by(IncomeTaxBracket.bracket_order)
            .all()
        )
        pack = TaxRulePack(
            country_code=country_code, grant_type=grant_type, effective_start_date=eff_date,
            calculation_method="PROGRESSIVE_BRACKETS", official_source_url=rows[0].official_source_url,
        )
        db.add(pack)
        db.flush()
        counts["progressive_packs"] += 1
        for row in rows:
            row.pack_id = pack.pack_id
            counts["bracket_rows_linked"] += 1

    return counts


def main():
    db = SessionLocal()
    try:
        already_ran = db.query(TaxRulePack).first()
        if already_ran:
            print("⛔ כבר קיימות שורות tax_rule_packs ב-DB הזה - לא רץ שוב (מונע כפילויות).")
            return

        print("🧾 מתחיל גיבוי ל-tax_rule_packs...")
        counts = backfill(db)
        db.commit()
        print(f"✅ הושלם: {counts}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
