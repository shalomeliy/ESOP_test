import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config
from sqlalchemy import event
from sqlalchemy import pool

from alembic import context

# שורש הפרויקט חייב להיות ב-sys.path כדי ש-"backend.app..." יימצא גם כשמריצים
# את alembic מתיקייה אחרת (prepend_sys_path=. מכסה רק הרצה משורש הפרויקט).
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.database import Base, DEFAULT_DATABASE_URL  # noqa: E402
# ה-import הזה נראה "לא בשימוש" אבל הוא הכרחי: הוא זה שרושם את כל המודלים
# על Base.metadata. בלעדיו autogenerate יראה metadata ריק ויציע DROP לכל הטבלאות.
import backend.app.models  # noqa: F401,E402

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_database_url() -> str:
    """מחזיר את יעד ה-DB לפי סדר עדיפות: משתנה סביבה -> alembic.ini -> ברירת המחדל.

    הקריאה ל-os.environ מתבצעת כאן בזמן ריצה ולא נשענת על הערך ש-database.py
    חישב בזמן import - כך ש-ESOP_DATABASE_URL תמיד גובר, בלי תלות בסדר ה-imports.
    """
    env_url = os.environ.get("ESOP_DATABASE_URL")
    if env_url:
        return env_url
    return config.get_main_option("sqlalchemy.url") or DEFAULT_DATABASE_URL


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite לא יודע ALTER/DROP COLUMN אמיתי - batch mode בונה טבלה זמנית,
        # מעתיק נתונים ומחליף. בלי זה כל מיגרציה עתידית על עמודה קיימת תיפול.
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    # אכיפת Foreign Keys גם בזמן מיגרציה, באותו אופן כמו ב-database.py.
    # חייב להיות ב-event של connect ולא כ-statement רגיל: PRAGMA foreign_keys
    # מתעלם בשקט אם הוא רץ בתוך transaction פתוח.
    if connectable.dialect.name == "sqlite":
        @event.listens_for(connectable, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
