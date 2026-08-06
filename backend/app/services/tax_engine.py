from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from backend.app.models import TaxRatesHistory, IncomeTaxBracket, TaxRulePack, TAX_CALCULATION_METHODS


class MissingTaxRuleError(ValueError):
    """אין TaxRulePack שחל על (מדינה, סוג מענק, תאריך מימוש) - ולכן *אי אפשר
    לחשב* מס, לא רק "אין נתון נוח". v0.7.0: זה מחליף fallback שקט לשיעור קבוע
    (25%) שהיה קיים כאן קודם - נחש שנראה כמו חישוב אמיתי הוא הפרה ישירה של
    GOAL.md קריטריון 5 ("אין מספר בלי שרשור מקורות").

    reason מבחין בין שני מצבים שדורשים תגובה שונה (גם אם שניהם מוחזרים כ-409
    זהה ללקוח - ראו routes.py): המסלול הזה מעולם לא נבנה בכלל, מול המסלול קיים
    אבל אין גרסה שחלה על התאריך הספציפי הזה (למשל תאריך מימוש לפני שנת המקור
    הראשונה שהוזנה). ההבחנה חיה בהודעה הפנימית/ב-audit, לא בקוד ה-HTTP.
    """
    NEVER_MODELED = "NEVER_MODELED"
    NO_RULE_EFFECTIVE_AS_OF_DATE = "NO_RULE_EFFECTIVE_AS_OF_DATE"
    PACK_HAS_NO_DETAIL_ROWS = "PACK_HAS_NO_DETAIL_ROWS"
    INVALID_CALCULATION_METHOD = "INVALID_CALCULATION_METHOD"

    def __init__(self, country_code: str, grant_type: str, exercise_date: date, reason: str):
        self.country_code = country_code
        self.grant_type = grant_type
        self.exercise_date = exercise_date
        self.reason = reason
        super().__init__(
            f"No tax rule pack applies to country_code={country_code}, grant_type={grant_type}, "
            f"exercise_date={exercise_date} (reason={reason})"
        )


def _round_money(amount: float) -> float:
    """עיגול לאגורות/סנטים. כפל float גולמי מחזיר 28000.000000000004 במקום 28000.0,
    וסכום מס שמוצג למשתמש חייב להיות סכום כסף חוקי - לא שארית ייצוג בינארי.
    DeterministicESOPEngine כבר מעגל ל-2 ספרות; זה מיישר את מנוע המס לאותו כלל."""
    return round(amount, 2)


@dataclass
class TaxCalculationResult:
    method: str  # "FLAT_RATE" | "PROGRESSIVE_BRACKETS"
    tax_amount: float
    effective_rate: float
    table_effective_date: date
    source_url: str
    pack_id: str


class TaxCalculationEngine:
    """חישוב מס דטרמיניסטי, versioned לפי תאריך - כמו DeterministicESOPEngine
    להבשלה, אבל לצד המיסוי. *** כל הנתונים המוזנים היום הם דמו לתרגול QA
    (ראו official_source_url), לא חוק מס אמיתי - ראו CLAUDE.md. ***

    v0.7.0: שיטת החישוב (שטוח מול מדורג) כבר לא if קשיח על grant_type - היא
    נקראת מ-TaxRulePack.calculation_method, שדה דאטה. זורק MissingTaxRuleError
    כשאין חבילה שחלה, במקום ליפול ל-fallback שקט. ראו GOAL.md קריטריון 5.
    """

    @staticmethod
    def calculate_tax(db: Session, country_code: str, grant_type: str,
                       exercise_date: date, gain: float) -> TaxCalculationResult:
        pack = (
            db.query(TaxRulePack)
            .filter(
                TaxRulePack.country_code == country_code,
                TaxRulePack.grant_type == grant_type,
                TaxRulePack.effective_start_date <= exercise_date,
            )
            .order_by(TaxRulePack.effective_start_date.desc())
            .first()
        )
        if not pack:
            ever_modeled = (
                db.query(TaxRulePack)
                .filter(TaxRulePack.country_code == country_code, TaxRulePack.grant_type == grant_type)
                .first()
                is not None
            )
            reason = (MissingTaxRuleError.NO_RULE_EFFECTIVE_AS_OF_DATE if ever_modeled
                      else MissingTaxRuleError.NEVER_MODELED)
            raise MissingTaxRuleError(country_code, grant_type, exercise_date, reason)

        # R-070-01: calculation_method הוא String חופשי (לא נאכף ב-DB, כמו
        # LEDGER_EVENT_TYPES) - נבדק כאן בזמן קריאה, לא רק נסמך על מה שנכתב.
        # ערך לא-מוכר הוא שגיאת דאטה, לא "כברירת מחדל שטוח" בשקט.
        if pack.calculation_method not in TAX_CALCULATION_METHODS:
            raise MissingTaxRuleError(pack.country_code, pack.grant_type, pack.effective_start_date,
                                      MissingTaxRuleError.INVALID_CALCULATION_METHOD)
        if pack.calculation_method == "PROGRESSIVE_BRACKETS":
            return TaxCalculationEngine._calculate_progressive(db, pack, gain)
        return TaxCalculationEngine._calculate_flat(db, pack, gain)

    @staticmethod
    def _calculate_flat(db: Session, pack: TaxRulePack, gain: float) -> TaxCalculationResult:
        rule = db.query(TaxRatesHistory).filter(TaxRatesHistory.pack_id == pack.pack_id).first()
        if not rule:
            raise MissingTaxRuleError(pack.country_code, pack.grant_type, pack.effective_start_date,
                                      MissingTaxRuleError.PACK_HAS_NO_DETAIL_ROWS)

        return TaxCalculationResult("FLAT_RATE", _round_money(gain * rule.capital_gains_rate),
                                    rule.capital_gains_rate, pack.effective_start_date,
                                    pack.official_source_url, pack.pack_id)

    @staticmethod
    def _calculate_progressive(db: Session, pack: TaxRulePack, gain: float) -> TaxCalculationResult:
        brackets = (
            db.query(IncomeTaxBracket)
            .filter(IncomeTaxBracket.pack_id == pack.pack_id)
            .order_by(IncomeTaxBracket.bracket_order)
            .all()
        )
        if not brackets:
            raise MissingTaxRuleError(pack.country_code, pack.grant_type, pack.effective_start_date,
                                      MissingTaxRuleError.PACK_HAS_NO_DETAIL_ROWS)

        # החישוב עצמו מתייחס ל-gain כאל כל ההכנסה החייבת (לא נערם על
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

        tax_amount = _round_money(tax_amount)
        effective_rate = (tax_amount / gain) if gain > 0 else 0.0
        return TaxCalculationResult("PROGRESSIVE_BRACKETS", tax_amount, effective_rate,
                                    pack.effective_start_date, pack.official_source_url, pack.pack_id)
