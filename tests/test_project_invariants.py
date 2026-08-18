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

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
QA_DIR = ROOT / "docs" / "qa"
TESTBOOK = ROOT / "QA_TESTBOOK.md"
HANDOFF = ROOT / "HANDOFF.md"
HANDOFF_ARCHIVE_DIR = ROOT / "docs" / "handoff"

# תקציב הגודל של האינדקס. ב-06/08/2026 הקובץ המאוחד היה 52,897 תווים
# (~18,400 טוקנים) ונקרא מחדש בכל תור. הוא פוצל ל-docs/qa/<גרסה>.md
# והאינדקס ירד ל-~3,500. התקציב הזה קיים כדי שהוא לא יזחל בחזרה למעלה.
TESTBOOK_CHAR_BUDGET = 8_000

# אותו כשל בדיוק, קובץ אחר. ב-14/08/2026 HANDOFF.md היה 97,449 תווים -
# פי 15 מהאינדקס שכבר פוצל בגלל הגודל, והוא זה שנקרא *ראשון* בכל שיחה.
# הסיבה: הוא היה append-only, בלוק לכל גרסה, ואף אחד לא מחק. פוצל ל-
# docs/handoff/<גרסה>.md. התקציב נדיב יותר משל האינדקס כי כאן יושב גם
# החוב הפתוח וגם ההחלטות העומדות - אבל הוא תקרה, לא יעד. המרווח (~23% מעל
# הגודל אחרי הפיצול) הוא אותו יחס בדיוק כמו TESTBOOK_CHAR_BUDGET מול האינדקס
# בפועל - תקציב חונק מייצר לחץ להחליש את הבדיקה, וזה בדיוק מה שאסור.
HANDOFF_CHAR_BUDGET = 14_000


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


def test_handoff_stays_small():
    """HANDOFF.md נקרא *ראשון* בכל שיחה. הוא מחזיק הווה, לא היסטוריה.

    כשגרסה נסגרת, הבלוק שלה עובר ל-docs/handoff/<גרסה>.md. הקובץ הזה
    מחזיק רק: מצב נוכחי, הצעד הבא, החלטות עומדות, וחוב פתוח.
    """
    size = len(HANDOFF.read_text(encoding="utf-8"))
    assert size <= HANDOFF_CHAR_BUDGET, (
        f"HANDOFF.md הוא {size:,} תווים, מעל התקציב של {HANDOFF_CHAR_BUDGET:,}.\n"
        f"סימן שבלוק של גרסה שנסגרה נצבר כאן במקום לעבור ל-docs/handoff/<גרסה>.md.\n"
        f"הקובץ נקרא ראשון בכל שיחה — כל תו כאן משולם מחדש בכל תור."
    )


