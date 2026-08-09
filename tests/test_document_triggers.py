"""טריגרי ההקפאה של מסמך שאושר — מול סכימה ממוגרצת, לא create_all.

*** למה הקובץ הזה קיים בנפרד ***: שאר הסוויטה בונה סכימה ב-`Base.metadata.
create_all` (ראו conftest), ו-create_all מכיר רק טבלאות ועמודות — **טריגר אינו
טבלה**. כלומר כל בדיקה אחרת רצה מול DB בלי אף טריגר, וההגנה שנבנתה במפורש
בשכבת הנתונים לא הייתה מכוסה בכלל. כך שרד באג של 500 שלם: יצירת גרסה חדשה
למענק שכבר יש לו מסמך מאושר הפילה את השרת, ואף בדיקה לא יכלה לראות את זה.

הקובץ הזה בונה DB זמני משלו דרך `alembic upgrade head` — אותן מיגרציות שירוצו
בפרודקשן — ולכן הוא הבדיקה היחידה בסוויטה שבאמת נוגעת בטריגרים.
"""
import os
import sqlite3
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def migrated_db(tmp_path_factory):
    """DB זמני שנבנה דרך המיגרציות במלואן.

    ESOP_DATABASE_URL נדרס זמנית ולא רק ב-`cfg.set_main_option`: ב-migrations/
    env.py משתנה הסביבה **גובר** על ה-config, ו-conftest כבר הצביע בו על ה-DB
    של הסוויטה (שכבר נבנה ב-create_all). בלי הדריסה המיגרציות היו רצות עליו
    ונופלות על "table companies already exists".
    """
    db_path = tmp_path_factory.mktemp("triggers") / "migrated.db"
    url = f"sqlite:///{db_path.as_posix()}"

    previous = os.environ.get("ESOP_DATABASE_URL")
    os.environ["ESOP_DATABASE_URL"] = url
    try:
        cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")
    finally:
        if previous is None:
            os.environ.pop("ESOP_DATABASE_URL", None)
        else:
            os.environ["ESOP_DATABASE_URL"] = previous
    return db_path


@pytest.fixture
def conn(migrated_db):
    connection = sqlite3.connect(migrated_db)
    yield connection
    connection.close()


def _insert_document(conn, status: str, is_latest: int = 1) -> str:
    """מסמך מאושר נכתב עם חותמת ומאשר, כמו שהאפליקציה כותבת אותו. בלי זה
    `acknowledged_at` היה NULL, ובדיקת "אי אפשר לאפס אותו" הייתה עוברת בשקט
    רק מפני שאיפוס NULL ל-NULL אינו שינוי בכלל."""
    document_id = str(uuid.uuid4())
    acknowledged = status == "ACKNOWLEDGED"
    conn.execute(
        """INSERT INTO documents (document_id, template_type, grant_id, company_id, employee_id,
           status, version, is_latest, file_path, file_sha256, generated_at,
           acknowledged_at, acknowledged_by_user_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'),?,?)""",
        (document_id, "GRANT_LETTER", "G-T", "C-T", "E-T", status, 1, is_latest, "f.pdf", "hash",
         "2026-08-09 09:00:00" if acknowledged else None,
         "U-ACK" if acknowledged else None),
    )
    conn.commit()
    return document_id


def test_the_migrated_schema_actually_has_the_triggers(conn):
    """אם זה נופל, כל שאר הקובץ בודק כלום."""
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}

    assert "trg_documents_no_update_once_acknowledged" in names
    assert "trg_documents_no_delete_once_acknowledged" in names


def test_an_acknowledged_document_can_be_marked_superseded(conn):
    """ההכרעה: הנייר הישן נשמר, ותמיד אפשר להוציא נייר חדש מעודכן. סימון
    is_latest=0 אינו משנה את מה שהעובד אישר, ולכן אינו נחסם — הטריגר המקורי
    חסם אותו וכך הפיל את יצירת הגרסה הבאה ב-500."""
    document_id = _insert_document(conn, "ACKNOWLEDGED", is_latest=1)

    conn.execute("UPDATE documents SET is_latest = 0 WHERE document_id = ?", (document_id,))
    conn.commit()

    still_there = conn.execute(
        "SELECT status, is_latest FROM documents WHERE document_id = ?", (document_id,)).fetchone()
    assert still_there == ("ACKNOWLEDGED", 0), "הנייר הישן חייב להישמר, רק מסומן כמיושן"


@pytest.mark.parametrize("column,value", [
    ("status", "DRAFT"),
    ("acknowledged_at", None),
    ("acknowledged_by_user_id", "someone-else"),
    ("file_sha256", "tampered"),
    ("file_path", "other.pdf"),
    ("version", 99),
    ("template_type", "SECTION_102_APPENDIX"),
])
def test_the_substance_of_an_acknowledgment_stays_frozen(conn, column, value):
    """כל מה שמהווה את רשומת האישור עצמה נשאר חסום — הצמצום נגע ל-is_latest בלבד."""
    document_id = _insert_document(conn, "ACKNOWLEDGED")

    with pytest.raises(sqlite3.IntegrityError, match="frozen"):
        conn.execute(f"UPDATE documents SET {column} = ? WHERE document_id = ?", (value, document_id))
        conn.commit()
    conn.rollback()


def test_an_acknowledged_document_still_cannot_be_deleted(conn):
    document_id = _insert_document(conn, "ACKNOWLEDGED")

    with pytest.raises(sqlite3.IntegrityError, match="frozen"):
        conn.execute("DELETE FROM documents WHERE document_id = ?", (document_id,))
        conn.commit()
    conn.rollback()


@pytest.mark.parametrize("status", ["DRAFT", "SENT"])
def test_a_document_that_was_not_acknowledged_is_still_freely_updatable(conn, status):
    """הקיפאון מותנה במצב: המעבר SENT -> ACKNOWLEDGED חייב להישאר אפשרי."""
    document_id = _insert_document(conn, status)

    conn.execute("UPDATE documents SET status = 'ACKNOWLEDGED' WHERE document_id = ?", (document_id,))
    conn.commit()

    assert conn.execute("SELECT status FROM documents WHERE document_id = ?",
                        (document_id,)).fetchone()[0] == "ACKNOWLEDGED"
