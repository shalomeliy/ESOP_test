"""תשתית הבדיקות + מנגנון ההגנה על ה-DB החי.

*** סדר הפעולות בקובץ הזה קריטי ואסור לשנות אותו. ***
``backend/app/database.py`` בונה את ה-Engine ברגע ה-import (שורת module level),
כלומר ``ESOP_DATABASE_URL`` חייב להיות מוגדר *לפני* כל import מ-backend.app.
pytest טוען conftest.py לפני כל מודול בדיקה, ולכן זה המקום היחיד שבו אפשר
להבטיח את זה. כל import מ-backend שיעבור לראש הקובץ ידליף בדיקות אל
esop_database.db האמיתי - ה-DB שמחזיק נתוני עבודה, לא fixtures זמניים.
"""

import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# backend.app.main עושה StaticFiles(directory="clients") - נתיב *יחסי* שנפתר בזמן
# import. בלי cwd יציב, ה-import של האפליקציה נופל כשמריצים pytest מתיקייה אחרת.
os.chdir(PROJECT_ROOT)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# קובץ DB זמני מחוץ לעץ הפרויקט. mkdtemp ולא tmp_path כי צריך את הערך כבר עכשיו,
# בזמן import, ולא בזמן ריצת fixture.
_TEST_DB_DIR = tempfile.mkdtemp(prefix="esop_test_db_")
TEST_DB_PATH = Path(_TEST_DB_DIR) / "esop_test_scratch.db"
TEST_DB_URL = f"sqlite:///{TEST_DB_PATH.as_posix()}"
os.environ["ESOP_DATABASE_URL"] = TEST_DB_URL

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from backend.app import models  # noqa: E402,F401  -- רושם את כל הטבלאות על Base.metadata
from backend.app.database import Base, engine, get_db  # noqa: E402

PRODUCTION_DB_NAME = "esop_database.db"
PRODUCTION_DB_PATH = PROJECT_ROOT / PRODUCTION_DB_NAME

# טביעת אצבע של ה-DB החי בזמן טעינת ה-conftest, כדי שאפשר יהיה להוכיח בבדיקה
# (ולא רק להבטיח) שאף בדיקה לא נגעה בו.
PRODUCTION_DB_MTIME_AT_IMPORT = (
    PRODUCTION_DB_PATH.stat().st_mtime if PRODUCTION_DB_PATH.exists() else None
)


def _assert_not_production(url: str) -> None:
    if PRODUCTION_DB_NAME in str(url):
        raise RuntimeError(
            "ABORT: הבדיקות מכוונות ל-DB הייצור/עבודה "
            f"({url}). ESOP_DATABASE_URL לא נתפס לפני ה-import של backend.app."
        )


# בדיקה ראשונה כבר בזמן ה-import: גם אם מישהו יריץ collection בלבד (--collect-only),
# שום חיבור לא ייפתח אל ה-DB האמיתי.
_assert_not_production(engine.url)


@pytest.fixture(scope="session", autouse=True)
def guard_production_db():
    """שכבת הגנה שנייה - נכשלת מיידית אם ה-Engine בכל זאת מצביע ל-DB החי."""
    _assert_not_production(engine.url)
    _assert_not_production(os.environ.get("ESOP_DATABASE_URL", ""))
    assert str(engine.url) == TEST_DB_URL, (
        f"Engine URL {engine.url!r} != expected scratch URL {TEST_DB_URL!r}"
    )
    yield