def test_handoff_archive_points_back_at_the_active_file():
    """ארכיון בלי הפניה חזרה הוא ארכיון שמישהו יקרא בטעות כמצב נוכחי.

    כל קובץ ב-docs/handoff/ חייב להצהיר שהוא ארכיון ולהפנות ל-HANDOFF.md.
    """
    archives = sorted(HANDOFF_ARCHIVE_DIR.glob("*.md"))
    assert archives, "docs/handoff/ ריק — הפיצול של 14/08/2026 בוטל או הועבר?"
    for path in archives:
        text = path.read_text(encoding="utf-8")
        assert "ארכיון" in text and "HANDOFF.md" in text, (
            f"{path.name} לא מצהיר שהוא ארכיון ולא מפנה חזרה ל-HANDOFF.md.\n"
            f"בלי זה, שיחה עתידית עלולה לקרוא מצב ישן כאילו הוא הנוכחי."
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
    """``date.today()`` מחזיר את תאריך המארח. **אין חריגים.**

    עד v0.9.1 היה חריג אחד ל-``termination_date``. הוא נסגר כשהתברר שהוא מזין
    את דדליין חלון המימוש שנבדק על שעון אחר - שני הצדדים של אותו חישוב הסכימו
    רק כל עוד המארח מוגדר לישראל.
    """
    offenders = [
        f"{p.relative_to(ROOT)}:{i}"
        for p in _python_sources("backend")
        if p != ROOT / "backend" / "app" / "types.py"
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if "date.today()" in line
        and not line.lstrip().startswith("#")
        # ``date.today()`` בגרשיים כפולים הוא אזכור בתיעוד, לא קריאה
        and "``date.today()``" not in line
    ]
    assert not offenders, (
        "date.today() תלוי באזור הזמן של המארח. השתמשו ב-"
        f"backend.app.types.business_today(). נמצא ב: {offenders}"
    )


def test_app_layer_runs_on_the_business_clock_not_utc():
    """``system_today_utc()`` אסור בשכבת האפליקציה - זו הרגרסיה ח1/ח2.

    ישראל לפני UTC, ולכן בין 00:00 ל-03:00 תאריך ה-UTC הוא *אתמול*. שלב א של
    v0.9.1 העביר גבולות מזכים ו-``effective_date`` לשעון הזה, ואת האחרון לטבלה
    append-only שאין בה UPDATE. הבדיקה הזו קיימת כי ה-grep שאיתר את האתרים
    האלה בסקירה ידנית לא ירוץ שוב מעצמו.

    ``backend/seed_data.py`` מחוץ לתחום בכוונה: זריעת נתונים אינה גבול מזכה.

    שלוש האיותים ולא אחד: איסור על השם ``system_today_utc`` בלבד היה חוסם את
    הניסוח ומשאיר את הבאג - ``utcnow().date()`` הוא בדיוק אותו ערך, כתוב אחרת.
    """
    utc_dates = ("system_today_utc(", "utcnow().date()", "datetime.now(timezone.utc).date()")
    offenders = [
        f"{p.relative_to(ROOT)}:{i}  {line.strip()}"
        for p in _python_sources("backend/app")
        if p != ROOT / "backend" / "app" / "types.py"
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if not line.lstrip().startswith("#")
        and any(spelling in line for spelling in utc_dates)
    ]
    assert not offenders, (
        "תאריך לפי UTC אינו יום העסקים. כל גבול שמעניק זכות, וכל effective_date, "
        f"רצים על backend.app.types.business_today(). נמצא ב: {offenders}"
    )


def test_the_clock_is_never_the_source_of_a_tax_date():
    """תאריך בעל משמעות מסית מגיע מהמסמך ולא משעון - בשום אזור זמן.

    ארה"ב מאחורי UTC וישראל לפניו, ולכן אין שעון יחיד שהוא שמרני לשתי
    המדינות. אימות מלא ב-docs/qa/v0.9.1.md.
    """
    tax_dated_fields = ("grant_date", "trustee_deposit_date", "exercise_date")
    # business_today() נוסף ב-v0.9.1: הוא השעון הנכון לגבול מזכה, אבל **אינו**
    # מקור לתאריך מס יותר משהיו קודמיו. שעון עסקי מדויק הוא עדיין שעון.
    clocks = ("system_today_utc()", "business_today()", "date.today()", "utcnow()")
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

def _sqlite_shape(conn):
    """צורת הסכמה כפי ש-SQLite עצמו רואה אותה, לא כפי שהיא כתובה.

    דרך PRAGMA ולא דרך טקסט ה-DDL בכוונה: אילוץ שנכתב בשורת העמודה
    (``pack_id VARCHAR UNIQUE``) ואילוץ שנכתב בסוף הטבלה (``CONSTRAINT ... UNIQUE``)
    הם אותו דבר עבור ה-DB ושונים לחלוטין כטקסט. השוואת טקסט הייתה נכשלת על
    עיצוב ועוברת על דריפט אמיתי - בדיוק הפוך מהנדרש.
    """
    shape = {}
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    for table in tables:
        if table == "alembic_version":
            continue  # טבלת הניהול של Alembic - לא מודל, ואין לה מקבילה ב-models.py
        shape[table] = {
            # (type, notnull, pk) לכל עמודה. ברירות מחדל *לא* נכללות: רובן
            # מיושמות ב-Python בזמן INSERT ולא ב-DB, ולכן הן לא נמצאות בשני הצדדים.
            "columns": {row[1]: (row[2].upper(), row[3], row[5]) for row in conn.execute(f"PRAGMA table_info('{table}')")},
            "foreign_keys": {(row[3], row[2], row[4]) for row in conn.execute(f"PRAGMA foreign_key_list('{table}')")},
            "unique": {
                tuple(part[2] for part in conn.execute(f"PRAGMA index_info('{row[1]}')"))
                for row in conn.execute(f"PRAGMA index_list('{table}')")
                if row[2]
            },
        }
    return shape


def _documented_shape():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.executescript((ROOT / "database" / "init_scheme.sql").read_text(encoding="utf-8"))
    return _sqlite_shape(conn)


def _modelled_shape():
    import sqlalchemy as sa

    from backend.app.database import Base
    import backend.app.models  # noqa: F401  -- רושם את הטבלאות על Base.metadata

    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine)
    connection = engine.raw_connection()
    try:
        return _sqlite_shape(connection.driver_connection)
    finally:
        connection.close()


