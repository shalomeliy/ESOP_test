import json
from sqlalchemy.orm import Session

from backend.app.models import AuditLog


def record_audit_event(db: Session, entity_type: str, entity_id: str, action: str,
                        actor_user_id: str = None, before: dict = None, after: dict = None,
                        notes: str = None) -> None:
    """מוסיף רשומת audit ל-session הנוכחי (לא עושה commit - חלק מאותה טרנזקציה
    כמו הפעולה העסקית עצמה, כדי שלא ייווצר מצב שבו הפעולה הצליחה אבל ה-audit לא)."""
    db.add(AuditLog(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        actor_user_id=actor_user_id,
        before_value=json.dumps(before, default=str) if before is not None else None,
        after_value=json.dumps(after, default=str) if after is not None else None,
        notes=notes,
    ))
