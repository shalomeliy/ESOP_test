from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

# שימוש ב-SQLite כ-Database מקומי לבדיקות וסביבת QA
SQLALCHEMY_DATABASE_URL = "sqlite:///./esop_database.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)

# SQLite לא אוכף Foreign Keys כברירת מחדל - חייבים להפעיל את זה בכל חיבור,
# אחרת כל ה-ForeignKey שמוגדרים ב-models הם קישוט בלבד וללא אכיפה אמיתית.
@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    """Dependency לקבלת session של בסיס הנתונים ושחרורו בסיום"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()