def test_init_scheme_sql_documents_every_table_in_the_models():
    documented, modelled = set(_documented_shape()), set(_modelled_shape())

    assert not modelled - documented, (
        "טבלאות שקיימות במודלים ולא מתועדות ב-init_scheme.sql: "
        f"{sorted(modelled - documented)}"
    )
    assert not documented - modelled, (
        "טבלאות שמתועדות ב-init_scheme.sql ולא קיימות במודלים: "
        f"{sorted(documented - modelled)}"
    )


def test_init_scheme_sql_matches_the_models_column_by_column():
    """שמות טבלאות בלבד אינם סנכרון - זו הייתה התקלה עצמה.

    הבדיקה הקודמת השוותה רק את קבוצת שמות הטבלאות, ולכן הייתה ירוקה מול קובץ
    שחסרו בו ``employees.national_id``, שלוש עמודות נעילת החשבון ב-``users``,
    ``pack_id`` ושני מפתחות זרים ושלושה אילוצי UNIQUE. כלומר היא *אישרה* את
    הדריפט כמתוקן. בדיקה שירוקה מול הבאג שהיא נכתבה כדי לתפוס גרועה מאין בדיקה,
    כי היא מסירה את הדריכות.
    """
    documented, modelled = _documented_shape(), _modelled_shape()
    problems = []

    for table in sorted(set(documented) & set(modelled)):
        doc, mod = documented[table], modelled[table]

        for column in sorted(set(mod["columns"]) - set(doc["columns"])):
            problems.append(f"{table}.{column} - קיימת במודל וחסרה ב-init_scheme.sql")
        for column in sorted(set(doc["columns"]) - set(mod["columns"])):
            problems.append(f"{table}.{column} - מתועדת ב-init_scheme.sql ואינה קיימת במודל")
        for column in sorted(set(mod["columns"]) & set(doc["columns"])):
            if mod["columns"][column] != doc["columns"][column]:
                problems.append(
                    f"{table}.{column} - (type, notnull, pk) שונה: "
                    f"מודל={mod['columns'][column]} init_scheme={doc['columns'][column]}"
                )

        for fk in sorted(mod["foreign_keys"] - doc["foreign_keys"]):
            problems.append(f"{table} - מפתח זר חסר ב-init_scheme.sql: {fk}")
        for fk in sorted(doc["foreign_keys"] - mod["foreign_keys"]):
            problems.append(f"{table} - מפתח זר מתועד שאינו במודל: {fk}")

        for uniq in sorted(mod["unique"] - doc["unique"]):
            problems.append(f"{table} - אילוץ UNIQUE חסר ב-init_scheme.sql: {uniq}")
        for uniq in sorted(doc["unique"] - mod["unique"]):
            problems.append(f"{table} - אילוץ UNIQUE מתועד שאינו במודל: {uniq}")

    assert not problems, (
        "init_scheme.sql אינו תואם את המודלים. הקובץ מצהיר בכותרתו שהוא נגזר "
        "מ-alembic upgrade head, ולכן דריפט כאן הוא תיעוד מטעה ולא תיעוד חסר:\n  "
        + "\n  ".join(problems)
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


# ---------------------------------------------------------------------------
# פיצול routes.py (v0.9.1). הקובץ המונוליטי (1,507 שורות, 48 endpoints) פוצל
# ל-12 ראוטרים לפי תחום תחת backend/app/api/. שני הכשלים שריפקטור כזה עלול
# להכניס בשקט: שני routers שנרשמים על אותו path בטעות (אחד מסתיר את השני),
# וקובץ ראוטר חדש שנוצר אבל נשכח מ-include_router ב-main.py (שקט לגמרי -
# ה-import לא נכשל, ה-endpoint פשוט לא קיים).
# ---------------------------------------------------------------------------

def _all_mounted_api_routes():
    """כל ה-APIRoute-ים שבאמת מורכבים על ה-app, כולל אלה שיושבים מאחורי
    ה-wrapper של include_router (``_IncludedRouter.original_router``) -
    ב-FastAPI העדכני include_router לא שוטח את הראוטרים מיד."""
    from fastapi.routing import APIRoute
    from backend.app.main import app

    def walk(routes):
        for r in routes:
            if isinstance(r, APIRoute):
                yield r
            elif hasattr(r, "original_router"):
                yield from walk(r.original_router.routes)
            elif hasattr(r, "routes"):
                yield from walk(r.routes)

    return list(walk(app.routes))


def _api_router_module_names() -> list[str]:
    """כל קובץ תחת backend/app/api/ שמגדיר router משלו (כל .py חוץ מ-__init__)."""
    api_dir = ROOT / "backend" / "app" / "api"
    return sorted(p.stem for p in api_dir.glob("*.py") if p.stem != "__init__")


def test_no_duplicate_path_method_pairs_across_routers():
    """שני ראוטרים (בקבצים שונים) שנרשמים בטעות על אותו (path, method) - אחד
    מהם מסתיר את השני לפי סדר הרישום ב-main.py, בלי שגיאה. עם קובץ אחד זה
    היה בולט לעין; מפוצל בין 12 קבצים זה לא."""
    seen: dict[tuple[str, str], object] = {}
    duplicates = []
    for route in _all_mounted_api_routes():
        for method in route.methods:
            key = (route.path, method)
            owner = seen.get(key)
            if owner is not None and owner is not route:
                duplicates.append(f"{method} {route.path}")
            seen[key] = route

    assert not duplicates, (
        "יותר מ-route אחד רשום על אותו (path, method) - הראשון שנכלל ב-main.py "
        f"זוכה, השני מוסתר בשקט: {sorted(set(duplicates))}"
    )


def test_every_api_router_module_is_mounted_in_main():
    """כל קובץ תחת backend/app/api/ שמגדיר router חייב להיות מוכלל ב-main.py
    בדיוק פעם אחת. קובץ ראוטר חדש ש"נשכח" מ-include_router לא נכשל בשום
    import - ה-endpoints שבו פשוט לא קיימים, בלי אינדיקציה."""
    import importlib

    mounted_router_ids = {id(r.original_router) for r in _iter_included_routers()}

    unmounted = []
    for name in _api_router_module_names():
        module = importlib.import_module(f"backend.app.api.{name}")
        router = getattr(module, "router", None)
        assert router is not None, f"backend/app/api/{name}.py חסר משתנה module-level בשם router"
        if id(router) not in mounted_router_ids:
            unmounted.append(name)

    assert not unmounted, (
        f"הקבצים האלה מגדירים router אבל הוא לא מוכלל ב-main.py: {unmounted}\n"
        f"הוסף app.include_router({unmounted[0] if unmounted else '...'}.router, prefix=\"/api/v1\") שם."
    )


def _iter_included_routers():
    from backend.app.main import app

    for r in app.routes:
        if hasattr(r, "original_router"):
            yield r


# ---------------------------------------------------------------------------
# ייצוא/ייבוא (v1.0.2, HANDOFF.md debt item 1). ShareClass/Shareholder/
# ShareIssuance נשכחו פעם אחת מ-_FORCE_COMPANY_ID_TABLES (v1.0.1) - export.py
# ו-import_.py תיארו אותן טבלאות בשני מבנים עצמאיים שיכלו לחרוג. אוחדו
# ל-company_scope.TABLE_REGISTRY, אבל איחוד לבדו לא מבטיח שטבלה חדשה עם
# עמודת company_id תמיד תוצהר נכון - רק בדיקה שרצה בכל build עושה זאת.
# ---------------------------------------------------------------------------

def _models_with_company_id_column():
    """כל טבלה (models.py, דרך Base.metadata) שיש לה עמודה בשם company_id
    ממש - לא fk כלשהו לחברה בשם אחר (למשל source_company_id/target_company_id
    ב-DataTransferRun, שהם קשר בין-חברות במפורש, לא "השורה הזו שייכת לחברה
    X"). זה בדיוק העמודה ש-TableSpec.force_company_id/import_.py::_build_row
    דורסים - האינווריאנט הזה בודק אותה עמודה ולא אחרת."""
    from backend.app.database import Base
    import backend.app.models  # noqa: F401 -- רושם את הטבלאות על Base.metadata

    return {
        table_name: table
        for table_name, table in Base.metadata.tables.items()
        if "company_id" in table.columns
    }


def test_every_company_scoped_table_is_registered_or_explicitly_special_cased():
    """כל טבלה עם עמודת company_id בפועל חייבת להופיע או ב-
    company_scope.TABLE_REGISTRY (ומצהירה force_company_id) או ב-
    company_scope.SPECIAL_CASED_TABLES (עם סיבה מתועדת) - אין דרך שלישית
    להישאר בשקט מחוץ לשניהם. זו בדיוק צורת הבאג שכבר קרה: טבלה חדשה
    שמישהו הוסיף לצד אחד (export.py) ושכח מהצד השני (_FORCE_COMPANY_ID_TABLES,
    import_.py)."""
    from backend.app.services.company_scope import TABLE_REGISTRY, SPECIAL_CASED_TABLES

    company_scoped = set(_models_with_company_id_column())
    registered = set(TABLE_REGISTRY)
    special_cased = set(SPECIAL_CASED_TABLES)

    unaccounted = company_scoped - registered - special_cased
    assert not unaccounted, (
        "טבלאות עם עמודת company_id שלא רשומות ב-TABLE_REGISTRY וגם לא "
        f"ב-SPECIAL_CASED_TABLES (company_scope.py): {sorted(unaccounted)}"
    )

    # 7 מתוך 11 טבלאות ה-TABLE_REGISTRY נושאות company_id ישיר (השאר scoped
    # דרך שרשור FK - grants/vesting_schedules/exercise_requests/
    # exercise_tax_records) - וכל אחת מה-7 האלה חייבת להצהיר
    # force_company_id=True, אחרת commit() לא ידרוס company_id זר מהקובץ.
    unforced = [
        table_name for table_name in (company_scoped & registered)
        if not TABLE_REGISTRY[table_name].force_company_id
    ]
    assert not unforced, (
        f"הטבלאות האלה נושאות עמודת company_id בפועל אבל ה-TableSpec שלהן "
        f"ב-TABLE_REGISTRY לא מצהיר force_company_id=True: {sorted(unforced)}"
    )


# ---------------------------------------------------------------------------
# v1.2.0 (B11, מפרט §11.4): אירוע שנכתב ולא משנה כלום בשקט.
#
# ה-ledger הוא append-only, ולכן סוג אירוע חדש נכתב בהצלחה גם כשאף פרויקטור
# אינו יודע לקפל אותו - התוצאה אינה שגיאה אלא מספר שלא זז. זה בדיוק הכשל
# שקרה עם POOL_ALLOCATED (ראו ההערה על LEDGER_EPOCH ב-services/ledger.py),
# והוא מתגלה רק כשמישהו משווה ידנית עמודה מוטטת מול פרויקציה.
# ---------------------------------------------------------------------------

_LEDGER_SOURCE = ROOT / "backend" / "app" / "services" / "ledger.py"


def _event_types_each_projector_branches_on() -> dict[str, set[str]]:
    """כל מחרוזת שמושווית מול ``e.event_type`` בתוך פונקציית קיפול, לפי AST.

    **לא** "כל מחרוזת שמופיעה בפונקציה": ה-docstring של
    ``project_share_issuance`` מזכיר בשמם שלושה סוגי אירוע, ובהם POOL_ALLOCATED
    שאינו שלו כלל - קריאה תמימה של מחרוזות הייתה מכריזה על כיסוי מתוך תיעוד.
    **ולא regex**: ``e.event_type == "X"`` מפוצל על פני שורות בכמה מהפרויקטורים,
    ובדיקה שנופלת על עיצוב קוד היא בדיקה שידחפו לה ignore.

    ``in (...)`` נתמך אף שאף פרויקטור אינו משתמש בו היום - אחרת האיחוד הראשון
    של שני סוגים לענף אחד היה מפיל את הבדיקה על צורה תקינה לחלוטין."""
    tree = ast.parse(_LEDGER_SOURCE.read_text(encoding="utf-8"))

    projectors: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "PROJECTORS" for target in node.targets
        ):
            projectors = {value.id for value in node.value.values if isinstance(value, ast.Name)}
    assert projectors, "לא נמצא מיפוי PROJECTORS ב-services/ledger.py"

    branched: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name in projectors):
            continue
        found: set[str] = set()
        for compare in ast.walk(node):
            if not isinstance(compare, ast.Compare):
                continue
            if not (isinstance(compare.left, ast.Attribute) and compare.left.attr == "event_type"):
                continue
            for op, comparator in zip(compare.ops, compare.comparators):
                if isinstance(op, ast.Eq):
                    literals = [comparator]
                elif isinstance(op, ast.In) and isinstance(comparator, (ast.Tuple, ast.List, ast.Set)):
                    literals = list(comparator.elts)
                else:
                    continue
                found.update(
                    literal.value for literal in literals
                    if isinstance(literal, ast.Constant) and isinstance(literal.value, str)
                )
        branched[node.name] = found

    unparsed = projectors - set(branched)
    assert not unparsed, (
        f"PROJECTORS מפנה לשמות שאינם def ברמת המודול ב-ledger.py: {sorted(unparsed)}"
    )
    return branched


