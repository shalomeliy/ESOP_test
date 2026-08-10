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
