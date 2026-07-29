# ESOP Enterprise Engine & Testbed

מערכת מלאה לניהול, תרגול וסימולציה של חלוקת אופציות/RSU, התאמת מיסוי (ישראל 102/ארה"ב ISO/NSO), ניהול נאמנים וסטטוסי עובדים.

## 🚀 הוראות הרצה מהירות

### 1. התקנת התלויות
לחץ `Ctrl + ~` לפתיחת הטרמינל ב-Cursor והרץ:
```bash
pip install -r requirements.txt
```

## 🗄️ מיגרציות סכמה (Alembic)

מ-v0.4.0 סכמת ה-DB מנוהלת ב-Alembic. הסיבה: `Base.metadata.create_all()` יוצר רק טבלאות
חסרות ולעולם לא מוסיף עמודה לטבלה שכבר קיימת — בדיוק זה גרם בעבר לקריסות `no such column`
מול `esop_database.db` הקיים.

כל הפקודות מורצות **משורש הפרויקט** (שם נמצא `alembic.ini`).

### הצעד החד-פעמי על `esop_database.db` הקיים

**חובה להריץ `alembic stamp head` — ולעולם לא `alembic upgrade head`.**

`esop_database.db` כבר מכיל את כל 13 הטבלאות (הן נבנו בזמנו ב-`create_all`), ולכן
`upgrade` ינסה ליצור אותן שוב וייפול מיד על `table companies already exists`.
`stamp` רק רושם את מספר הרוויזיה בטבלת `alembic_version` בלי להריץ שום DDL — כלומר
"ה-DB הזה כבר נמצא ב-baseline".

```bash
alembic stamp head    # פעם אחת בלבד, על DB קיים
alembic current       # אימות: צריך להציג eca19ffceb4d (head)
```

אחרי ה-stamp, `alembic upgrade head` הופך לבטוח ולא עושה כלום עד שתתווסף רוויזיה חדשה.

### DB חדש לגמרי (מפתח חדש / סביבת בדיקות)

```bash
alembic upgrade head          # בונה את כל 13 הטבלאות מאפס
python -m backend.seed_data   # נתוני דמו (אופציונלי)
```

### הוספת מיגרציה חדשה

```bash
alembic revision --autogenerate -m "add xxx to yyy"
# חובה לקרוא את הקובץ שנוצר לפני ההרצה: autogenerate לא מזהה שינוי שם עמודה
# (הוא יציע DROP + ADD ויאבד נתונים) ולא מזהה שינוי ב-CHECK constraint.
alembic upgrade head
alembic downgrade -1          # rollback לרוויזיה הקודמת
```

### עבודה מול DB אחר (בדיקות, ניסויים)

`ESOP_DATABASE_URL` גובר גם על האפליקציה וגם על Alembic. בלי המשתנה — ברירת המחדל היא
`sqlite:///./esop_database.db` כמו תמיד. אף פעם אל תנסה מיגרציה חדשה ישירות על ה-DB החי:

```bash
ESOP_DATABASE_URL="sqlite:///./scratch.db" alembic upgrade head
```

### בדיקת דריפט בין `models.py` ל-DB

```bash
alembic revision --autogenerate -m probe   # הגוף של upgrade() חייב להיות pass
```

אם נוצרו פקודות — יש דריפט בין המודלים לסכמה בפועל, וצריך לטפל בזה לפני כל דבר אחר.
בכל מקרה למחוק את קובץ ה-probe בסוף, הוא לא אמור להיכנס ל-git.

> `alembic.ini` חייב להישאר ASCII בלבד: Alembic קורא אותו בקידוד ה-locale של המערכת
> (cp1252 כאן), ולכן הערה בעברית שם מפילה כל פקודת alembic ב-`UnicodeDecodeError`.
> ההסברים בעברית יושבים ב-`migrations/env.py`, שנקרא כ-UTF-8.