@pytest.fixture(scope="session", autouse=True)
def create_schema(guard_production_db):
    """סכימה דרך ``alembic upgrade head`` ולא ``create_all``.

    ``create_all`` בונה טבלאות בלבד ו*אינו* מייצר טריגרים, שקיימים רק
    במיגרציות. לכן כל הסוויטה פרט ל-test_document_triggers.py רצה בלי אף
    אינווריאנט שנאכף ב-DB, ובאג 500 שלם שרד את זה. הטריגרים הם המקום שבו
    אי-שינוי היומן ("append-only") נאכף בפועל - בדיקה שרצה בלעדיהם מאמתת
    סכימה אחרת מזו שבייצור.

    לא מחלצים את ה-DDL לקבוע משותף: מיגרציה היא ארטיפקט קפוא, ושינוי מיגרציה
    ישנה כדי שתייבא קבוע היה משנה התנהגות רטרואקטיבית. העתקה לקובץ עזר של
    הבדיקות הייתה מחזירה בדיוק את הדריפט שהתיקון הזה סוגר.
    """
    from alembic import command  # noqa: PLC0415 - יקר, ונדרש רק פעם אחת בסשן
    from alembic.config import Config as AlembicConfig  # noqa: PLC0415

    alembic_cfg = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
    # alembic.ini שורה 99 מצביעה על ה-DB החי. env.py אמנם מעדיף את משתנה
    # הסביבה, אבל ההגנה לא תישען על סדר קדימויות בקובץ אחר: קובעים במפורש.
    alembic_cfg.set_main_option("sqlalchemy.url", TEST_DB_URL)
    _assert_not_production(alembic_cfg.get_main_option("sqlalchemy.url"))

    command.upgrade(alembic_cfg, "head")

    # הוכחה ולא הבטחה: בלי זה, מיגרציה שתפסיק לייצר טריגר תחזיר את הסוויטה
    # בשקט למצב שלפני התיקון.
    with engine.connect() as conn:
        triggers = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        ).scalars().all()
    assert triggers, "הסכימה נבנתה בלי טריגרים - alembic upgrade head לא רץ על ה-DB הזמני"

    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session(create_schema):
    """Session בתוך טרנזקציה שמתגלגלת אחורה בסוף כל בדיקה - כל בדיקה מתחילה
    מ-DB ריק בלי לשלם על drop/create בכל פעם."""
    connection = engine.connect()
    transaction = connection.begin()
    # *** מגבלה מוכרת ***: ה-Session הזה לא עוטף את ה-commit של ה-endpoint בתוך
    # savepoint. המשמעות: endpoint שעושה commit סוגר את הטרנזקציה החיצונית, ואם
    # אחר כך הוא עושה rollback (למשל נתיב ה-IntegrityError של dismiss) הוא מוחק
    # את ה-fixture באמצע הבדיקה. לכן בדיקה שעוברת בנתיב rollback של ה-endpoint
    # לא אמורה לגשת ל-ORM אחריו - ראו tests/test_notifications.py.
    # join_transaction_mode="create_savepoint" נראה כמו הפתרון אבל מייצר הפרעה
    # בין בדיקות ב-SQLite (22 שגיאות בסוויטה, עוברות אחת-אחת) - לא לנסות שוב
    # בלי לטפל בנעילות של SQLite.
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        # endpoint שעשה commit כבר סגר את הטרנזקציה החיצונית (ראו המגבלה למעלה),
        # ו-rollback עליה פלט SAWarning בכל בדיקה כזו. הבדיקה כאן אינה מסתירה את
        # המגבלה - היא רק מפסיקה לדווח עליה כאזהרה בכל ריצה, כדי שאזהרה אמיתית
        # לא תיבלע ברעש. ההתנהגות זהה: טרנזקציה שכבר נסגרה אין מה לגלגל אחורה.
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture
def client(db_session):
    """TestClient שה-get_db שלו מחובר לאותה טרנזקציה זמנית של הבדיקה."""
    from backend.app.main import app

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# מפעלי אובייקטים לא-מחוברים ל-session.
# חשוב: ערכי ה-default של SQLAlchemy (למשל post_termination_window_days=90,
# paused_days_total=0) מוחלים רק ב-INSERT. אובייקט שנבנה בזיכרון בלבד מקבל None,
# ולכן המפעלים כאן מציבים כל ערך במפורש.
# ---------------------------------------------------------------------------

@pytest.fixture
def make_schedule():
    def _make(start_date, cliff_months=12, total_months=48, paused_days_total=0,
              grant_id="GRANT-TEST-1"):
        return models.VestingSchedule(
            schedule_id="SCHED-TEST-1",
            grant_id=grant_id,
            start_date=start_date,
            cliff_months=cliff_months,
            total_months=total_months,
            paused_days_total=paused_days_total,
        )

    return _make


@pytest.fixture
def make_grant():
    def _make(total_options=4800.0, grant_date=None, trustee_deposit_date=None,
              post_termination_window_days=90, grant_id="GRANT-TEST-1",
              employee_id="EMP-TEST-1", grant_type=models.GrantType.IL_102_CAPITAL_GAINS):
        from datetime import date

        return models.Grant(
            grant_id=grant_id,
            employee_id=employee_id,
            pool_id="POOL-TEST-1",
            grant_date=grant_date or date(2022, 1, 1),
            grant_type=grant_type,
            total_options=total_options,
            exercise_price=1.0,
            currency="USD",
            trustee_deposit_date=trustee_deposit_date,
            post_termination_window_days=post_termination_window_days,
        )

    return _make


@pytest.fixture
def make_employee():
    def _make(status=models.EmployeeStatus.ACTIVE, termination_date=None,
              employee_id="EMP-TEST-1"):
        from datetime import date

        return models.Employee(
            employee_id=employee_id,
            company_id="COMP-TEST-1",
            first_name="Test",
            last_name="Employee",
            email=f"{employee_id.lower()}@test.example",
            country_code="IL",
            status=status,
            hire_date=date(2020, 1, 1),
            termination_date=termination_date,
        )

    return _make
