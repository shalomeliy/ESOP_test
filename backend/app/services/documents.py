"""מנוע יצירת מסמכי PDF (v0.9.0 שלב 1: כתב הענקה בלבד).

ReportLab (לא WeasyPrint) - ראו הערת requirements.txt. כל תבנית היא פונקציית
Python שמרכיבה flowables, לא תבנית HTML - עלות חד-פעמית של 3 תבניות קבועות,
לא עלות מתמשכת.

הקבצים יושבים ב-document_store/ (תיקייה מקומית, לא ב-git - בדיוק כמו
esop_database.db, ראו .gitignore). לעולם לא מוגשים כקובץ סטטי ישירות; רק
דרך endpoint מאומת שקורא ל-assert_document_access קודם.
"""

import hashlib
import io
import os
from datetime import date
from pathlib import Path
from typing import List, Optional

from bidi import get_display
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors

from backend.app.models import Grant, Employee, Company, Trustee

DOCUMENT_STORE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "document_store"

# *** נמצא באימות ידני מול נתונים אמיתיים ***: ReportLab's default font
# (Helvetica) אין לו glyphs לעברית בכלל - שם עובד עברי (למשל "ישראל ישראלי",
# הנפוץ ביותר בדאטה הזרוע) הודפס כריבועים שחורים, לא כטקסט קריא. זה בדיוק סוג
# הכשל שהפרויקט אמור לתפוס: PDF "שנוצר בהצלחה" עם שם העובד בלתי-קריא הוא גרוע
# יותר מכשל מפורש. הרשימה כאן היא נתיבי גופן ידועים לפי מערכת הפעלה - רק Arial
# ב-Windows אומת בפועל בסשן הזה שיש לו כיסוי עברית; שאר הנתיבים הם מועמדים
# סבירים שלא אומתו. אם אף אחד לא נמצא - נכשל בגלוי (DocumentRenderingError),
# לא מייצר PDF עם טקסט שבור בשקט.
_UNICODE_FONT_CANDIDATES = [
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
]
_UNICODE_FONT_NAME = "DocumentUnicodeFont"
_unicode_font_registered = False


class DocumentRenderingError(RuntimeError):
    """כשל תשתיתי (למשל: אין גופן עם כיסוי יוניקוד/עברית זמין על השרת) - לא
    בעיית נתוני מענק. נבדל במפורש מ-MissingDocumentDataError."""


def _ensure_unicode_font() -> str:
    global _unicode_font_registered
    if _unicode_font_registered:
        return _UNICODE_FONT_NAME
    for candidate in _UNICODE_FONT_CANDIDATES:
        if os.path.exists(candidate):
            pdfmetrics.registerFont(TTFont(_UNICODE_FONT_NAME, candidate))
            _unicode_font_registered = True
            return _UNICODE_FONT_NAME
    raise DocumentRenderingError(
        "No Unicode-capable font found on this server (checked: "
        f"{', '.join(_UNICODE_FONT_CANDIDATES)}) - cannot safely render employee names "
        "that may contain Hebrew characters. Install a Unicode font before generating documents."
    )


class MissingDocumentDataError(ValueError):
    """אין למענק את הנתונים הדרושים כדי לייצר את המסמך הזה - נכשל בגלוי,
    לא מייצר PDF חלקי/מטעה. ראו החלטת התכנון: לוח הבשלה חסר => כשל, לא PDF
    בלי סעיף ההבשלה."""


def _ensure_store_dir() -> None:
    os.makedirs(DOCUMENT_STORE_DIR, exist_ok=True)


def _sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rtl(text: str) -> str:
    """ReportLab מצייר תווים בסדר שהוזן, בלי אלגוריתם BIDI משלו - טקסט עברי
    (מימין-לשמאל) נצבע במראה בלי זה. *** נמצא באימות ידני *** מול שם עובד
    אמיתי ("ישראל ישראלי") - חובה על כל שדה טקסט חופשי (שם עובד/חברה/נאמן),
    לא רק תוויות קבועות באנגלית."""
    return get_display(text)


