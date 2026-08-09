"""אינווריאנטים של הפרויקט — בדיקות ברמת הריפו, לא ברמת הקוד.

למה הקובץ הזה קיים
------------------
ב-06/08/2026 התגלה ש-`VERSION` נשאר על 0.5.1 בזמן ש-v0.6.0 ו-v0.7.0 כבר
נחתו ב-main. שלושת הפורטלים הציגו למשתמשים גרסה שקרית, וכותרת ה-OpenAPI איתם.
אף בדיקה לא יכלה לתפוס את זה, כי לא היתה בדיקה שמסתכלת על הריפו כמערכת.

הכלל שנגזר מזה: **כל באג שנתפס פעם אחת הופך לבדיקה קבועה.**
זו הגרסה האכיפה של דפוסי P1-P6 ב-QA_TESTBOOK — לא צ'קליסט לזיכרון, קוד שרץ.

הבדיקות כאן דטרמיניסטיות ומסתכלות על קבצים בלבד. אין בהן ניתוח קוד היוריסטי,
בכוונה: בדיקה שנופלת על false positive היא בדיקה שמישהו ידחף לה ignore,
וזה מנוגד לכלל "לא מחלישים בדיקה".
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
QA_DIR = ROOT / "docs" / "qa"
TESTBOOK = ROOT / "QA_TESTBOOK.md"

# תקציב הגודל של האינדקס. ב-06/08/2026 הקובץ המאוחד היה 52,897 תווים
# (~18,400 טוקנים) ונקרא מחדש בכל תור. הוא פוצל ל-docs/qa/<גרסה>.md
# והאינדקס ירד ל-~3,500. התקציב הזה קיים כדי שהוא לא יזחל בחזרה למעלה.
TESTBOOK_CHAR_BUDGET = 8_000


def _semver(text: str) -> tuple[int, int, int]:
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", text.strip())
    assert m, f"גרסה בפורמט לא מזוהה: {text!r}"
    return tuple(int(g) for g in m.groups())  # type: ignore[return-value]


def _declared_version() -> str:
    return (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def _qa_files() -> dict[str, Path]:
    """כל docs/qa/vX.Y.Z.md — לפי שם הקובץ. _TEMPLATE ו-appendices לא נספרים."""
    out = {}
    for p in QA_DIR.glob("v*.md"):
        if re.fullmatch(r"v\d+\.\d+\.\d+", p.stem):
            out[p.stem] = p
    return out


def _indexed_versions() -> set[str]:
    """הגרסאות שמופיעות בטבלת האינדקס ב-QA_TESTBOOK.md."""
    text = TESTBOOK.read_text(encoding="utf-8")
    return set(re.findall(r"docs/qa/(v\d+\.\d+\.\d+)\.md", text))


# ---------------------------------------------------------------- VERSION drift

def test_version_file_is_not_behind_the_qa_testbook():
    """VERSION לא נשאר מאחור מול הגרסאות שיש להן ספר בדיקות.

    זו הבדיקה שתופסת את הבאג של 06/08/2026. היא **לא** דורשת שוויון:
    בזמן פיתוח פעיל VERSION יכול להיות קדימה (0.9.0) לפני שנכתב
    docs/qa/v0.9.0.md, וזה תקין. מה שאסור הוא ההפוך.
    """
    qa = _qa_files()
    assert qa, "אין אף docs/qa/vX.Y.Z.md — האינדקס לא אמור להיות ריק"

    declared = _declared_version()
    newest_qa = max(qa, key=_semver)

    assert _semver(declared) >= _semver(newest_qa), (
        f"VERSION={declared} אבל יש כבר ספר בדיקות ל-{newest_qa}.\n"
        f"כלומר גרסה נסגרה בלי bump ל-VERSION — והפורטלים מציגים גרסה שקרית\n"
        f"(version.py קורא את הקובץ, main.py מזין ממנו את כותרת ה-OpenAPI,\n"
        f"ו-GET /api/v1/version מגיש אותו לשלושת הפורטלים).\n"
        f"התיקון: release-manager מבצע bump ל-VERSION — הוא הסוכן היחיד שרשאי."
    )


# --------------------------------------------------------- אינדקס ↔ קבצים בפועל

def test_every_qa_file_is_listed_in_the_index():
    """קובץ גרסה שקיים על הדיסק אבל לא באינדקס = ידע שאף אחד לא ימצא."""
    missing = sorted(set(_qa_files()) - _indexed_versions())
    assert not missing, (
        f"הקבצים האלה קיימים ב-docs/qa/ אבל לא מקושרים מ-QA_TESTBOOK.md: {missing}\n"
        f"הוסף להם שורה בטבלת הגרסאות שם."
    )


def test_every_indexed_version_has_a_file():
    """קישור באינדקס לקובץ שלא קיים = קישור שבור."""
    dangling = sorted(_indexed_versions() - set(_qa_files()))
    assert not dangling, (
        f"QA_TESTBOOK.md מקשר לקבצים שלא קיימים: {dangling}\n"
        f"או שהקובץ נמחק, או שהקישור שגוי."
    )


# ------------------------------------------------- שמירה על הפיצול (מניעת רגרסיה)

def test_testbook_index_stays_small():
    """האינדקס נטען בכל תור. הוא לא אמור לחזור להיות מונוליט.

    מקרי הבדיקה נכנסים ל-docs/qa/<גרסה>.md, לא לכאן. האינדקס מחזיק
    רק את מה שנכון לכל הגרסאות: המבנה, הכנת הסביבה, ודפוסי P1-P6.
    """
    size = len(TESTBOOK.read_text(encoding="utf-8"))
    assert size <= TESTBOOK_CHAR_BUDGET, (
        f"QA_TESTBOOK.md הוא {size:,} תווים, מעל התקציב של {TESTBOOK_CHAR_BUDGET:,}.\n"
        f"סימן שמקרי בדיקה נכתבו לאינדקס במקום ל-docs/qa/<גרסה>.md.\n"
        f"הקובץ נטען בכל תור — כל תו כאן מוכפל באלף."
    )


def test_failure_patterns_are_present_in_the_index():
    """P1-P6 חייבים להישאר באינדקס — הם הרשימה שנבדקת בכל פיצ'ר חדש."""
    text = TESTBOOK.read_text(encoding="utf-8")
    for pattern in ("P1", "P2", "P3", "P4", "P5", "P6"):
        assert re.search(rf"\|\s*{pattern}\s*\|", text), (
            f"דפוס {pattern} נעלם מטבלת דפוסי הכשל ב-QA_TESTBOOK.md.\n"
            f"אלה לא רשימה תיאורטית — כל אחד מהם ייצר יותר מבאג אחד במערכת הזו."
        )