def test_every_ledger_event_type_is_folded_by_some_projector():
    """כל טיפוס ב-LEDGER_EVENT_TYPES מופיע בענף מפורש **באחד** מהפרויקטורים.

    הצורה חלשה בכוונה (״באחד״, בלי לדרוש איזה): LEDGER_EVENT_TYPES היא קבוצה
    שטוחה בלי מיפוי לסוג הצובר, והשם אינו מפתח אמין - GRANT_CREATED
    ו-TRUSTEE_DEPOSIT_CONFIRMED שייכים שניהם ל-Grant. מיפוי טיפוס→צובר הוא
    שיפור עתידי; הצורה הזו כבר תופסת את הכשל שבגללו היא נכתבת."""
    from backend.app.models import LEDGER_EVENT_TYPES

    folded = set().union(*_event_types_each_projector_branches_on().values())

    never_folded = LEDGER_EVENT_TYPES - folded
    assert not never_folded, (
        f"סוגי אירוע שאף פרויקטור אינו מקפל: {sorted(never_folded)}. "
        "אירוע כזה נכתב ל-ledger בהצלחה ולא משנה שום מספר - כשל שקט. "
        "הוסף ענף ב-services/ledger.py, או הסר את הטיפוס מ-LEDGER_EVENT_TYPES."
    )


