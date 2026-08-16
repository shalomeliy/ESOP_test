"""חישוב דילול (dilution) - v1.0.0 שלב ב. אגרגציה טהורה בזמן קריאה, בלי
persist ובלי ledger event type חדש (ראו PLAN של השלב - "decision 2": אישור
ExerciseRequest לא יוצר ShareIssuance/Shareholder, ולכן total_shares של פול
מכיל כבר את כל מה ששמור לאופציות בו, ממומש או לא - זה מה שמונע ספירה כפולה
כשמחברים אותו ל-outstanding_shares).

הגדרת "fully diluted" הוחלט מפורשות מול המשתתף (לא הומצא כאן):
outstanding_shares (סכום ShareIssuance.shares) + total_shares של *כל* פול
אופציות, פעם אחת per פול בלי קשר לכמה ממנו הוקצה/מומש בפועל.
"""

from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from backend.app.models import Company, LedgerEvent, OptionPool, ShareIssuance
from backend.app.services.ledger import project, project_share_issuance
from backend.app.types import business_today


def compute_cap_table_snapshot(db: Session, company_id: str, as_of: Optional[date] = None) -> dict:
    """מצב טבלת ההון (outstanding + fully-diluted) נכון לתאריך ``as_of``.

    ``as_of`` בעבר => שכפול חלקי דרך project() לפי היסטוריית ה-ledger של כל
    פול; ``as_of`` בהווה/עתיד => קריאה ישירה של העמודות המוטטות הנוכחיות
    (אין הבדל בין "היום" ל"עתיד" - שניהם "מה שקיים כרגע", בדיוק כמו
    reconcile()/business_today()). תאריך עתידי *אינו* נדחה - הוא תקף,
    ופשוט שווה-ערך ל"היום" כל עוד לא נוספו עוד הנפקות/שינויי-פול.
    """
    as_of = as_of or business_today()
    today = business_today()

    warnings: list[str] = []
    partial = False

    # --- צד המונפק (outstanding) - replay מלא, v1.2.0 ---
    # עד v1.1.1 זה היה סינום עמודה ישיר (sum(row.shares)), שהיה שקול ל-replay
    # רק כל עוד ל-ShareIssuance היה סוג אירוע יחיד. SHARE_ISSUANCE_ADJUSTED
    # שבר את השקילות הזו, ולכן הסכום נגזר עכשיו מהאירועים.
    #
    # *** שורש השאילתה הוא ShareIssuance ולא LedgerEvent ***: שאילתה שמושרשת
    # באירועים מחזירה אירועים, ולכן שורת הנפקה שאין לה אף אירוע מחזירה אפס
    # שורות - כלומר אינה נראית כלל, אי אפשר להזהיר עליה, והיא נעלמת בשקט
    # מהסכום. ענף הפול למטה עובד נכון בדיוק מאותה סיבה: הוא מאיטרר קודם על
    # שורות הפול ורק אז מקרין כל אחת.
    #
    # *** issue_date <= as_of נשאר, ואינו עודף ***: אירוע הבסיס של ShareIssuance
    # מתועד ב-effective_date=issue_date אמיתי (לא LEDGER_EPOCH), ולכן בלי החתך
    # הזה כל הנפקה מאוחרת מ-as_of הייתה מגיעה לכאן עם קבוצת אירועים ריקה
    # ונופלת לענף "חסר היסטוריה" - כלומר partial ואזהרה על דאטה בריאה לגמרי,
    # בכל תמונת מצב היסטורית.
    #
    # שתי שאילתות בלבד, לא N+1: השורות, ואז כל האירועים שלהן במכה אחת.
    # JOIN ולא IN(...) - אותו תיקון בדיוק כמו services/export.py ו-reports.py
    # (v1.1.1 פריט ב); רשימת מזהים ב-IN גדלה עם מספר ההנפקות.
    issuance_rows = (
        db.query(ShareIssuance)
        .filter(ShareIssuance.company_id == company_id, ShareIssuance.issue_date <= as_of)
        .all()
    )

    events_by_issuance: dict[str, list] = {}
    if issuance_rows:
        event_rows = (
            db.query(LedgerEvent)
            .join(ShareIssuance, ShareIssuance.share_issuance_id == LedgerEvent.aggregate_id)
            .filter(
                ShareIssuance.company_id == company_id,
                ShareIssuance.issue_date <= as_of,
                LedgerEvent.aggregate_type == "ShareIssuance",
                LedgerEvent.effective_date <= as_of,
            )
            .order_by(LedgerEvent.effective_date, LedgerEvent.sequence_no)
            .all()
        )
        for event in event_rows:
            events_by_issuance.setdefault(event.aggregate_id, []).append(event)

    outstanding_shares = 0.0
    breakdown_by_key: dict[tuple[str, str], float] = {}
    # צמד שיש לו *ולו שורה אחת* בלי היסטוריה - הסכום שלו אינו ידוע, ולכן הוא
    # מדווח None ולא "סכום שאר השורות". סכום חלקי שנראה שלם גרוע יותר מ"לא ידוע".
    keys_with_missing_history: set[tuple[str, str]] = set()
    for row in issuance_rows:
        state = project_share_issuance(events_by_issuance.get(row.share_issuance_id, []))
        key = (row.shareholder_id, row.share_class_id)
        if state is None:
            # שורה קיימת בלי שום היסטוריית ledger - תקלת שלמות דאטה, לא מסלול
            # נורמלי (import_.py יכול לנחות שורה בלי האירוע שלה). אותה התנהגות
            # בדיוק כמו ענף הפול: מסמנים ומחריגים, אף פעם לא 0 - אפס כאן היה
            # מפחית את הדילול בשקט. שים לב שזה *לא* המצב "כל האירועים מאוחרים
            # מ-as_of", שכבר סוננה החוצה בחתך issue_date למעלה.
            warnings.append(
                f"share issuance {row.share_issuance_id} has no ledger history - "
                f"excluded from as-of calculation"
            )
            partial = True
            keys_with_missing_history.add(key)
            breakdown_by_key.setdefault(key, 0.0)
            continue

        # 0.0 הוא ערך נכון ומובחן: מנה שנרכשה במלואה. השורה נשארת בפילוח
        # כשורת אפס ולא נשמטת - השמטה הייתה הופכת "הוחזק ואיננו" ל"מעולם לא היה".
        breakdown_by_key[key] = breakdown_by_key.get(key, 0.0) + state["shares"]
        outstanding_shares += state["shares"]

    by_shareholder_and_class = [
        {"shareholder_id": shareholder_id, "share_class_id": share_class_id,
         "shares": None if (shareholder_id, share_class_id) in keys_with_missing_history else shares}
        for (shareholder_id, share_class_id), shares in breakdown_by_key.items()
    ]

    # --- צד הפול (fully-diluted) ---
    pools_out = []
    pool_shares_total = 0.0

    pool_rows = db.query(OptionPool).filter(OptionPool.company_id == company_id).all()
    for pool in pool_rows:
        if as_of >= today:
            # "עכשיו" (או עתיד) - העמודה המוטטת היא כבר מקור-האמת הנוכחי,
            # אותו דבר שהבדיקות הקיימות של שלב א קוראות (למשל QA-100-08) -
            # אין צורך ב-replay כדי לשאול "מה נכון עכשיו".
            pool_total = pool.total_shares
        else:
            state = project(db, "OptionPool", pool.pool_id, as_of_effective_date=as_of)
            if state is None:
                # פול קיים אבל אין לו שום היסטוריית ledger (תקלת שלמות-דאטה,
                # לא מסלול נורמלי) - אסור להתייחס לזה כ-0 (שם היה מוריד את
                # ה-fully-diluted באופן שקרי) ואסור לקרוס. מסמנים ומדלגים.
                warnings.append(
                    f"pool {pool.pool_id} has no ledger history - excluded from as-of calculation"
                )
                partial = True
                pools_out.append({
                    "pool_id": pool.pool_id, "share_class_id": pool.share_class_id, "total_shares": None,
                })
                continue
            pool_total = state["total_shares"]

        pool_shares_total += pool_total
        pools_out.append({
            "pool_id": pool.pool_id, "share_class_id": pool.share_class_id, "total_shares": pool_total,
        })

    fully_diluted_shares = outstanding_shares + pool_shares_total

    # --- אחוזי דילול מתוך total_authorized_shares - אותו דפוס P4 כמו
    # create_share_issuance: None => שני האחוזים None (לא 0%), כי 0% הוא
    # מספר שקרי-קונקרטי במקום "לא זמין". *** תוקן בסקירה ***: total_authorized_shares=0.0
    # (לא None - ערך ממשי שאין עליו ולידציית positivity ב-CompanyUpdateRequest,
    # ראו schemas.py) עבר את בדיקת ה-`is not None` והפיל ZeroDivisionError לא-מטופל.
    # מבחינת דילול, 0 אינו שונה מ"לא הוגדר" - אין מכנה תקין לחלק בו - ולכן מטופל
    # באותו אופן בדיוק: שני האחוזים נשארים None, לא קורס ולא 0%/100% שקריים.
    company = db.query(Company).filter(Company.company_id == company_id).first()
    total_authorized_shares = company.total_authorized_shares if company else None

    outstanding_pct_of_authorized = None
    fully_diluted_pct_of_authorized = None
    if total_authorized_shares is not None and total_authorized_shares > 0:
        outstanding_pct_of_authorized = outstanding_shares / total_authorized_shares
        fully_diluted_pct_of_authorized = fully_diluted_shares / total_authorized_shares

    return {
        "as_of": as_of,
        "outstanding_shares": outstanding_shares,
        "fully_diluted_shares": fully_diluted_shares,
        "total_authorized_shares": total_authorized_shares,
        "outstanding_pct_of_authorized": outstanding_pct_of_authorized,
        "fully_diluted_pct_of_authorized": fully_diluted_pct_of_authorized,
        "partial": partial,
        "warnings": warnings,
        "by_shareholder_and_class": by_shareholder_and_class,
        "pools": pools_out,
    }
