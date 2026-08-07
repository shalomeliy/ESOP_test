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