def test_no_projector_branches_on_an_event_type_that_cannot_be_written():
    """אותו כשל מהכיוון השני: ענף שמשווה מול מחרוזת שאינה ב-LEDGER_EVENT_TYPES
    לעולם אינו נכנס, כי append_event דוחה כל טיפוס שאינו בקבוצה. שגיאת כתיב
    בשם אירוע נראית כמו קוד עובד ומתנהגת כמו קוד חסר."""
    from backend.app.models import LEDGER_EVENT_TYPES

    branched = _event_types_each_projector_branches_on()
    unknown = {
        f"{function}: {event_type}"
        for function, types in branched.items()
        for event_type in types - LEDGER_EVENT_TYPES
    }
    assert not unknown, (
        f"ענפי קיפול על סוגי אירוע שאינם ב-LEDGER_EVENT_TYPES: {sorted(unknown)}"
    )


# ===================================================================
# v1.1.1 פריט ד1: הבדיקות לא כותבות לתיקיות העבודה של הפרויקט
# ===================================================================

def test_file_stores_are_redirected_out_of_the_project_tree_during_tests():
    """שלוש דליפות באותו שורש, שנתפסו אחת-אחת: ה-DB החי (נסגר ב-conftest עם
    ESOP_DATABASE_URL), document_store (נסגר ב-test_documents.py) ו-export_store
    (נשאר פתוח עד v1.1.1 - 3,427 קבצים, 19MB, 100% יתומים).

    כולן אותו דפוס: קבוע ברמת מודול שנגזר מ-``__file__``, שבדיקה שלא חשבה על
    הנושא לא יודעת שהיא צריכה לעקוף. הבדיקה הזו הופכת את זה ממשמעת לאכיפה -
    אם store רביעי ייווסף ולא ייעקף, או שהפיקסצ'ר ייעלם, זה ייפול כאן ולא
    יתגלה בעוד שנה לפי גודל תיקייה."""
    import backend.app.api.documents as api_documents_module
    import backend.app.api.export as api_export_module
    import backend.app.services.documents as documents_module
    import backend.app.services.export as export_module

    bindings = [
        ("services/export.py", export_module, "EXPORT_STORE_DIR"),
        ("api/export.py", api_export_module, "EXPORT_STORE_DIR"),
        ("services/documents.py", documents_module, "DOCUMENT_STORE_DIR"),
        ("api/documents.py", api_documents_module, "DOCUMENT_STORE_DIR"),
    ]

    for label, module, attr in bindings:
        current = Path(getattr(module, attr)).resolve()
        assert ROOT not in current.parents and current != ROOT, (
            f"{label}::{attr} מצביע ל-{current}, שנמצא בתוך עץ הפרויקט. "
            "כל הרצת בדיקות תשאיר שם קבצים אמיתיים שהשורה שלהם ב-DB נזרקת עם "
            "ה-DB הזמני (conftest.py::isolated_file_stores)."
        )


