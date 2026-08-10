from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import User, UserRole
from backend.app.schemas import SearchResultItem
from backend.app.services.search_engine import SearchEngine
from backend.app.auth import get_current_user
from backend.app.version import get_version

router = APIRouter()


@router.get("/search", response_model=List[SearchResultItem])
def search(q: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """חיפוש חופשי חכם (fuzzy) - כל תפקיד מחפש רק בתוך תחום ההרשאה שלו."""
    if current_user.role == UserRole.COMPANY_ADMIN:
        results = SearchEngine.search_for_admin(db, current_user.company_id, q)
    elif current_user.role == UserRole.TRUSTEE:
        results = SearchEngine.search_for_trustee(db, current_user.trustee_id, q)
    else:
        results = SearchEngine.search_for_employee(db, current_user.employee_id, q)

    return [SearchResultItem(entity_type=r.entity_type, entity_id=r.entity_id,
                              title=r.title, subtitle=r.subtitle, score=round(r.score, 3))
            for r in results]


@router.get("/version")
def read_version():
    """גרסת המערכת - ציבורי, בלי אימות, כדי ששלושת הפורטלים יוכלו להציג אותה.
    נקרא מהקובץ מחדש בכל בקשה כדי שעדכון גרסה ישתקף מיד, בלי restart לשרת."""
    return {"version": get_version()}
