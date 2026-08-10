"""מכונת המצבים של Document (v0.9.0 שלב 2).

*** לא חתימה - "אישור קבלה" (acknowledgment) בלבד. ראו models.py.Document. ***

מעבר לא-חוקי נכשל ב-409 מפורש ולא בשקט - זה בדיוק דפוס P5 מ-QA_TESTBOOK.md
(כתיבה לא אידמפוטנטית): "אישור" של מסמך שכבר אושר, או של מסמך שמעולם לא נשלח,
חייב להיחסם לפני הכתיבה, לא להוסיף אפקט שני.
"""

from datetime import timedelta

from fastapi import HTTPException

from backend.app.models import Document, DocumentStatus
from backend.app.types import utcnow

# תוקף בקשת אישור קבלה. 30 יום הוא הנוהג המקובל בבקשות אישור, והוא מדיניות
# מוצר בלבד - אין בסעיף 102 תקופת תוקף לבקשת אישור, ולכן אין כאן כלל מס.
ACKNOWLEDGMENT_WINDOW_DAYS = 30

# מצב -> המצבים שמותר לעבור אליהם ממנו. מצב שלא מופיע כמפתח הוא סופי.
ALLOWED_TRANSITIONS = {
    DocumentStatus.DRAFT: {DocumentStatus.SENT},
    DocumentStatus.SENT: {DocumentStatus.ACKNOWLEDGED, DocumentStatus.DECLINED, DocumentStatus.EXPIRED},
}

TERMINAL_STATUSES = {DocumentStatus.ACKNOWLEDGED, DocumentStatus.DECLINED, DocumentStatus.EXPIRED}


def expire_due(db, documents) -> None:
    """מפקיע כל מסמך SENT ברשימה שעבר את מועד התוקף שלו.

    "טאטוא עצל" ולא scheduler: למערכת אין תהליך רקע, והוספת אחד רק בשביל
    המעבר הזה הייתה מוסיפה רכיב תפעולי שלם לפיצ'ר בגודל עמודה. במקום זה
    ההפקעה מתרחשת בכל נתיב שטוען מסמך - צפייה או פעולה - ולכן מצב ה-DB ומה
    שמוצג על המסך תמיד מסכימים. אין כאן "GET שכותב" מסוכן: המעבר
    SENT -> EXPIRED הוא חד-כיווני, אידמפוטנטי, ותלוי אך ורק בערך שכבר מאוחסן.

    commit אחד לכל הרשימה ולא אחד לכל מסמך: ספריית המסמכים של חברה גדולה
    נטענת במלואה, ואותה הקפדה שמנעה שם N+1 בשאילתות תקפה גם לכתיבות.

    ``expires_at is None`` הוא "אין דדליין" ולא "פג" - כך מסמכים שנשלחו לפני
    v0.9.1 נשארים פתוחים במקום להיסגר ברגע השדרוג בלי שאיש הודיע לעובד.
    """
    now = utcnow()
    due = [d for d in documents
           if d.status == DocumentStatus.SENT and d.expires_at is not None and now > d.expires_at]
    if not due:
        return
    for document in due:
        document.status = DocumentStatus.EXPIRED
    db.commit()


def expire_if_due(db, document: Document) -> Document:
    expire_due(db, [document])
    return document


def deadline_for(sent_at):
    """מועד הפקיעה הנגזר משליחה. פונקציה ולא חישוב inline כדי שיהיה מקום אחד
    יחיד שהבדיקות והקוד מסכימים עליו."""
    return sent_at + timedelta(days=ACKNOWLEDGMENT_WINDOW_DAYS)


def assert_is_current_version(is_latest: bool, target: DocumentStatus) -> None:
    """גרסה מיושנת לא עוברת שום מצב - גם לא אישור קבלה.

    למה בשרת ולא רק במסך (v0.9.0 שלב 3): אישור קבלה נצמד למסמך *מסוים*, וזו כל
    הסיבה שגרסה קודמת נשמרת ולא נדרסת. אישור על גרסה שהחברה עצמה כבר החליפה
    מייצר רשומת ציות שקרית - "העובד אישר" על נייר שאינו הנייר הנוכחי.

    עד שלב 3 הבדיקה הזו הייתה קיימת בשליחה בלבד, ולא באישור/דחייה - בדיוק דפוס
    P3 (ולידציה קיימת בנתיב אחד וחסרה במקביל לו). לכן היא יושבת כאן, בפונקציה
    שכל ארבעת הנתיבים (עובד/נאמן × אישור/דחייה) עוברים בה, ולא בכל נתיב בנפרד.
    """
    if not is_latest:
        raise HTTPException(
            status_code=409,
            detail=(f"This is a superseded version - it cannot become {target.value}. "
                    "Act on the latest version of this document instead."),
        )


def assert_transition_allowed(current: DocumentStatus, target: DocumentStatus) -> None:
    """נכשל ב-409 אם המעבר לא חוקי. 409 ולא 400: הבקשה תקינה מבחינת מבנה,
    המצב הוא שלא מאפשר אותה - אותו עיקרון כמו MissingVestingScheduleError."""
    if current == target:
        raise HTTPException(
            status_code=409,
            detail=f"Document is already {current.value} - this action has no further effect",
        )
    allowed = ALLOWED_TRANSITIONS.get(current, set())
    if target not in allowed:
        if current in TERMINAL_STATUSES:
            raise HTTPException(
                status_code=409,
                detail=f"Document is {current.value}, which is final - it cannot become {target.value}",
            )
        raise HTTPException(
            status_code=409,
            detail=f"Cannot move a document from {current.value} to {target.value}",
        )
