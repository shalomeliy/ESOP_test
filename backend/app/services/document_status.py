"""מכונת המצבים של Document (v0.9.0 שלב 2).

*** לא חתימה - "אישור קבלה" (acknowledgment) בלבד. ראו models.py.Document. ***

מעבר לא-חוקי נכשל ב-409 מפורש ולא בשקט - זה בדיוק דפוס P5 מ-QA_TESTBOOK.md
(כתיבה לא אידמפוטנטית): "אישור" של מסמך שכבר אושר, או של מסמך שמעולם לא נשלח,
חייב להיחסם לפני הכתיבה, לא להוסיף אפקט שני.
"""

from fastapi import HTTPException

from backend.app.models import DocumentStatus

# מצב -> המצבים שמותר לעבור אליהם ממנו. מצב שלא מופיע כמפתח הוא סופי.
ALLOWED_TRANSITIONS = {
    DocumentStatus.DRAFT: {DocumentStatus.SENT},
    DocumentStatus.SENT: {DocumentStatus.ACKNOWLEDGED, DocumentStatus.DECLINED, DocumentStatus.EXPIRED},
}

TERMINAL_STATUSES = {DocumentStatus.ACKNOWLEDGED, DocumentStatus.DECLINED, DocumentStatus.EXPIRED}


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
