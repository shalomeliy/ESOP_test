"""רגרסיה על מיגרציה bd65db40f654: option_pools הוא FK target קיים מ-grants.

הבאג המקורי (11/08/2026): create_foreign_key על option_pools מחייב SQLite
לבצע recreate מלא של הטבלה (batch mode), וה-DROP של הטבלה הישנה נכשל
ב-FOREIGN KEY constraint failed כל עוד grants.pool_id מפנה אליה. פספסה את
זה כי כל בדיקות ה-pytest וה-sandbox המקוריים הריצו את המיגרציה מול סכימה
ריקה, בלי grants מזרוע - הבאג הופיע רק מול esop_database.db החי (251 שורות
grants אמיתיות). הבדיקה כאן מזריעה שורת grants לפני ההעלאה כדי לתפוס בדיוק
את זה, ולא רק "המיגרציה רצה בלי שגיאה" על DB ריק.
"""

import os
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig

from tests.conftest import PROJECT_ROOT, _assert_not_production

_ONE_BEFORE = "d9e4f1a2b3c6"


def _run_alembic(db_path: Path, action: str, target: str) -> None:
    url = f"sqlite:///{db_path.as_posix()}"
    _assert_not_production(url)
    cfg = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    # env.py מעדיף תמיד את משתנה הסביבה (ראו migrations/env.py:get_database_url) -
    # בלי לדרוס אותו כאן, ה-URL של הבדיקה יתעלם וה-alembic ירוץ על ה-DB של הסשן.
    previous = os.environ.get("ESOP_DATABASE_URL")
    os.environ["ESOP_DATABASE_URL"] = url
    try:
        if action == "upgrade":
            command.upgrade(cfg, target)
        else:
            command.downgrade(cfg, target)
    finally:
        if previous is not None:
            os.environ["ESOP_DATABASE_URL"] = previous
        else:
            os.environ.pop("ESOP_DATABASE_URL", None)


def test_cap_table_migration_with_existing_grants_referencing_option_pools(tmp_path):
    db_path = tmp_path / "migration_fk_check.db"
    _run_alembic(db_path, "upgrade", _ONE_BEFORE)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        "INSERT INTO companies (company_id, name, country_code, is_active) "
        "VALUES ('C1', 'Co', 'IL', 1)"
    )
    conn.execute(
        "INSERT INTO employees "
        "(employee_id, company_id, first_name, last_name, email, country_code, status, hire_date) "
        "VALUES ('E1', 'C1', 'A', 'B', 'a@b.example', 'IL', 'ACTIVE', '2020-01-01')"
    )
    conn.execute(
        "INSERT INTO option_pools (pool_id, company_id, total_shares, allocated_shares, unallocated_shares) "
        "VALUES ('P1', 'C1', 1000, 0, 1000)"
    )
    conn.execute(
        "INSERT INTO grants "
        "(grant_id, employee_id, pool_id, grant_date, grant_type, total_options, exercise_price, "
        "post_termination_window_days) "
        "VALUES ('G1', 'E1', 'P1', '2022-01-01', 'IL_102_CAPITAL_GAINS', 100, 1.0, 90)"
    )
    conn.commit()
    conn.close()

    # לפני התיקון: נכשל כאן ב-FOREIGN KEY constraint failed בזמן ה-DROP TABLE
    # של option_pools בתוך batch_alter_table.
    _run_alembic(db_path, "upgrade", "head")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_key_check")
    assert cur.fetchall() == []
    cur.execute("SELECT COUNT(*) FROM grants")
    assert cur.fetchone()[0] == 1
    cur.execute("SELECT share_class_id FROM option_pools WHERE pool_id = 'P1'")
    assert cur.fetchone()[0] is None
    conn.close()

    # downgrade עושה recreate דומה על option_pools - אותה בעיה, אותו תיקון.
    _run_alembic(db_path, "downgrade", _ONE_BEFORE)
    _run_alembic(db_path, "upgrade", "head")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA integrity_check")
    assert cur.fetchall() == [("ok",)]
    cur.execute("PRAGMA foreign_key_check")
    assert cur.fetchall() == []
    cur.execute("SELECT COUNT(*) FROM grants")
    assert cur.fetchone()[0] == 1
    conn.close()
