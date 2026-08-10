import json
from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.types import ensure_utc
from backend.app.models import User, UserRole, LedgerOwnership, LEDGER_AGGREGATE_TYPES
from backend.app.schemas import LedgerEventOut, LedgerProjectionOut
from backend.app.services.ledger import events_for, project
from backend.app.auth import require_roles

router = APIRouter()


# ===================================================================
# LEDGER (v0.6.0 שלב 3) - ציר זמן ושאילתה בי-טמפורלית. admin-only (דרך א' -
# הבוס הקיים מקבל גישה, לא נוצר תפקיד "מבקר" חדש - ראו GOAL.md/FEATURE_SPEC.md).
# ===================================================================

def _assert_ledger_ownership(db: Session, aggregate_type: str, aggregate_id: str, current_user: User) -> None:
    """מאשר גישה מול ledger_ownership - אינדקס נפרד ולא-חוזר, לעולם לא מול
    דאטה משוחזר/מוקרן (project()). זו בדיוק ההגנה מפני IDOR שחוזר בצורה חדשה
    במסכי v0.6.0, שהוזכרה בסקירת האבטחה בתכנון: מסך חדש שמאשר גישה מול
    הפרויקציה עצמה היה חוזר על אותו דפוס שכבר תוקן פעמיים (list_employees,
    employee/dashboard/{id}).

    בודק גם ש-aggregate_type בכתובת תואם לסוג האמיתי שנשמר - אחרת מזהה תקין
    של ישות אחת (למשל מענק) עם aggregate_type של ישות אחרת (למשל עובד) היה
    עובר את בדיקת ה-company_id ומופעל מול הפרויקטור הלא נכון."""
    ownership = db.get(LedgerOwnership, aggregate_id)
    if not ownership or ownership.company_id != current_user.company_id or ownership.aggregate_type != aggregate_type:
        raise HTTPException(status_code=403, detail="Not your company's data")


@router.get("/admin/ledger/{aggregate_type}/{aggregate_id}/events", response_model=List[LedgerEventOut])
def get_ledger_timeline(aggregate_type: str, aggregate_id: str,
                         current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                         db: Session = Depends(get_db)):
    """ציר הזמן המלא של ישות אחת - "מה קרה ומתי", בסדר הקיפול הקנוני."""
    if aggregate_type not in LEDGER_AGGREGATE_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported aggregate_type: {aggregate_type}")
    _assert_ledger_ownership(db, aggregate_type, aggregate_id, current_user)

    events = events_for(db, aggregate_id)
    return [
        LedgerEventOut(event_id=e.event_id, event_type=e.event_type,
                      effective_date=e.effective_date, recorded_at=e.recorded_at,
                      source=e.source, payload=json.loads(e.payload),
                      corrects_event_id=e.corrects_event_id)
        for e in events
    ]


@router.get("/admin/ledger/{aggregate_type}/{aggregate_id}/as-of", response_model=LedgerProjectionOut)
def get_ledger_as_of(aggregate_type: str, aggregate_id: str,
                     effective_date: Optional[date] = None, knowledge_date: Optional[datetime] = None,
                     current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                     db: Session = Depends(get_db)):
    """שאילתה בי-טמפורלית: 'מה חשבנו נכון' לפי כל אחד משני צירי הזמן בנפרד.
    שני הפרמטרים None => כל ההיסטוריה, כלומר "מה נכון עכשיו". state=None
    כשאין אירועים עד לחתך המבוקש - "אין נתון", לא 0/ריק."""
    if aggregate_type not in LEDGER_AGGREGATE_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported aggregate_type: {aggregate_type}")
    _assert_ledger_ownership(db, aggregate_type, aggregate_id, current_user)

    # נקודת הכשל האמיתית של החתך הבי-טמפורלי: FastAPI מפרסר
    # ?knowledge_date=...+03:00 ל-datetime aware, וההשוואה מול recorded_at
    # ה-naive הייתה מוחקת את ההיסט בשקט - כלומר מחזירה אירוע שנוצר *אחרי*
    # נקודת החתך כאילו המערכת כבר ידעה עליו. נרמול כאן, לפני כל שאילתה.
    knowledge_date = ensure_utc(knowledge_date)

    state = project(db, aggregate_type, aggregate_id,
                    as_of_effective_date=effective_date, as_of_knowledge_date=knowledge_date)
    return LedgerProjectionOut(aggregate_type=aggregate_type, aggregate_id=aggregate_id,
                               as_of_effective_date=effective_date, as_of_knowledge_date=knowledge_date,
                               state=state)