# ===================================================================
# v1.1.1 פריט ג: אין אינטרפולציה גולמית של שגיאה ל-innerHTML
# ===================================================================

# הפורטלים הם HTML סטטי בלי build step, ולכן אין להם בדיקת יחידה - אבל *כן*
# אפשר לאכוף את הכלל שהם עצמם הצהירו עליו (clients/shared/documents.js:
# "כל טקסט שמגיע מה-DB עובר כאן לפני שהוא נכנס ל-innerHTML"). הביטוי מכוון
# צר בכוונה: התבנית האסורה היא ${...err/message/detail...} *ישירות* בתוך
# מחרוזת שמושמת ל-innerHTML, ולא "כל ${} ב-innerHTML" - האחרון היה תופס
# עשרות שימושים תקינים (קלאסים, colspan, ערכי ESOPDocuments.escapeHtml
# עצמם) ובדיקה שנופלת על false positive היא בדיקה שידחפו לה ignore.
_RAW_ERROR_INTERPOLATION = re.compile(
    r"innerHTML\s*=\s*`[^`]*\$\{\s*(?:err|error|e)\.(?:message|detail)\s*\}"
)

PORTAL_FILES = ("admin_portal/index_manage.html", "employee_portal/index_emp.html",
                "trustee_portal/index_trustee.html")


