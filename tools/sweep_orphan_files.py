"""סריקת קבצים יתומים ב-export_store/ ו-document_store/ - דוח כברירת מחדל.

מה זה פותר
----------
שתי התיקיות צומחות בלי מדיניות שמירה (19MB ו-4.6MB ב-14/08/2026), ובאותה
נשימה **אסור למחוק מהן על עיוור**: ל-Document.file_path יש NOT NULL וקובץ חסר
מחזיר 500 מפורש (api/documents.py), ו-DataTransferRun.file_path נקרא שוב כדי
לבצע IMPORT_COMMIT ולהפיק את דוח ההתאמה - מחיקת bundle שיש לו שורה הופכת ייבוא
עובד ל-500. לכן "יתום" כאן אינו "ישן" אלא **אין שום שורה ב-DB שמפנה אליו**.

למה כלי ולא ניקוי אוטומטי: המחיקה בלתי-הפיכה ופועלת על תיקיות של המשתתף. ברירת
המחדל היא דוח; ``--delete`` נדרש במפורש, ומדפיס מה נמחק.

v1.1.1 פריט ד1. הצטברות היתומים עצמה נגרמה מדליפת בדיקות שנסגרה בנפרד
(conftest.py::isolated_file_stores) - הכלי הזה מפנה את מה שכבר הצטבר, ואינו
תחליף לסגירת המקור.

    python -m tools.sweep_orphan_files              # דוח בלבד
    python -m tools.sweep_orphan_files --delete      # מוחק, אחרי אישור
"""
import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def _referenced_names(db_url: str) -> tuple[set[str], list[str]]:
    """שמות הקבצים שיש להם שורה ב-DB, ואזהרות על שורות שהקובץ שלהן חסר.

    file_path נשמר יחסי ל-store, אבל ההשוואה היא על basename בכוונה: שינוי
    עתידי בפריסת התיקיות (תתי-תיקיות לפי חברה, למשל) לא יהפוך קבצים מוגנים
    ל"יתומים" בשקט. basename מייצר לכל היותר הגנת-יתר, וזה הכיוון הנכון לטעות בו.
    """
    os.environ["ESOP_DATABASE_URL"] = db_url
    from backend.app.database import SessionLocal  # noqa: PLC0415 - אחרי קביעת ה-URL
    from backend.app.models import DataTransferRun, Document  # noqa: PLC0415

    referenced: set[str] = set()
    dangling: list[str] = []
    db = SessionLocal()
    try:
        for model, store in ((Document, "document_store"), (DataTransferRun, "export_store")):
            for (path,) in db.query(model.file_path).filter(model.file_path.isnot(None)).all():
                name = Path(path).name
                referenced.add(name)
                if not (PROJECT_ROOT / store / path).exists():
                    dangling.append(f"{store}/{path}")
    finally:
        db.close()
    return referenced, dangling


def _scan(store: str, referenced: set[str]) -> tuple[list[Path], int]:
    root = PROJECT_ROOT / store
    if not root.exists():
        return [], 0
    orphans, total_bytes = [], 0
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name not in referenced:
            orphans.append(path)
            total_bytes += path.stat().st_size
    return orphans, total_bytes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delete", action="store_true",
                        help="מוחק את היתומים בפועל. בלי הדגל - דוח בלבד.")
    parser.add_argument("--database-url", default="sqlite:///./esop_database.db",
                        help="ברירת מחדל: ה-DB החי, כי הוא זה שמחזיק את ההפניות.")
    args = parser.parse_args()

    referenced, dangling = _referenced_names(args.database_url)
    print(f"[sweep] {len(referenced)} קבצים מופנים מ-{args.database_url}")

    # שורה שהקובץ שלה חסר היא באג חי (500 בקריאה), לא נושא של ניקוי - אבל זה
    # המקום היחיד שסורק את שני הצדדים, ולכן שקט כאן היה בזבוז של המידע.
    if dangling:
        print(f"\n*** {len(dangling)} שורות ב-DB מצביעות לקובץ שאינו קיים ***")
        for item in dangling:
            print(f"  ! {item}")

    grand_total = 0
    all_orphans: list[Path] = []
    for store in ("export_store", "document_store"):
        orphans, size = _scan(store, referenced)
        all_orphans.extend(orphans)
        grand_total += size
        print(f"\n{store}/: {len(orphans)} יתומים, {size / 1_048_576:.1f}MB")

    if not all_orphans:
        print("\nאין יתומים.")
        return

    if not args.delete:
        print(f"\nסה\"כ {len(all_orphans)} יתומים, {grand_total / 1_048_576:.1f}MB. "
              "דוח בלבד - הוסף --delete כדי למחוק.")
        return

    answer = input(f"\nלמחוק {len(all_orphans)} קבצים ({grand_total / 1_048_576:.1f}MB)? [yes/N] ")
    if answer.strip().lower() != "yes":
        sys.exit("בוטל. לא נמחק דבר.")

    for path in all_orphans:
        path.unlink()
    print(f"נמחקו {len(all_orphans)} קבצים, {grand_total / 1_048_576:.1f}MB פונו.")


if __name__ == "__main__":
    main()