# הכיתוב שמופיע *בגוף כל PDF*, לא רק בהערת קוד: המסמך עצמו חייב להצהיר שאין לו
# תוקף משפטי ושאין בו חתימה. אחרת עובד שמחזיק את הקובץ ביד לא יכול לדעת את זה.
_NOT_BINDING_NOTICE = (
    "This is an internally generated practice document (demo template, not reviewed legal "
    "counsel content) and does not constitute a legally binding agreement or signature."
)
_ACKNOWLEDGMENT_NOTICE = (
    "This document is generated for internal tracking only. Any acknowledgment recorded "
    "against it is an internal record of receipt, not a legally binding signature."
)


def _render_pdf(title: str, rows: list, document_id: str, version: int) -> tuple[str, str]:
    """המרנדר המשותף לכל התבניות - כותרת, הצהרת אי-מחויבות, טבלת שדות, והצהרת
    האישור. שלוש התבניות נבדלות רק בכותרת ובשורות, לא במבנה."""
    _ensure_store_dir()
    relative_path = f"{document_id}_v{version}.pdf"
    full_path = DOCUMENT_STORE_DIR / relative_path

    # שם עובד עברי הוא המקרה הנפוץ בדאטה הזרוע (ראו seed_data.py) - Helvetica
    # (ברירת המחדל של ReportLab) אין לו glyphs לעברית בכלל ומדפיס ריבועים
    # שחורים. נמצא באימות ידני מול G-2021-001 (עובד "ישראל ישראלי"). כל
    # הסגנונות כאן משתמשים בגופן היוניקוד הזה, לא בברירת המחדל.
    font_name = _ensure_unicode_font()
    styles = getSampleStyleSheet()
    for style_name in ("Title", "Italic", "Normal"):
        styles[style_name].fontName = font_name

    story = [
        Paragraph(title, styles["Title"]),
        Spacer(1, 0.5 * cm),
        Paragraph(_NOT_BINDING_NOTICE, styles["Italic"]),
        Spacer(1, 1 * cm),
    ]

    table = Table(rows, colWidths=[6 * cm, 9 * cm])
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(table)
    story.append(Spacer(1, 1 * cm))
    story.append(Paragraph(_ACKNOWLEDGMENT_NOTICE, styles["Normal"]))

    SimpleDocTemplate(str(full_path), pagesize=A4).build(story)
    return relative_path, _sha256_of_file(full_path)