# ------------------------------------------------------------------------- הבא בתור
#
# בדיקות שכדאי להוסיף כאן כשתיתפס עוד פעם אותה מחלקת באג. הכלל: קודם
# הבאג נתפס בפועל, אחר כך הופך לבדיקה — לא לנחש מראש.
#
#   P2 (IDOR)  — כל route עם {id} מהלקוח עובר דרך בדיקת בעלות מהסשן
#   P4 (None→0) — אין `or 0` על שדות vested/tax/amount
#   P1 (תאריכים) — חשבון חודשים עובר דרך פונקציית עזר, לא `// 12` גולמי
#
# בוצע ב-v0.9.1: שדות TaxCalculationResult. הפער האמיתי לא היה האריתמטיקה
# (שהייתה מכוסה היטב) אלא **שרשור המקורות** — source_url לא נבדק מעולם
# ו-pack_id רק כ-is not None, כך שמנוע שבוחר את החבילה הלא נכונה עם אותו
# שיעור היה עובר הכל. ראו tests/test_tax_engine.py, סעיף שרשור המקורות.


# ---------------------------------------------------------------------------
# נאמנות חותמות זמן (v0.9.1). הבאג: datetime.utcnow() מחזיר naive, ו-SQLite
# משמיט את ההיסט בשקט - כלומר שאילתה בי-טמפורלית החזירה תשובה שגויה בלי
# חריגה. תיקון נקודתי היה נסוג בשקט בפעם הבאה שמישהו כותב utcnow() מתוך הרגל.
# ---------------------------------------------------------------------------

def _python_sources(*relative_dirs: str) -> list[Path]:
    files: list[Path] = []
    for rel in relative_dirs:
        files.extend(
            p for p in (ROOT / rel).rglob("*.py") if "__pycache__" not in p.parts
        )
    return files


