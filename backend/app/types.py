"""טיפוסי עמודה משותפים.

``UtcDateTime`` קיים כי SQLite אינו שומר אזור זמן: SQLAlchemy משמיט את ה-tzinfo
בשקט ו*אינו* ממיר ל-UTC. ערך aware ב-+03:00 נשמר לפי שעון הקיר שלו ונקרא בחזרה
כאילו היה UTC - שגיאה של שלוש שעות, בלי חריגה ובלי אזהרה. ``DateTime(timezone=True)``
אינו משנה זאת ב-SQLite.

הנרמול יושב בשכבה אחת ולא בכל אתר קריאה, כי נרמול מפוזר הוא נרמול שייעשה בחלק
מהמקומות ויישכח באחרים - וזה בדיוק המצב שהוליד את הבאג.
"""

import os
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator

# אזור הזמן שהעסק פועל לפיו. **מפורש ולא נגזר מהמארח** - זו כל ההבחנה בין
# business_today() לבין date.today(): שתי הפונקציות מחזירות את אותו ערך על שרת
# שמוגדר לישראל, אבל רק אחת מהן תחזיר אותו גם על שרת ב-us-east-1.
# ניתן לעקוף למי שמפעיל תוכנית אמריקאית בלבד; אין ברירת מחדל שקטה לערך שגוי.
_BUSINESS_TIMEZONE_NAME = os.getenv("ESOP_BUSINESS_TIMEZONE", "Asia/Jerusalem")
try:
    BUSINESS_TIMEZONE = ZoneInfo(_BUSINESS_TIMEZONE_NAME)
except (ZoneInfoNotFoundError, ValueError) as exc:  # pragma: no cover - כשל תצורה, לא זרימה
    # ValueError ולא רק ZoneInfoNotFoundError: ערך כמו "UTC+3" הוא מחרוזת לא
    # חוקית ולא אזור חסר, והוא היה עולה כ-traceback גולמי במקום ההסבר שלמטה.
    # נפילה ל-UTC בשקט הייתה מחזירה בדיוק את הבאג שהמודול הזה סוגר, ודווקא
    # במכונה שבה אי אפשר לראות אותו. requirements.txt נועץ tzdata בגלל זה.
    raise RuntimeError(
        f"אזור הזמן העסקי '{_BUSINESS_TIMEZONE_NAME}' לא נמצא. ודאו ש-tzdata "
        "מותקן (pip install -r requirements.txt), או הגדירו ESOP_BUSINESS_TIMEZONE."
    ) from exc


def utcnow() -> datetime:
    """חלופה יחידה ל-``datetime.utcnow()`` המסומן להסרה. מחזיר תמיד aware.

    קיימת כפונקציה ולא כקריאה ישירה בכל אתר, כדי שאפשר יהיה לאסור את
    ``datetime.utcnow`` כאינווריאנט של הריפו ולא בסקירה ידנית חוזרת.
    """
    return datetime.now(timezone.utc)


def business_today() -> date:
    """יום העסקים הנוכחי, לפי ``BUSINESS_TIMEZONE`` המפורש.

    **זהו השעון של כל גבול שמעניק או שולל זכות**, ושל ``effective_date`` ביומן
    האירועים. הנימוק הוא גבול היום ולא העדפה: ישראל לפני UTC, ולכן בין 00:00
    ל-03:00 בירושלים תאריך ה-UTC הוא *אתמול*. עובד שהדדליין שלו 30/08 היה
    מתקבל ב-31/08 בשעה 01:00, ובקשה מ-01/01/2027 ב-01:00 הייתה נרשמת לנצח
    כ-31/12/2026 - מעבר לגבול שנת מס, בטבלה append-only שאין בה UPDATE.

    ההבחנה מול ``recorded_at`` היא לב המודל הבי-טמפורלי ולא כפילות: ``recorded_at``
    הוא ממד ה**ידיעה** (מתי המערכת למדה) ולכן UTC, ו-``effective_date`` הוא ממד
    ה**תוקף** (באיזה יום עסקים זה קרה) ולכן כאן. איחוד שני הממדים לשעון אחד
    מוחק את ההבחנה שכל v0.6.0 עומד עליה.

    **אינו מקור לתאריך מס** - ראו ``system_today_utc`` להלן; האיסור זהה וחל על
    שתיהן. שעון אינו מקור ל-``grant_date``, בשום אזור זמן.
    """
    return datetime.now(BUSINESS_TIMEZONE).date()


def business_date_of(value: datetime) -> date:
    """באיזה **יום עסקים** התרחשה חותמת זמן מאוחסנת.

    קיימת כי ``.date()`` על עמודת ``UtcDateTime`` מחזיר את היום לפי UTC, והשוואתו
    מול ``business_today()`` היא ערבוב שעונים - שני צדדים של אותו חישוב שנמדדים
    בסרגלים שונים. בקשה שהוגשה ב-01:00 בירושלים נשמרת עם תאריך UTC של אתמול,
    ולכן ``today - requested_at.date()`` היה סופר יום אחד יותר מדי.

    זה בדיוק הפגם של ח1/ח2 בכיוון ההפוך: שם השעון היה שגוי, כאן *ההמרה* חסרה.
    """
    return value.astimezone(BUSINESS_TIMEZONE).date()


def system_today_utc() -> date:
    """התאריך שהמערכת עצמה פועלת לפיו, ב-UTC.

    ``date.today()`` מחזיר את תאריך **המארח**: אותו קוד על שרת אחר נותן תאריך
    אחר. הפונקציה הזו מסירה את התלות במארח, אבל **אינה** יום העסקים - ולכן
    היא אסורה בשכבת האפליקציה (``backend/app/``) ונשארה לזריעת נתונים בלבד.
    כל גבול שמעניק זכות עבר ל-``business_today`` ב-v0.9.1; שימוש בה כאן היה
    הרגרסיה ח1/ח2. נאכף ב-tests/test_project_invariants.py.

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
