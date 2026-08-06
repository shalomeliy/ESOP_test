"""מנוע יצירת מסמכי PDF (v0.9.0 שלב 1: כתב הענקה בלבד).

ReportLab (לא WeasyPrint) - ראו הערת requirements.txt. כל תבנית היא פונקציית
Python שמרכיבה flowables, לא תבנית HTML - עלות חד-פעמית של 3 תבניות קבועות,
לא עלות מתמשכת.

הקבצים יושבים ב-document_store/ (תיקייה מקומית, לא ב-git - בדיוק כמו
esop_database.db, ראו .gitignore). לעולם לא מוגשים כקובץ סטטי ישירות; רק
דרך endpoint מאומת שקורא ל-assert_document_access קודם.
"""

import hashlib
import os
from datetime import date
from pathlib import Path
from typing import Optional

from bidi import get_display
from reportlab.lib.pagesizes import A4
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


def build_grant_letter(grant: Grant, employee: Employee, company: Company,
                       trustee: Optional[Trustee], document_id: str, version: int) -> tuple[str, str]:
    """בונה PDF כתב הענקה, שומר ל-document_store/, ומחזיר (נתיב יחסי, sha256).

    זורק MissingDocumentDataError אם אין ללוח הבשלה בכלל - כשל מפורש, לא
    מסמך שמדלג בשקט על סעיף ההבשלה (החלטת התכנון v0.9.0, מקביל ל-
    MissingVestingScheduleError)."""
    schedule = grant.vesting_schedule
    if not schedule:
        raise MissingDocumentDataError(
            f"Grant {grant.grant_id} has no vesting schedule - cannot generate a grant letter "
            "without vesting terms. Attach a vesting schedule before generating this document."
        )

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
    story = []

    story.append(Paragraph(f"Grant Letter — {_rtl(company.name)}", styles["Title"]))
    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph(
        "This is an internally generated practice document (demo template, not reviewed legal "
        "counsel content) and does not constitute a legally binding agreement or signature.",
        styles["Italic"],
    ))
    story.append(Spacer(1, 1 * cm))

    employee_name = _rtl(f"{employee.first_name} {employee.last_name}")
    trustee_name = _rtl(trustee.name) if trustee else "— none —"
    rows = [
        ["Employee", employee_name],
        ["National ID", employee.national_id or "— not on file —"],
        ["Company", _rtl(company.name)],
        ["Grant ID", grant.grant_id],
        ["Grant date", grant.grant_date.isoformat()],
        ["Grant type", grant.grant_type.value if hasattr(grant.grant_type, "value") else grant.grant_type],
        ["Total options", f"{grant.total_options:,.2f}"],
        ["Exercise price", f"{grant.exercise_price:,.2f} {grant.currency or ''}"],
        ["Vesting start", schedule.start_date.isoformat()],
        ["Cliff (months)", str(schedule.cliff_months)],
        ["Total vesting (months)", str(schedule.total_months)],
        ["Trustee", trustee_name],
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
    story.append(Paragraph(
        "This document is generated for internal tracking only. It carries no acknowledgment "
        "of receipt until an explicit acknowledgment action is recorded in the system.",
        styles["Normal"],
    ))

    doc = SimpleDocTemplate(str(full_path), pagesize=A4)
    doc.build(story)

    file_hash = _sha256_of_file(full_path)
    return relative_path, file_hash
