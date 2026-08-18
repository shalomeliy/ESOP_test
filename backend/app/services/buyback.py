"""רכישה עצמית ותיקון הנפקה (v1.2.0) - מפרט: docs/spec/v1.2.0.md.

*** למה שירות ולא קוד בתוך ה-handler ***: המפרט (§7) דורש שהתצוגה המקדימה
תישען על *אותו אובייקט בדיוק* כמו הביצוע. תצוגה מקדימה שמריצה ולידציה משלה
היא שער שני וחלש יותר - היא מראה לאדמין דיף שהתקבל בכללים אחרים מאלה שיחליטו
אם הכתיבה תעבור. לכן כל הוולידציה וכל חישוב הדיף יושבים כאן, ושני
האנדפוינטים קוראים לאותה פונקציה.

*** אפס מס ***: המס כאן אינו "לא אומת" אלא לא ניתן לחישוב במודל הקיים -
ל-ShareIssuance אין בסיס עלות ול-Shareholder אין מסלול מס או קישור למענק.
לכן שדה מפורש NOT_COMPUTED עם קוד סיבה, ולעולם לא שדה חסר ולעולם לא 0.0.
אותו תקדים בדיוק כמו MissingTaxRuleError ב-services/tax_engine.py, שהחליף
נפילה שקטה ל-25%.
"""

from datetime import date
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.models import Company, LedgerEvent, ShareIssuance, Shareholder
from backend.app.services.cap_table import compute_cap_table_snapshot
from backend.app.services.ledger import events_for, project_share_issuance

# הסיבות החוקיות לאירוע SHARE_ISSUANCE_ADJUSTED. אוצר מילים סגור שנבדק
# באפליקציה ולא ב-CHECK, בדיוק כמו LEDGER_EVENT_TYPES עצמו.
BUYBACK_REASONS = {"BUYBACK", "CORRECTION", "CANCELLATION"}

# קוד הסיבה שנלווה ל-tax_treatment. קוד ולא משפט חופשי, כדי שצרכן במורד הזרם
# יוכל להסתמך עליו בלי לפרסר טקסט.
TAX_NOT_COMPUTED = "NOT_COMPUTED"
TAX_REASON_NO_COST_BASIS = "NO_COST_BASIS_OR_TAX_TRACK_IN_MODEL"


class BuybackRejected(ValueError):
    """נדחה *לפני* כתיבת האירוע. נושא הודעה אמיתית שמוצגת למשתמש כפי שהיא -
    §9 דורש שתיבת השגיאה תראה את טקסט האילוץ מהשרת ולא ניסוח כללי."""


def current_sequence_no(db: Session, aggregate_id: str) -> int:
    """הסימן שמולו התצוגה המקדימה והביצוע משווים (קריטריון 10). sequence_no
    עולה מונוטונית פר-צובר (UniqueConstraint(aggregate_id, sequence_no)), ולכן
    הוא הסימן היחיד הזמין בלי סכמה חדשה. אותו נרמול (or 0) כמו
    ledger.py::_next_sequence_no - למנה בלי אף אירוע הסימן מוגדר היטב כ-0."""
    return (db.query(func.max(LedgerEvent.sequence_no))
            .filter(LedgerEvent.aggregate_id == aggregate_id).scalar()) or 0


