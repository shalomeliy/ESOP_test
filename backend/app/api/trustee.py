from datetime import date
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.types import business_today
from backend.app.models import Grant, User, UserRole
from backend.app.schemas import TrusteePortfolioItem
from backend.app.services.engine import MissingVestingScheduleError
from backend.app.services.audit import record_audit_event
from backend.app.services.ledger import append_event
from backend.app.auth import require_roles
from backend.app.api.exercise_requests import _vested_at, _trustee_holding_status

router = APIRouter()


# ===================================================================
# TRUSTEE PORTAL
# ===================================================================

@router.get("/trustee/portfolio", response_model=List[TrusteePortfolioItem])
def trustee_portfolio(current_user: User = Depends(require_roles(UserRole.TRUSTEE)), db: Session = Depends(get_db)):
    grants = db.query(Grant).filter(Grant.trustee_id == current_user.trustee_id).all()
    today = business_today()
    result = []
    for g in grants:
        emp = g.employee
        try:
            vested = _vested_at(g, today)
            vesting_data_missing = False
        except MissingVestingScheduleError:
            vested, vesting_data_missing = None, True
        is_met, end_date = _trustee_holding_status(g, today)
        result.append(TrusteePortfolioItem(
            grant_id=g.grant_id,
            employee_id=emp.employee_id if emp else None,
            employee_name=f"{emp.first_name} {emp.last_name}" if emp else None,
            company_id=emp.company_id if emp else None,
            company_name=emp.company.name if (emp and emp.company) else None,
            total_options=g.total_options,
            vested_options=vested,
            vesting_data_missing=vesting_data_missing,
            trustee_deposit_date=g.trustee_deposit_date,
            holding_period_end_date=end_date,
            is_trustee_holding_period_met=is_met,
        ))
    return result


@router.patch("/trustee/confirm-deposit/{grant_id}")
def confirm_trustee_deposit(grant_id: str, deposit_date: date, current_user: User = Depends(require_roles(UserRole.TRUSTEE)), db: Session = Depends(get_db)):
    grant = db.query(Grant).filter(Grant.grant_id == grant_id).first()
    if not grant:
        raise HTTPException(status_code=404, detail="Grant not found")
    if grant.trustee_id != current_user.trustee_id:
        raise HTTPException(status_code=403, detail="This grant is not under your trusteeship")

    # תיקון backdating (v0.6.0, מאושר במפורש): לא ניתן להפקיד מניות אצל הנאמן
    # לפני שהמענק בכלל נוצר - נבדק מול הדאטה הקיים לפני התכנון (0 הפרות).
    # קודם לא הייתה שום בדיקה כאן, וזה בדיוק מה שמאפשר לזכות במסלול רווח הון
    # מוקדם מדי אם ההפקדה "נרשמת" לתאריך שקדם למענק עצמו.
    if deposit_date < grant.grant_date:
        raise HTTPException(
            status_code=400,
            detail=f"Deposit date {deposit_date} cannot precede the grant date {grant.grant_date}",
        )

    before_deposit = grant.trustee_deposit_date
    grant.trustee_deposit_date = deposit_date
    record_audit_event(db, "Grant", grant_id, "DEPOSIT_CONFIRMED", current_user.user_id,
                        before={"trustee_deposit_date": before_deposit}, after={"trustee_deposit_date": deposit_date})
    append_event(db, event_type="TRUSTEE_DEPOSIT_CONFIRMED", aggregate_type="Grant",
                aggregate_id=grant_id, payload={"deposit_date": deposit_date},
                effective_date=deposit_date, actor_user_id=current_user.user_id)
    db.commit()

    return {"grant_id": grant_id, "deposit_date": str(deposit_date), "status": "DEPOSIT_CONFIRMED"}
