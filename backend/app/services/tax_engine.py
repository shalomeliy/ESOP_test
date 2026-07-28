from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from backend.app.models import TaxRatesHistory, IncomeTaxBracket

# רשת ביטחון בלבד - לא אמור לקרות בפועל אחרי שהטבלאות מאוכלסות. אם זה כן קורה
# (טבלה ריקה/חסרה שילוב מדינה+סוג), עדיף חישוב גס וגלוי מאשר קריסה.
_FALLBACK_RATE = 0.25


@dataclass
class TaxCalculationResult:
    method: str  # "FLAT_RATE" | "PROGRESSIVE_BRACKETS" | "*_FALLBACK"
    tax_amount: float
    effective_rate: float
    table_effective_date: Optional[date]
    source_url: str


class TaxCalculationEngine:
    """חישוב מס דטרמיניסטי, versioned לפי תאריך - כמו DeterministicESOPEngine
    להבשלה, אבל לצד המיסוי. *** כל הנתונים המוזנים היום הם דמו לתרגול QA
    (ראו official_source_url), לא חוק מס אמיתי - ראו CLAUDE.md. ***"""

    @staticmethod
    def calculate_tax(db: Session, country_code: str, grant_type: str,
                       exercise_date: date, gain: float) -> TaxCalculationResult:
        if grant_type == "IL_102_WORK_INCOME":
            return TaxCalculationEngine._calculate_progressive(db, country_code, grant_type, exercise_date, gain)
        return TaxCalculationEngine._calculate_flat(db, country_code, grant_type, exercise_date, gain)

    @staticmethod
    def _calculate_flat(db: Session, country_code: str, grant_type: str,
                         exercise_date: date, gain: float) -> TaxCalculationResult:
        rule = (
            db.query(TaxRatesHistory)
            .filter(
                TaxRatesHistory.grant_type == grant_type,
                TaxRatesHistory.country_code == country_code,
                TaxRatesHistory.effective_start_date <= exercise_date,
            )
            .order_by(TaxRatesHistory.effective_start_date.desc())
            .first()
        )
        if not rule:
            return TaxCalculationResult("FLAT_RATE_FALLBACK", gain * _FALLBACK_RATE, _FALLBACK_RATE,
                                         None, "NO_TAX_RULE_FOUND_FALLBACK")

        return TaxCalculationResult("FLAT_RATE", gain * rule.capital_gains_rate, rule.capital_gains_rate,
                                     rule.effective_start_date, rule.official_source_url)

    @staticmethod
    def _calculate_progressive(db: Session, country_code: str, grant_type: str,
                                exercise_date: date, gain: float) -> TaxCalculationResult:
        # שלב 1: איתור גרסת טבלת המדרגות העדכנית שכבר הייתה בתוקף בתאריך המימוש
        # (בדיוק אותו רעיון כמו ה-flat rate, רק שכאן "גרסה" = קבוצת שורות).
        latest_version = (
            db.query(IncomeTaxBracket.effective_start_date)
            .filter(
                IncomeTaxBracket.grant_type == grant_type,
                IncomeTaxBracket.country_code == country_code,
                IncomeTaxBracket.effective_start_date <= exercise_date,
            )
            .order_by(IncomeTaxBracket.effective_start_date.desc())
            .first()
        )
        if not latest_version:
            return TaxCalculationResult("PROGRESSIVE_FALLBACK", gain * _FALLBACK_RATE, _FALLBACK_RATE,
                                         None, "NO_TAX_BRACKETS_FOUND_FALLBACK")
        version_date = latest_version[0]

        brackets = (
            db.query(IncomeTaxBracket)
            .filter(
                IncomeTaxBracket.grant_type == grant_type,
                IncomeTaxBracket.country_code == country_code,
                IncomeTaxBracket.effective_start_date == version_date,
            )
            .order_by(IncomeTaxBracket.bracket_order)
            .all()
        )

        # שלב 2: החישוב עצמו מתייחס ל-gain כאל כל ההכנסה החייבת (לא נערם על
        # משכורת/הכנסה אחרת - המערכת הזו לא עוקבת אחר הכנסות אחרות של העובד).
        # זו הפשטה מכוונת לתרגול, לא דיוק מלא של מס הכנסה מדורג על כלל ההכנסה.
        remaining = gain
        tax_amount = 0.0
        for bracket in brackets:
            width = (bracket.max_amount - bracket.min_amount) if bracket.max_amount is not None else remaining
            taxed_in_bracket = max(0.0, min(remaining, width))
            tax_amount += taxed_in_bracket * bracket.rate
            remaining -= taxed_in_bracket
            if remaining <= 0:
                break

        effective_rate = (tax_amount / gain) if gain > 0 else 0.0
        source_url = brackets[0].official_source_url if brackets else "NO_SOURCE"
        return TaxCalculationResult("PROGRESSIVE_BRACKETS", tax_amount, effective_rate, version_date, source_url)
