"""טיפוסי עמודה משותפים.

``UtcDateTime`` קיים כי SQLite אינו שומר אזור זמן: SQLAlchemy משמיט את ה-tzinfo
בשקט ו*אינו* ממיר ל-UTC. ערך aware ב-+03:00 נשמר לפי שעון הקיר שלו ונקרא בחזרה
כאילו היה UTC - שגיאה של שלוש שעות, בלי חריגה ובלי אזהרה. ``DateTime(timezone=True)``
אינו משנה זאת ב-SQLite.

הנרמול יושב בשכבה אחת ולא בכל אתר קריאה, כי נרמול מפוזר הוא נרמול שייעשה בחלק
מהמקומות ויישכח באחרים - וזה בדיוק המצב שהוליד את הבאג.
"""

from datetime import date, datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    """חלופה יחידה ל-``datetime.utcnow()`` המסומן להסרה. מחזיר תמיד aware.

    קיימת כפונקציה ולא כקריאה ישירה בכל אתר, כדי שאפשר יהיה לאסור את
    ``datetime.utcnow`` כאינווריאנט של הריפו ולא בסקירה ידנית חוזרת.
    """
    return datetime.now(timezone.utc)


def system_today_utc() -> date:
    """התאריך שהמערכת עצמה פועלת לפיו, ב-UTC.

    ``date.today()`` מחזיר את תאריך **המארח**: אותו קוד על שרת אחר נותן תאריך
    אחר. ביומן אירועים זה בלתי נסבל, ולכן שעון אחד עם ``recorded_at``.

    **אסור להשתמש בו כמקור לתאריך בעל משמעות מסית או משפטית** - ``grant_date``,
    ``trustee_deposit_date``, ``exercise_date`` וכל תאריך מימוש/מכירה עתידי.
    כולם מגיעים מהמסמך (החלטת דירקטוריון, אישור הפקדה, הודעת מימוש) ולא משעון,
    בשום אזור זמן. הנימוק אינו העדפה: ארה"ב מאחורי UTC וישראל לפניו, כך שאין
    שעון יחיד שהוא שמרני לשתי המדינות - ולכן שעון אינו יכול להיות מקור לתאריך מס.
    השם ``system_today_utc`` ולא ``business_date`` בדיוק כדי שהשימוש האסור ייראה
    אסור. נאכף ב-tests/test_project_invariants.py.
    """
    return datetime.now(timezone.utc).date()


def ensure_utc(value: datetime | None) -> datetime | None:
    """מנרמל ל-UTC חותמת זמן שהגיעה **מהלקוח**, ומפרש naive כ-UTC.

    זהו הגבול היחיד שבו פירוש naive מותר, וההיתר מנומק ולא מקל: ה-API עצמו
    פלט עד היום חותמות naive-UTC, ולכן לקוח שמחזיר לנו ערך שקיבל מאיתנו
    מתכוון ל-UTC. דחייה כאן הייתה שוברת כל לקוח קיים בלי להוסיף נכונות.

    ``UtcDateTime`` בשכבת ה-DB ממשיך *לדחות* naive, וזו החלוקה: ניחוש מותר
    פעם אחת, במקום גלוי, על קלט חיצוני - ולעולם לא על באג פנימי.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class UtcDateTime(TypeDecorator):
    """עמודת ``DateTime`` שמובטח בה ש-UTC נכנס ו-UTC יוצא.

    פורמט האחסון זהה **בדיוק** לזה שנכתב עד היום (naive, בלי היסט), ולכן אין
    צורך במיגרציית דאטה - כל הערכים הקיימים כבר UTC, שכן אין בקוד ולו קריאה
    אחת ל-``datetime.now()`` מקומי. זהות הפורמט היא גם מה שמאפשר לתקן את
    ``ledger_events``, שהטריגר ``trg_ledger_events_no_update`` חוסם עליה כל
    ``UPDATE`` - האינווריאנט שהתיקון הזה בא להגן עליו מלכתחילה.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            # לא מנחשים אזור זמן. ניחוש כאן הוא בדיוק הכשל שהטיפוס נועד למנוע,
            # והוא היה חוזר בשקט - הפעם מתוך הקוד שאמור היה לסגור אותו.
            raise ValueError(
                "חותמת זמן naive נדחתה. השתמשו ב-backend.app.types.utcnow(), "
                "או ציינו tzinfo מפורש."
            )
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        # replace ולא astimezone: הערך המאוחסן *הוא* UTC. astimezone היה מפרש
        # אותו כזמן מקומי של המארח ומזיז אותו בהיסט של השרת - כלומר מחזיר את
        # אותה שגיאת שלוש שעות דרך הדלת האחורית.
        return value.replace(tzinfo=timezone.utc)