@pytest.mark.parametrize("relative_path", PORTAL_FILES)
def test_portal_never_interpolates_a_raw_error_into_innerhtml(relative_path):
    """נמצאו 11 מקומות כאלה ב-14/08/2026, לצד 9 באותם קבצים שכן הבריחו - שני
    דפוסים סותרים באותו קובץ, כלומר הקורא הבא לא יכול לדעת מה הכלל.

    err.message אינו ערך מתוך <select> כפי שהוערך תחילה: הוא *גוף התשובה* של
    השרת (documents.js::errorDetail), ולכן 500 בטקסט חופשי או HTML מ-proxy
    נכנסים לתוך <td> ומפרקים את הטבלה. השתמש ב-ESOPDocuments.errorRow/errorText."""
    path = ROOT / "clients" / relative_path
    matches = _RAW_ERROR_INTERPOLATION.findall(path.read_text(encoding="utf-8"))

    assert not matches, (
        f"{relative_path}: {len(matches)} הזרקות שגיאה גולמיות ל-innerHTML. "
        "עבור ל-ESOPDocuments.errorRow(colspan, err.message) או errorText(err.message)."
    )


def test_the_buyback_modal_keeps_its_accessibility_contract():
    """v1.2.0 §10. אף מודאל אחר בפורטל (2,738 שורות) אינו לוכד Escape, אינו
    מעביר פוקוס ואינו מצהיר role - ובקובץ כולו היו שלוש תכונות aria. כלומר
    החובות האלה כאן אינן "הסטנדרט של הקובץ" אלא חריגה ממנו, ובלי אינווריאנט
    העריכה הבאה תיישר אותן חזרה לרוב בלי שאיש ישים לב.

    הן אינן קוסמטיקה: זו זרימה דו-שלבית שכותבת אירוע ledger בלתי-הפיך."""
    portal = (ROOT / "clients" / "admin_portal" / "index_manage.html").read_text(encoding="utf-8")
    modal = portal[portal.index('id="buyback-modal"'):portal.index('id="ei-action-modal"')]

    for attribute in ('role="dialog"', 'aria-modal="true"', 'aria-labelledby="buyback-modal-title"'):
        assert attribute in modal, f"מודאל הרכישה העצמית איבד את {attribute}"

    # אזור התצוגה המקדימה ואזור הקבלה - שניהם משתנים בלי שהמשתמש לחץ עליהם.
    for region in ('id="buyback-preview"', 'id="buyback-receipt"'):
        segment = modal[modal.index(region):modal.index(region) + 200]
        assert 'aria-live="polite"' in segment, f"{region} אינו מוכרז ל-screen reader"

    # Escape ו"סגור" חסומים בזמן הכתיבה בלבד - לא תמיד, ולא אף פעם.
    #
    # *** חוזק בסקירה 12 (פער אימות 6) ***: הטענה הייתה על שלוש מחרוזות מקור
    # מילוליות, כלומר שינוי שם משתנה שבר אותה בעוד שינוי *סמנטיקה* ששומר על
    # המחרוזת עבר. עכשיו הטענה היא על מיקום: השומר חייב לשבת בתוך הגוף של
    # closeBuybackModal (ולא בפונקציה אחרת שמחזירה את המחרוזת), ולהיות return
    # מוקדם שתלוי במצב committing. שם המשתנה חופשי.
    close_body = portal[portal.index("function closeBuybackModal()"):]
    close_body = close_body[:close_body.index("\n        }")]
    assert re.search(r'if\s*\([^)]*===\s*"committing"\s*\)\s*return\s*;', close_body), (
        "closeBuybackModal אינו חוסם יציאה בזמן committing"
    )
    assert re.search(r'getElementById\(\s*"buyback-modal-title"\s*\)\.focus\(\)', portal), (
        "פתיחת המודאל אינה מעבירה פוקוס לכותרת"
    )
    # הכפתור שכותב חייב להיות מנוטרל *לפני* שהמצב חוזר ל-preview-shown אחרי כשל
    # (סקירה 12, אזהרה 8): אחרת 409 משאיר אותו דרוך עם סימן מיושן, ולחיצה חוזרת
    # מייצרת את אותו 409 לנצח.
    execute_body = portal[portal.index("async function executeBuyback()"):]
    execute_body = execute_body[:execute_body.index("\n        function ")]
    assert re.search(r"catch[\s\S]*buybackPreview\s*=\s*null", execute_body), (
        "נתיב הכשל בביצוע אינו פוסל את התצוגה המקדימה - הכפתור נשאר דרוך עם סימן מיושן"
    )