# ===================================================================
# רינדור PDF אפמרי, טבלאי n-עמודות (v1.1.0, דוחות/BI - services/reports.py)
# ===================================================================
# שונה מ-_render_pdf למעלה בשני דברים, שני חסמים אמיתיים לא סגנוניים:
# (1) _render_pdf כותב תמיד ל-DOCUMENT_STORE_DIR לפי document_id/version -
#     שני פרמטרים שמזהים שורת Document שמורה ב-DB. לדוח BI אין ולעולם לא
#     תהיה שורת Document כזו, ואין רצון להנציח קובץ על הדיסק בכל צפייה/
#     הורדת דוח (בניגוד למסמך שנוצר פעם אחת ונשלח). הפונקציה כאן מחזירה
#     bytes בזיכרון בלבד - שום דבר לא נכתב לדיסק.
# (2) _render_pdf בונה תמיד טבלת label/value דו-עמודתית קבועה (colWidths
#     [6cm, 9cm]) - מתאימה ל"כרטיס פרטים" של מענק בודד, לא לדוח טבלאי
#     שנועד מטבעו להיות הרבה שורות/כמה עמודות (pool_id, total, allocated...).
# משתפת עם _render_pdf: רישום הגופן (_ensure_unicode_font, אותו global
# _unicode_font_registered - לא נרשם פעמיים), עוזר ה-RTL (_rtl), והצהרת
# אי-המחויבות (_NOT_BINDING_NOTICE) - אותה סיבה בדיוק (שם עובד עברי אמיתי
# עלול להופיע גם בתוכן דוח, למשל "עובדים בסיכון דדליין").
def render_tabular_pdf(title: str, headers: List[str], rows: List[list],
                       disclosures: Optional[List[str]] = None) -> bytes:
    font_name = _ensure_unicode_font()
    styles = getSampleStyleSheet()
    for style_name in ("Title", "Italic", "Normal"):
        styles[style_name].fontName = font_name

    story = [
        Paragraph(_rtl(title), styles["Title"]),
        Spacer(1, 0.5 * cm),
        Paragraph(_NOT_BINDING_NOTICE, styles["Italic"]),
    ]
    for note in (disclosures or []):
        story.append(Spacer(1, 0.2 * cm))
        story.append(Paragraph(_rtl(note), styles["Italic"]))
    story.append(Spacer(1, 0.5 * cm))

    col_count = max(1, len(headers))
    if rows:
        table_data = [[_rtl(h) for h in headers]] + [[_rtl(cell) for cell in row] for row in rows]
    else:
        table_data = [[_rtl(h) for h in headers], ["— no rows —"] + [""] * (col_count - 1)]

    # A4 לרוחב (landscape) ולא לאורך - דוחות טבלאיים נוטים לכמה עמודות (עד
    # כ-12 בדוח הוצאת השכר המשוער), ורוחב נוסף חוסך גדישת טקסט לפני שכל
    # תא נחתך. usable_width משוער לפי גודל landscape A4 פחות שולי ברירת
    # המחדל של SimpleDocTemplate (2.5 ס"מ לכל צד).
    usable_width = landscape(A4)[0] - 5 * cm
    col_width = usable_width / col_count
    table = Table(table_data, colWidths=[col_width] * col_count, repeatRows=1)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(table)

    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=landscape(A4)).build(story)
    return buffer.getvalue()


def _grant_type_value(grant: Grant) -> str:
    return grant.grant_type.value if hasattr(grant.grant_type, "value") else grant.grant_type


def build_grant_letter(grant: Grant, employee: Employee, company: Company,
                       trustee: Optional[Trustee], document_id: str, version: int) -> tuple[str, str]:
    """כתב הענקה. זורק MissingDocumentDataError אם אין לוח הבשלה - כשל מפורש,
    לא מסמך שמדלג בשקט על סעיף ההבשלה (מקביל ל-MissingVestingScheduleError)."""
    schedule = grant.vesting_schedule
    if not schedule:
        raise MissingDocumentDataError(
            f"Grant {grant.grant_id} has no vesting schedule - cannot generate a grant letter "
            "without vesting terms. Attach a vesting schedule before generating this document."
        )

    rows = [
        ["Employee", _rtl(f"{employee.first_name} {employee.last_name}")],
        ["National ID", employee.national_id or "— not on file —"],
        ["Company", _rtl(company.name)],
        ["Grant ID", grant.grant_id],
        ["Grant date", grant.grant_date.isoformat()],
        ["Grant type", _grant_type_value(grant)],
        ["Total options", f"{grant.total_options:,.2f}"],
        ["Exercise price", f"{grant.exercise_price:,.2f} {grant.currency or ''}"],
        ["Vesting start", schedule.start_date.isoformat()],
        ["Cliff (months)", str(schedule.cliff_months)],
        ["Total vesting (months)", str(schedule.total_months)],
        ["Trustee", _rtl(trustee.name) if trustee else "— none —"],
    ]
    return _render_pdf(f"Grant Letter — {_rtl(company.name)}", rows, document_id, version)


