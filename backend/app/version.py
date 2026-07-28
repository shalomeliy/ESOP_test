from pathlib import Path

# מקור אמת יחיד למספר הגרסה - קובץ VERSION בשורש הפרויקט, כדי שהשרת
# והקליינטים (שקוראים אותו דרך /api/v1/version) לעולם לא יסטו זה מזה.
_VERSION_FILE = Path(__file__).resolve().parent.parent.parent / "VERSION"


def get_version() -> str:
    """קורא את הקובץ מחדש בכל קריאה - כך עדכון גרסה לא דורש הפעלה מחדש של השרת."""
    return _VERSION_FILE.read_text(encoding="utf-8").strip()


# ערך קבוע לזמן עליית התהליך בלבד - משמש רק לכותרת ה-OpenAPI (/docs), ששם
# ממילא נבנית פעם אחת עם עליית השרת ולא ניתנת לרענון per-request.
VERSION = get_version()