def build_buyback_projection(
    db: Session,
    *,
    issuance: ShareIssuance,
    shares: float,
    effective_date: date,
    reason: str,
) -> dict:
    """מאמת ובונה את הדיף המלא. אינו כותב דבר ואינו עושה commit.

    ``shares`` חיובי = רכישה עצמית (דלתא שלילית). ``shares`` שלילי = תיקון
    כלפי מעלה - מותר, ראו בדיקת התקרה למטה.
    """
    if reason not in BUYBACK_REASONS:
        raise BuybackRejected(f"reason must be one of {sorted(BUYBACK_REASONS)}")
    if shares == 0:
        raise BuybackRejected("shares must not be zero")

    # --- שלמות הכמות (סקירה 12, אזהרה 7) ---
    # כל שדה כספי במודל הוא Float ולא Decimal (חוב פתוח א', מתוזמן ל-v1.2.3),
    # וה-CHECK של option_pools הוא שוויון צף מדויק שמחזיק *רק* כל עוד כל
    # הכמויות שלמות. זו הגרסה הראשונה שמפחיתה מניות, כלומר הראשונה שיכולה
    # להכניס שבר לעמודה - ולכן השומר יושב כאן, לפני שהשבר נכתב, ולא בהמתנה
    # למעבר ל-Decimal. is_integer ולא int(shares) בכוונה: inf/nan מפילים int().
    if not float(shares).is_integer():
        raise BuybackRejected(
            f"shares must be a whole number of shares (got {shares}) - "
            f"fractional share amounts are not modelled"
        )

    # --- קריטריון 7: מוקדם מההנפקה נדחה קשיחות, בלי חלופה מרוככת ---
    # הקיפול ממוין לפי (effective_date, sequence_no) ואירוע הבסיס של ShareIssuance
    # מתועד ב-issue_date אמיתי. אירוע שמתוארך לפניו הוא אירוע שהמודל אינו יודע
    # לתאר: "הופחתו מניות לפני שהונפקו". הפרויקטור מגן על נתיב הייבוא; כאן
    # חוסמים במקור.
    if effective_date < issuance.issue_date:
        raise BuybackRejected(
            f"effective_date ({effective_date}) is before the issuance date "
            f"({issuance.issue_date}) - shares cannot be adjusted before they were issued"
        )

    # --- קריטריון 16: מנה בלי היסטוריית ledger נדחית, לא "מסומנת" ---
    # אחרת אירוע הדלתא נוחת כאירוע הראשון של הצובר, הפרויקטור לעולם לא יחיל
    # אותו, והעמודה תופחת בלעדיו - סטייה קבועה בין העמודה ל-ledger.
    lot_state = project_share_issuance(events_for(db, issuance.share_issuance_id))
    if lot_state is None:
        raise BuybackRejected(
            f"share issuance {issuance.share_issuance_id} has no ledger history - "
            f"it cannot be adjusted until its history is repaired"
        )

    delta = -shares
    lot_before = lot_state["shares"]
    lot_after = lot_before + delta

    # --- קריטריון 4: אין החזקה שלילית בשום מסלול ---
    if lot_after < 0:
        raise BuybackRejected(
            f"cannot buy back {shares} shares - the lot holds only {lot_before}"
        )

    # --- שעון אחד: מספרי החברה הם מספרי *עכשיו* (הכרעת המשתתף, 17/08/2026) ---
    # עד סקירה 12 זה היה compute_cap_table_snapshot(..., effective_date), וזה
    # הראה לאדמין - על מסך אישור בלתי-הפיך - מספרי חברה שאינם מספרי החברה: כל
    # הפיצ'ר הוא "תיעוד עסקה שכבר קרתה מחוץ למערכת" (§2), ולכן effective_date
    # הוא כמעט תמיד בעבר, והפער היה מגיע ל-9,000 מניות בהוכחת הסוקר.
    # החמור מזה היה עירוב שני שעונים באותו דיף: lot_before מגיע מקיפול מלא בלי
    # חתך as-of (למעלה), כלומר "עכשיו", בעוד מספרי החברה נחתכו ב-effective_date.
    # עכשיו שני הבלוקים על אותו שעון, ה-as-of מוחזר במפורש בשדה company_as_of,
    # ו-effective_date נשאר מוצג כתאריך העסקה בלבד. הדלתא נכונה לשני הרגעים:
    # אירוע מתוארך-אחורנית משפיע גם על התמונה של היום.
    company_before = compute_cap_table_snapshot(db, issuance.company_id, None)

    # --- קריטריון 15: דלתא חיובית נבדקת מול תקרת המניות המורשות ---
    # *** תוקן בסקירה 12 (חוסם 1) ***: כאן הצהירה הערה ש"זו אותה נוסחה בדיוק
    # כמו create_share_issuance" בעוד ההשוואה נעשתה מול snapshot חתוך ב-
    # effective_date. שתי נוסחאות נפרדות: כל הנפקה מאוחרת מ-effective_date
    # נשמטה מהסך, וכך תיקון כלפי מעלה שמתוארך למנה ישנה *פרץ את התקרה* -
    # פגם דיני-תאגידי שנשאר ב-ledger לנצח. הנוסחה עכשיו זהה לתו: סכום העמודה
    # לפי company_id, בלי חתך תאריך. העמודה - ולא ה-snapshot - היא משטח האכיפה,
    # כי היא מה ש-create_share_issuance סוכם, וקריטריון 15 מפנה אליו בשמו.
    # תקרה None => אין בדיקה (דפוס P4: לא ממציאים תקרה שלא הוגדרה).
    if delta > 0:
        company = db.get(Company, issuance.company_id)
        cap = company.total_authorized_shares if company else None
        if cap is not None:
            issued_total = (
                db.query(func.sum(ShareIssuance.shares))
                .filter(ShareIssuance.company_id == issuance.company_id)
                .scalar()
            ) or 0.0
            if issued_total + delta > cap:
                raise BuybackRejected(
                    f"Adjustment would exceed total_authorized_shares "
                    f"(available: {cap - issued_total})"
                )

    shareholder = db.get(Shareholder, issuance.shareholder_id)

    # ההחזקה הכוללת ברמת (בעל מניות, סוג מניה) - §6 דורש את *שתי* הרמות על
    # אותו מסך: האדמין בוחר מנה, אבל התוצאה שהוא שופט היא ההחזקה הכוללת.
    holding_before = next(
        (row["shares"] for row in company_before["by_shareholder_and_class"]
         if row["shareholder_id"] == issuance.shareholder_id
         and row["share_class_id"] == issuance.share_class_id),
        None,
    )
    holding_after = None if holding_before is None else holding_before + delta

    return {
        "share_issuance_id": issuance.share_issuance_id,
        "issue_date": issuance.issue_date,
        "effective_date": effective_date,
        "reason": reason,
        "shareholder": {
            "shareholder_id": issuance.shareholder_id,
            "name": shareholder.name if shareholder else None,
            # §6: רכישה מעובד היא החלטה שונה מרכישה ממשקיע, וזה חייב להיות על
            # פני התצוגה ולא מוסק ממנה.
            "employee_id": shareholder.employee_id if shareholder else None,
        },
        "share_class_id": issuance.share_class_id,
        "lot_before": lot_before,
        "lot_delta": delta,
        "lot_after": lot_after,
        "holding_before": holding_before,
        "holding_after": holding_after,
        # התאריך שמספרי החברה נכונים לו, במפורש ולא במשתמע - הצרכן (המסך,
        # הקבלה, בדיקה) אינו צריך לדעת איזה שעון השירות בחר.
        "company_as_of": company_before["as_of"],
        "company_before": _company_numbers(company_before),
        "company_after": _company_numbers(company_before, delta=delta),
        "partial": company_before["partial"],
        "warnings": company_before["warnings"],
        "expected_sequence_no": current_sequence_no(db, issuance.share_issuance_id),
        "tax_treatment": TAX_NOT_COMPUTED,
        "tax_reason_code": TAX_REASON_NO_COST_BASIS,
    }


def _company_numbers(snapshot: dict, delta: float = 0.0) -> dict:
    """מספרי הכותרת, לפני או אחרי. ה"אחרי" נגזר אריתמטית מה"לפני" ולא
    מחישוב שני - שני חישובים נפרדים על אותו רגע היו יכולים להיפרד זה מזה
    ולהראות לאדמין דיף שאינו עקבי בתוך עצמו."""
    authorized = snapshot["total_authorized_shares"]
    outstanding = snapshot["outstanding_shares"] + delta
    fully_diluted = snapshot["fully_diluted_shares"] + delta
    usable_cap = authorized if authorized is not None and authorized > 0 else None
    return {
        "outstanding_shares": outstanding,
        "fully_diluted_shares": fully_diluted,
        "total_authorized_shares": authorized,
        # None ולא 0% כשאין תקרה - דפוס הכשל P4, זהה ל-compute_cap_table_snapshot.
        "outstanding_pct_of_authorized": None if usable_cap is None else outstanding / usable_cap,
        "fully_diluted_pct_of_authorized": None if usable_cap is None else fully_diluted / usable_cap,
    }
