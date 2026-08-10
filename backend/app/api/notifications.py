from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import User, UserRole, NotificationPreference, NotificationDismissal, NOTIFICATION_DEFAULT_LEAD_DAYS
from backend.app.schemas import (
    NotificationFeedOut, NotificationCountOut, NotificationPreferencesOut,
    NotificationPreferencesUpdate,
)
from backend.app.services import notifications as notif
from backend.app.auth import get_current_user

router = APIRouter()


# ===================================================================
# NOTIFICATIONS - מחושבות על קריאה, לא נשמרות. ראו services/notifications.py
# ===================================================================

def _feed_for(current_user: User, db: Session) -> "notif.NotificationFeed":
    """הפניה לפי תפקיד. הסקופ נאכף בתוך services/notifications.py, שם הוא
    נקבע מ-current_user בלבד - אין כאן פרמטר שהלקוח יכול לדרוס."""
    if current_user.role == UserRole.COMPANY_ADMIN:
        return notif.for_admin(db, current_user.company_id, current_user.user_id)
    if current_user.role == UserRole.TRUSTEE:
        return notif.for_trustee(db, current_user.trustee_id, current_user.user_id)
    return notif.for_employee(db, current_user.employee_id, current_user.user_id)


@router.get("/notifications", response_model=NotificationFeedOut)
def list_notifications(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    feed = _feed_for(current_user, db)
    return NotificationFeedOut(items=[vars(i) for i in feed.items],
                                degraded_entities=feed.degraded_entities, total=feed.total)


@router.get("/notifications/unread-count", response_model=NotificationCountOut)
def notifications_unread_count(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # מדווח את הסך האמיתי ולא את הפיד הקטוע - אחרת התקרה נראית כמו העובדה.
    return NotificationCountOut(count=_feed_for(current_user, db).total)


@router.post("/notifications/{key:path}/dismiss", status_code=204)
def dismiss_notification(key: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """idempotent: נשען על ה-unique index ברמת ה-DB במקום check-then-insert,
    שהוא race שמייצר כפילויות בדיוק בלחיצה כפולה."""
    db.add(NotificationDismissal(user_id=current_user.user_id, notification_key=key))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    return Response(status_code=204)


@router.get("/notifications/preferences", response_model=NotificationPreferencesOut)
def get_notification_preferences(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    effective = notif._effective_preferences(db, current_user.user_id)
    return NotificationPreferencesOut(preferences=[
        {"rule": rule, "enabled": cfg["enabled"], "lead_days": cfg["lead_days"]}
        for rule, cfg in effective.items()
    ])


@router.put("/notifications/preferences", response_model=NotificationPreferencesOut)
def update_notification_preferences(payload: NotificationPreferencesUpdate,
                                     current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    for item in payload.preferences:
        if item.rule not in NOTIFICATION_DEFAULT_LEAD_DAYS:
            raise HTTPException(status_code=400, detail=f"Unknown notification rule: {item.rule}")
        if item.lead_days < 0:
            raise HTTPException(status_code=400, detail="lead_days must not be negative")

    for item in payload.preferences:
        row = (db.query(NotificationPreference)
               .filter(NotificationPreference.user_id == current_user.user_id,
                       NotificationPreference.rule == item.rule).first())
        if row:
            row.enabled, row.lead_days = item.enabled, item.lead_days
        else:
            db.add(NotificationPreference(user_id=current_user.user_id, rule=item.rule,
                                           enabled=item.enabled, lead_days=item.lead_days))
    db.commit()
    return get_notification_preferences(current_user, db)