def build_section_102_appendix(grant: Grant, employee: Employee, company: Company,
                               trustee: Optional[Trustee], document_id: str,
                               version: int) -> tuple[str, str]:
    """נספח 102 - *** תבנית דמו מסומנת, לא נוסח משפטי אמיתי ***.

    ההחלטה המפורשת בתכנון v0.9.0: המערכת לא מנסחת תוכן משפטי אמיתי (אותו כלל
    כמו "לא ממציאים כלל מס"). התבנית מציגה את *הנתונים* של המסלול מתוך המענק,
    ולא טוענת לנוסח סטטוטורי - ראו _NOT_BINDING_NOTICE שמופיע בגוף המסמך.

    חסום למסלולים שאינם 102: נספח 102 לא חל על US_ISO/US_NSO. זו לא הכרעת מס
    חדשה - זו פשוט אי-תחולה של מסמך ישראלי על מסלול אמריקאי."""
    grant_type = _grant_type_value(grant)
    if not grant_type.startswith("IL_102"):
        raise MissingDocumentDataError(
            f"Grant {grant.grant_id} is {grant_type} - a Section 102 appendix applies only to "
            "Israeli Section 102 tracks (IL_102_CAPITAL_GAINS / IL_102_WORK_INCOME)."
        )
    if not trustee:
        raise MissingDocumentDataError(
            f"Grant {grant.grant_id} has no trustee - a Section 102 track is held in trust, "
            "so this appendix cannot be generated without one."
        )

    rows = [
        ["Employee", _rtl(f"{employee.first_name} {employee.last_name}")],
        ["National ID", employee.national_id or "— not on file —"],
        ["Company", _rtl(company.name)],
        ["Grant ID", grant.grant_id],
        ["Section 102 track", grant_type],
        ["Grant date", grant.grant_date.isoformat()],
        ["Total options", f"{grant.total_options:,.2f}"],
        ["Trustee", _rtl(trustee.name)],
        ["Trustee registration no.", trustee.registration_number],
        ["Trustee deposit date", grant.trustee_deposit_date.isoformat()
                                 if grant.trustee_deposit_date else "— not yet deposited —"],
    ]
    return _render_pdf("Section 102 Appendix (DEMO TEMPLATE — NOT REAL LEGAL TEXT)",
                       rows, document_id, version)


def build_trustee_deposit_confirmation(grant: Grant, employee: Employee, company: Company,
                                       trustee: Optional[Trustee], document_id: str,
                                       version: int) -> tuple[str, str]:
    """אישור הפקדה בנאמנות. דורש נאמן *וגם* תאריך הפקדה בפועל - אישור על הפקדה
    שלא קרתה הוא בדיוק סוג המסמך המטעה שאסור לייצר בשקט."""
    if not trustee:
        raise MissingDocumentDataError(
            f"Grant {grant.grant_id} has no trustee - cannot confirm a trustee deposit."
        )
    if not grant.trustee_deposit_date:
        raise MissingDocumentDataError(
            f"Grant {grant.grant_id} has no trustee deposit date on record - cannot confirm a "
            "deposit that has not been recorded. Confirm the deposit before generating this document."
        )

    rows = [
        ["Employee", _rtl(f"{employee.first_name} {employee.last_name}")],
        ["National ID", employee.national_id or "— not on file —"],
        ["Company", _rtl(company.name)],
        ["Grant ID", grant.grant_id],
        ["Grant type", _grant_type_value(grant)],
        ["Grant date", grant.grant_date.isoformat()],
        ["Total options deposited", f"{grant.total_options:,.2f}"],
        ["Trustee", _rtl(trustee.name)],
        ["Trustee registration no.", trustee.registration_number],
        ["Deposit date", grant.trustee_deposit_date.isoformat()],
    ]
    return _render_pdf(f"Trustee Deposit Confirmation — {_rtl(trustee.name)}",
                       rows, document_id, version)


# מיפוי סוג תבנית -> בונה. כל הבונים חולקים אותה חתימה בדיוק, כדי ש-routes.py
# יקרא להם בלי if/elif שגדל עם כל תבנית חדשה.
TEMPLATE_BUILDERS = {
    "GRANT_LETTER": build_grant_letter,
    "SECTION_102_APPENDIX": build_section_102_appendix,
    "TRUSTEE_DEPOSIT_CONFIRMATION": build_trustee_deposit_confirmation,
}