def test_no_naive_utcnow_in_backend():
    """``datetime.utcnow()`` אסור ב-backend. ``backend/app/types.py`` הוא
    החריג היחיד, ורק בטקסט התיעוד שמסביר למה הוא אסור."""
    offenders = [
        f"{p.relative_to(ROOT)}:{i}"
        for p in _python_sources("backend")
        if p != ROOT / "backend" / "app" / "types.py"
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if "datetime.utcnow(" in line
    ]
    assert not offenders, (
        "datetime.utcnow() מחזיר naive. השתמשו ב-backend.app.types.utcnow(). "
        f"נמצא ב: {offenders}"
    )


def test_no_host_local_date_today_in_backend():
    """``date.today()`` מחזיר את תאריך המארח. החריג היחיד המותר הוא
    ``routes.py`` בהשמת ``termination_date``, שמנומק שם בהערה ורשום כחוב פתוח."""
    offenders = [
        f"{p.relative_to(ROOT)}:{i}"
        for p in _python_sources("backend")
        if p != ROOT / "backend" / "app" / "types.py"
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if "date.today()" in line
        and not line.lstrip().startswith("#")
        # ``date.today()`` בגרשיים כפולים הוא אזכור בתיעוד, לא קריאה
        and "``date.today()``" not in line
        and "termination_date" not in line
    ]
    assert not offenders, (
        "date.today() תלוי באזור הזמן של המארח. השתמשו ב-"
        f"backend.app.types.system_today_utc(). נמצא ב: {offenders}"
    )


def test_the_clock_is_never_the_source_of_a_tax_date():
    """תאריך בעל משמעות מסית מגיע מהמסמך ולא משעון - בשום אזור זמן.

    ארה"ב מאחורי UTC וישראל לפניו, ולכן אין שעון יחיד שהוא שמרני לשתי
    המדינות. אימות מלא ב-docs/qa/v0.9.1.md.
    """
    tax_dated_fields = ("grant_date", "trustee_deposit_date", "exercise_date")
    clocks = ("system_today_utc()", "date.today()", "utcnow()")
    offenders = [
        f"{p.relative_to(ROOT)}:{i}  {line.strip()}"
        for p in _python_sources("backend")
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if not line.lstrip().startswith("#")
        and any(f"{field} =" in line or f"{field}=" in line for field in tax_dated_fields)
        and any(clock in line for clock in clocks)
    ]
    assert not offenders, (
        "תאריך מס נגזר משעון. הוא חייב להגיע מהמסמך (החלטת דירקטוריון, אישור "
        f"הפקדה, הודעת מימוש). נמצא ב: {offenders}"
    )


# ---------------------------------------------------------------------------
# database/init_scheme.sql הוא תיעוד בלבד, אבל CLAUDE.md שורה 17 מפנה אליו
# כמקור לאימות לוגיקת דומיין. ב-09/08/2026 התגלה שהוא עצר ב-0.5.0 וחסרו בו
# ארבע טבלאות וכל הטריגרים - כלומר הוא לא היה מיושן, הוא היה מטעה.
# ---------------------------------------------------------------------------

def test_init_scheme_sql_documents_every_table_in_the_models():
    import sqlite3

    from backend.app.database import Base
    import backend.app.models  # noqa: F401  -- רושם את הטבלאות על Base.metadata

    conn = sqlite3.connect(":memory:")
    conn.executescript((ROOT / "database" / "init_scheme.sql").read_text(encoding="utf-8"))
    documented = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    } - {"alembic_version"}

    modelled = set(Base.metadata.tables)
    assert not modelled - documented, (
        "טבלאות שקיימות במודלים ולא מתועדות ב-init_scheme.sql: "
        f"{sorted(modelled - documented)}"
    )
    assert not documented - modelled, (
        "טבלאות שמתועדות ב-init_scheme.sql ולא קיימות במודלים: "
        f"{sorted(documented - modelled)}"
    )


def test_init_scheme_sql_documents_the_triggers():
    """הטריגרים הם המקום היחיד שבו append-only נאכף בפועל. קובץ סכמה שמראה
    את הטבלה בלי הטריגר משדר שאפשר לעדכן אותה."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.executescript((ROOT / "database" / "init_scheme.sql").read_text(encoding="utf-8"))
    triggers = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}

    assert triggers >= {
        "trg_ledger_events_no_update",
        "trg_ledger_events_no_delete",
        "trg_documents_no_update_once_acknowledged",
        "trg_documents_no_delete_once_acknowledged",
    }, f"טריגרים חסרים ב-init_scheme.sql. נמצאו: {sorted(triggers)}"
