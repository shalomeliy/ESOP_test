"""הרצת השרת מול qa_sandbox.db במקום מול ה-DB החי.

הסיבה לקובץ הזה ולא לשורת פקודה: אימות ידני שכותב (שליחת מסמך, אישור קבלה,
dismiss) משנה נתונים אמיתיים ב-esop_database.db. הגדרת ESOP_DATABASE_URL חייבת
לקרות *לפני* ייבוא backend.app.database, שקורא את המשתנה ברגע הייבוא - ולכן
os.environ נקבע כאן ולא ב-uvicorn CLI, שם כבר מאוחר מדי.

PYTHONIOENCODING חובה ב-Windows: seed_data מדפיס אמוג'י, וב-cp1252 הוא נופל
ב-UnicodeEncodeError באמצע הזריעה.

    python -m tools.run_sandbox [--port 8001]
"""
import argparse
import os
import sys
from pathlib import Path

SANDBOX_URL = "sqlite:///./qa_sandbox.db"
SANDBOX_FILE = Path(__file__).resolve().parent.parent / "qa_sandbox.db"

# *** נמצא בסקירת שלב 3 ***: setdefault כיבד ESOP_DATABASE_URL שכבר יוצא בסביבה -
# כולל כזה שמצביע על ה-DB החי - וכך הכלי שכל תפקידו "לא לגעת בחי" היה מגיש אותו
# בשקט. נוהל ה-QA עצמו (QA_TESTBOOK.md) מנחה לייצא את המשתנה, כך שזה לא מקרה קצה.
# היעד נכפה כאן, וכל ערך אחר נעצר במקום להיות מכובד.
_existing = os.environ.get("ESOP_DATABASE_URL")
if _existing and _existing != SANDBOX_URL:
    sys.exit(
        f"ESOP_DATABASE_URL is set to {_existing!r}, which is not the sandbox.\n"
        f"This runner refuses to serve anything but {SANDBOX_URL}.\n"
        "Unset the variable (or set it to the sandbox URL) and run again."
    )
os.environ["ESOP_DATABASE_URL"] = SANDBOX_URL
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()

    # main.py לא מריץ create_all בכוונה, ולכן DB חסר לא נכשל בעלייה אלא רק בזמן
    # ריצה, בכל בקשה, עם "no such table" - שגיאה שנראית כמו באג באפליקציה.
    # עדיף להיעצר כאן עם ההוראה המדויקת.
    if not SANDBOX_FILE.exists():
        sys.exit(
            f"{SANDBOX_FILE.name} does not exist. Seed it first:\n"
            f'  PYTHONIOENCODING=utf-8 ESOP_DATABASE_URL="{SANDBOX_URL}" python -m backend.seed_data'
        )

    print(f"[sandbox] serving {SANDBOX_URL} on http://127.0.0.1:{args.port}")

    import uvicorn
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=args.port, reload=False)


if __name__ == "__main__":
    main()