def test_the_buyback_row_action_never_interpolates_an_id_into_a_js_string():
    """~25 אתרי onclick="fn('${id}')" עדיין קיימים בשלושת הפורטלים (חוב פתוח
    ב-HANDOFF.md), ו-escapeHtml *אינו* מכסה מחרוזת JS בתוך אטריביוט. הקוד
    החדש לא יצטרף לחוב הזה: המזהה עובר ב-data-*, כמו התיקון ב-v1.1.1."""
    portal = (ROOT / "clients" / "admin_portal" / "index_manage.html").read_text(encoding="utf-8")

    assert not re.search(r"onclick=\"openBuybackModal\('", portal), (
        "כפתור הרכישה העצמית חזר להזרקת מזהה לתוך מחרוזת JS בתוך אטריביוט"
    )
    assert 'data-issuance-id="${esc(i.share_issuance_id)}"' in portal, (
        "שורת ההנפקה אינה מעבירה את המזהה ב-data-* מוברח"
    )


def test_the_shared_escaping_helpers_stay_exported():
    """שלושת הפורטלים קוראים ל-ESOPDocuments.errorRow/errorText/escapeHtml מתוך
    HTML גולמי, כלומר שינוי שם או הסרה מה-export לא ייפול בשום מקום אחר - הוא
    יתגלה כשורת שגיאה ריקה במסך של המשתמש."""
    source = (ROOT / "clients" / "shared" / "documents.js").read_text(encoding="utf-8")

    for name in ("escapeHtml", "errorRow", "errorText", "errorMessage"):
        assert re.search(rf"^\s*{name}:\s*{name},", source, re.MULTILINE), (
            f"documents.js אינו מייצא {name} דרך global.ESOPDocuments"
        )

    for relative_path in PORTAL_FILES:
        portal = (ROOT / "clients" / relative_path).read_text(encoding="utf-8")
        if "ESOPDocuments." in portal:
            assert "shared/documents.js" in portal, (
                f"{relative_path} משתמש ב-ESOPDocuments בלי לטעון את shared/documents.js"
            )


# ===================================================================
# v1.1.1 פריט ד2: כל endpoint מתעד את תשובת ההצלחה שלו
# ===================================================================

def test_every_endpoint_documents_its_success_response():
    """24 endpoints החזירו dict בלי response_model, כלומר ב-/docs הופיע גוף ריק.
    לא דליפה (הם מחזירים dict מפורש, לא ORM גולמי) - פער תיעוד.

    הבדיקה מקבלת שלוש צורות תקינות, כי "מתועד" אינו "יש response_model":
      1. response_model - סכימת JSON.
      2. הצהרת content-type לא-JSON (הורדות: application/pdf עם format=binary).
      3. 204 בלי גוף - שם response_model הוא סתירה, לא חוסר.

    למה אינווריאנט ולא צ'קליסט: endpoint חדש נכתב בדרך כלל בלי response_model
    (זה ברירת המחדל של FastAPI), ולכן הפער הזה חוזר מעצמו בכל גרסה."""
    from backend.app.main import app

    undocumented = []
    for path, operations in sorted(app.openapi()["paths"].items()):
        for method, operation in sorted(operations.items()):
            if method.upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                continue
            responses = operation.get("responses", {})
            success = responses.get("200") or responses.get("204") or {}
            content = success.get("content", {})
            schema = next(iter(content.values()), {}).get("schema") if content else None

            if schema and schema != {}:
                continue
            if not content and "204" in responses:
                continue
            undocumented.append(f"{method.upper()} {path} (content={list(content) or '-'})")

    assert not undocumented, (
        f"{len(undocumented)} endpoints בלי תשובת הצלחה מתועדת ב-/docs:\n  "
        + "\n  ".join(undocumented)
    )


def test_the_report_envelope_still_declares_columns():
    """columns הוא השדה שכל שלושת הפורטלים גוזרים ממנו את כותרות הטבלה.
    ReportEnvelopeOut הוגדר ב-v1.1.0 *בלי* השדה הזה ולא חובר לאף endpoint, כך
    שהחיסרון לא התגלה; חיבור שלו במצב ההוא היה מוחק את columns מהתשובה בשקט
    ושובר את הכותרות בלי שאף בדיקת endpoint תיפול. מכאן הבדיקה הזו."""
    from backend.app.schemas import ReportEnvelopeOut

    for field in ("report_type", "generated_at", "columns", "rows", "summary", "disclosures"):
        assert field in ReportEnvelopeOut.model_fields, (
            f"ReportEnvelopeOut חסר את {field} - response_model שמחובר לדוחות ימחק אותו בשקט"
        )
