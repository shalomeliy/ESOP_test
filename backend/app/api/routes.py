from datetime import date, datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import (
    Employee, EmployeeStatus, OptionPool, Grant, TaxRatesHistory, StockPricesHistory,
    Trustee, VestingSchedule, User, UserRole, UserSession, ExerciseRequest, ExerciseRequestStatus,
    Company, AuditLog,
)
from backend.app.schemas import (
    EmployeeStatusUpdate, ExerciseSimulationRequest, ExerciseSimulationResponse,
    CreateGrantRequest, CreateGrantResponse, LoginRequest, LoginResponse,
    EmployeeCreateRequest, EmployeeUpdateRequest, EmployeeOut,
    CompanyUpdateRequest, CompanyOut, GrantOut, PoolOut,
    TrusteePortfolioItem, ExerciseRequestCreate, ExerciseRequestReview, ExerciseRequestOut,
    AuditLogOut,
)
from backend.app.services.engine import DeterministicESOPEngine
from backend.app.services.audit import record_audit_event
from backend.app.auth import hash_password, verify_password, create_session, get_current_user, require_roles

router = APIRouter()


# ===================================================================
# AUTH
# ===================================================================

@router.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == payload.username).first()
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash, user.password_salt):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = create_session(db, user)

    display_name = user.username
    if user.employee_id:
        emp = db.query(Employee).filter(Employee.employee_id == user.employee_id).first()
        if emp:
            display_name = f"{emp.first_name} {emp.last_name}"
    elif user.company_id:
        comp = db.query(Company).filter(Company.company_id == user.company_id).first()
        if comp:
            display_name = comp.name
    elif user.trustee_id:
        trustee_row = db.query(Trustee).filter(Trustee.trustee_id == user.trustee_id).first()
        if trustee_row:
            display_name = trustee_row.name

    return LoginResponse(
        token=token,
        role=user.role.value if hasattr(user.role, "value") else user.role,
        display_name=display_name,
        company_id=user.company_id,
        trustee_id=user.trustee_id,
        employee_id=user.employee_id,
    )


@router.post("/auth/logout")
def logout(authorization: str = Header(None), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
        db.query(UserSession).filter(UserSession.token == token).delete()
        db.commit()
    return {"status": "logged_out"}


@router.get("/auth/me")
def get_me(current_user: User = Depends(get_current_user)):
    return {
        "user_id": current_user.user_id,
        "username": current_user.username,
        "role": current_user.role,
        "company_id": current_user.company_id,
        "trustee_id": current_user.trustee_id,
        "employee_id": current_user.employee_id,
    }


# ===================================================================
# COMPANY ADMIN PORTAL - Employees CRUD
# ===================================================================

@router.get("/admin/employees", response_model=List[EmployeeOut])
def list_employees(current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    # הערה: מחזיר את כל העובדים במערכת ולא רק את אלו של current_user.company_id.
    employees = db.query(Employee).all()
    return employees


@router.post("/admin/employees", response_model=EmployeeOut)
def create_employee(payload: EmployeeCreateRequest, current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    existing = db.query(Employee).filter(Employee.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Employee with this email already exists")

    emp = Employee(
        company_id=current_user.company_id,
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=payload.email,
        country_code=payload.country_code,
        status=EmployeeStatus.ACTIVE,
        hire_date=payload.hire_date,
        birth_date=payload.birth_date,
    )
    db.add(emp)
    db.flush()

    # פרובייז אוטומטי של חשבון כניסה לפורטל העובד עם סיסמת ברירת מחדל.
    pw_hash, salt = hash_password("Welcome123!")
    new_user = User(username=emp.email, password_hash=pw_hash, password_salt=salt,
                    role=UserRole.EMPLOYEE, employee_id=emp.employee_id)
    db.add(new_user)

    db.commit()
    db.refresh(emp)
    return emp


@router.put("/admin/employees/{employee_id}", response_model=EmployeeOut)
def update_employee(employee_id: str, payload: EmployeeUpdateRequest,
                     current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    emp = db.query(Employee).filter(Employee.employee_id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    if emp.company_id != current_user.company_id:
        raise HTTPException(status_code=403, detail="Cannot modify an employee outside your company")

    before = {"first_name": emp.first_name, "last_name": emp.last_name, "email": emp.email,
              "country_code": emp.country_code, "birth_date": emp.birth_date}
    for field in ["first_name", "last_name", "email", "country_code", "birth_date"]:
        value = getattr(payload, field)
        if value is not None:
            setattr(emp, field, value)

    record_audit_event(db, "Employee", employee_id, "UPDATE", current_user.user_id,
                        before=before, after={"first_name": emp.first_name, "last_name": emp.last_name,
                                              "email": emp.email, "country_code": emp.country_code,
                                              "birth_date": emp.birth_date})
    db.commit()
    db.refresh(emp)
    return emp


@router.delete("/admin/employees/{employee_id}")
def delete_employee(employee_id: str, current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    emp = db.query(Employee).filter(Employee.employee_id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    if emp.company_id != current_user.company_id:
        raise HTTPException(status_code=403, detail="Cannot delete an employee outside your company")

    grants_count = db.query(Grant).filter(Grant.employee_id == employee_id).count()

    if grants_count == 0:
        # מעולם לא קיבל grant - מותר למחוק לגמרי (hard delete), כולל חשבון הכניסה שלו.
        orphan_user_ids = [u.user_id for u in db.query(User.user_id).filter(User.employee_id == employee_id).all()]
        if orphan_user_ids:
            db.query(UserSession).filter(UserSession.user_id.in_(orphan_user_ids)).delete(synchronize_session=False)
            db.query(User).filter(User.employee_id == employee_id).delete()
        record_audit_event(db, "Employee", employee_id, "HARD_DELETE", current_user.user_id,
                            before={"email": emp.email, "status": emp.status})
        db.delete(emp)
        db.commit()
        return {"employee_id": employee_id, "deleted": "hard"}
    else:
        # יש היסטוריית grants - אסור למחוק, רק מסמנים TERMINATED.
        before_status = emp.status
        emp.status = EmployeeStatus.TERMINATED
        emp.termination_date = date.today()
        record_audit_event(db, "Employee", employee_id, "SOFT_DELETE_TERMINATE", current_user.user_id,
                            before={"status": before_status}, after={"status": "TERMINATED", "termination_date": emp.termination_date})
        db.commit()
        return {"employee_id": employee_id, "deleted": "soft", "new_status": "TERMINATED"}


# ===================================================================
# COMPANY ADMIN PORTAL - Company profile
# ===================================================================

@router.get("/admin/company", response_model=CompanyOut)
def get_my_company(current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    comp = db.query(Company).filter(Company.company_id == current_user.company_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Company not found")
    return comp


@router.put("/admin/company", response_model=CompanyOut)
def update_my_company(payload: CompanyUpdateRequest, current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    comp = db.query(Company).filter(Company.company_id == current_user.company_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Company not found")
    before = {"name": comp.name, "country_code": comp.country_code}
    if payload.name is not None:
        comp.name = payload.name
    if payload.country_code is not None:
        comp.country_code = payload.country_code
    record_audit_event(db, "Company", comp.company_id, "UPDATE", current_user.user_id,
                        before=before, after={"name": comp.name, "country_code": comp.country_code})
    db.commit()
    db.refresh(comp)
    return comp


@router.delete("/admin/company")
def delete_my_company(current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    comp = db.query(Company).filter(Company.company_id == current_user.company_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Company not found")

    emp_count = db.query(Employee).filter(Employee.company_id == comp.company_id).count()
    pool_count = db.query(OptionPool).filter(OptionPool.company_id == comp.company_id).count()
    trustee_count = db.query(Trustee).filter(Trustee.company_id == comp.company_id).count()

    if emp_count == 0 and pool_count == 0 and trustee_count == 0:
        orphan_user_ids = [u.user_id for u in db.query(User.user_id).filter(User.company_id == comp.company_id).all()]
        if orphan_user_ids:
            db.query(UserSession).filter(UserSession.user_id.in_(orphan_user_ids)).delete(synchronize_session=False)
            db.query(User).filter(User.company_id == comp.company_id).delete()
        record_audit_event(db, "Company", comp.company_id, "HARD_DELETE", current_user.user_id, before={"name": comp.name})
        db.delete(comp)
        db.commit()
        return {"company_id": comp.company_id, "deleted": "hard"}
    else:
        record_audit_event(db, "Company", comp.company_id, "SOFT_DELETE_DEACTIVATE", current_user.user_id,
                            before={"is_active": comp.is_active}, after={"is_active": False})
        comp.is_active = False
        db.commit()
        return {"company_id": comp.company_id, "deleted": "soft", "is_active": False}


@router.get("/admin/pools", response_model=List[PoolOut])
def list_pools(current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    return db.query(OptionPool).filter(OptionPool.company_id == current_user.company_id).all()


@router.get("/admin/grants", response_model=List[GrantOut])
def list_company_grants(current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    pool_ids = [p.pool_id for p in db.query(OptionPool.pool_id).filter(OptionPool.company_id == current_user.company_id).all()]
    return db.query(Grant).filter(Grant.pool_id.in_(pool_ids)).all()


@router.get("/admin/exercise-requests", response_model=List[ExerciseRequestOut])
def list_pending_requests_admin(current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    pool_ids = [p.pool_id for p in db.query(OptionPool.pool_id).filter(OptionPool.company_id == current_user.company_id).all()]
    grant_ids = [g.grant_id for g in db.query(Grant.grant_id).filter(Grant.pool_id.in_(pool_ids)).all()]
    return db.query(ExerciseRequest).filter(ExerciseRequest.grant_id.in_(grant_ids)).all()


@router.patch("/admin/exercise-requests/{request_id}", response_model=ExerciseRequestOut)
def review_exercise_request_admin(request_id: str, payload: ExerciseRequestReview,
                                   current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    req = db.query(ExerciseRequest).filter(ExerciseRequest.request_id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    grant = db.query(Grant).filter(Grant.grant_id == req.grant_id).first()
    pool = db.query(OptionPool).filter(OptionPool.pool_id == grant.pool_id).first()
    if pool.company_id != current_user.company_id:
        raise HTTPException(status_code=403, detail="Not your company's grant")

    # הערה: לא בודקים כאן שה-vested_options בפועל מכסה את options_requested, ולא
    # בודקים אם כבר יש בקשות אחרות שאושרו לאותו grant, ולא בודקים
    # is_trustee_holding_period_met לפני אישור.
    req.status = ExerciseRequestStatus.APPROVED if payload.approve else ExerciseRequestStatus.REJECTED
    req.reviewed_by_user_id = current_user.user_id
    req.reviewed_at = datetime.utcnow()
    req.review_notes = payload.notes
    record_audit_event(db, "ExerciseRequest", request_id,
                        "APPROVE" if payload.approve else "REJECT", current_user.user_id,
                        before={"status": "PENDING"}, after={"status": req.status.value, "notes": payload.notes})
    db.commit()
    db.refresh(req)
    return req


@router.get("/admin/audit-log", response_model=List[AuditLogOut])
def get_audit_log(entity_type: str, entity_id: str,
                   current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    """היסטוריית שינויים לישות בודדת. בודק בעלות לפי company_id של current_user, לפי סוג הישות."""
    if entity_type == "Employee":
        emp = db.query(Employee).filter(Employee.employee_id == entity_id).first()
        if not emp or emp.company_id != current_user.company_id:
            raise HTTPException(status_code=403, detail="Not your company's employee")
    elif entity_type == "Grant":
        grant = db.query(Grant).filter(Grant.grant_id == entity_id).first()
        pool = db.query(OptionPool).filter(OptionPool.pool_id == grant.pool_id).first() if grant else None
        if not grant or not pool or pool.company_id != current_user.company_id:
            raise HTTPException(status_code=403, detail="Not your company's grant")
    elif entity_type == "Company":
        if entity_id != current_user.company_id:
            raise HTTPException(status_code=403, detail="Not your company")
    elif entity_type == "ExerciseRequest":
        req = db.query(ExerciseRequest).filter(ExerciseRequest.request_id == entity_id).first()
        grant = db.query(Grant).filter(Grant.grant_id == req.grant_id).first() if req else None
        pool = db.query(OptionPool).filter(OptionPool.pool_id == grant.pool_id).first() if grant else None
        if not req or not pool or pool.company_id != current_user.company_id:
            raise HTTPException(status_code=403, detail="Not your company's exercise request")
    else:
        raise HTTPException(status_code=400, detail="Unsupported entity_type")

    return (
        db.query(AuditLog)
        .filter(AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id)
        .order_by(AuditLog.occurred_at.desc())
        .all()
    )


# --- COMPANY ADMIN PORTAL ENDPOINTS (legacy) ---
@router.patch("/admin/employees/{employee_id}/status")
def update_employee_status(employee_id: str, payload: EmployeeStatusUpdate,
                            current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    employee = db.query(Employee).filter(Employee.employee_id == employee_id).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    if employee.company_id != current_user.company_id:
        raise HTTPException(status_code=403, detail="Cannot modify an employee outside your company")

    before_status = employee.status
    employee.status = payload.status
    returned_options = 0.0

    if payload.status == EmployeeStatus.TERMINATED:
        employee.termination_date = payload.effective_date
        if payload.return_unvested_to_pool:
            for grant in employee.grants:
                vested = DeterministicESOPEngine.calculate_vested_options(grant, grant.vesting_schedule, payload.effective_date)
                unvested = grant.total_options - vested
                returned_options += unvested

                pool = db.query(OptionPool).filter(OptionPool.pool_id == grant.pool_id).first()
                if pool:
                    pool.unallocated_shares += unvested
                    pool.allocated_shares -= unvested

    record_audit_event(db, "Employee", employee_id, "STATUS_CHANGE", current_user.user_id,
                        before={"status": before_status.value if hasattr(before_status, "value") else before_status},
                        after={"status": employee.status, "effective_date": payload.effective_date,
                               "returned_options": returned_options})
    db.commit()
    return {"employee_id": employee_id, "status": employee.status, "returned_options": returned_options}


@router.post("/admin/grants", response_model=CreateGrantResponse)
def create_grant(payload: CreateGrantRequest,
                  current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    """יצירת מענק אופציות חדש לעובד, כולל שיוך לפול, יצירת לוח הבשלה, ועדכון יתרות הפול."""
    employee = db.query(Employee).filter(Employee.employee_id == payload.employee_id).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    pool = db.query(OptionPool).filter(OptionPool.pool_id == payload.pool_id).first()
    if not pool:
        raise HTTPException(status_code=404, detail="Option pool not found")
    if pool.company_id != current_user.company_id:
        raise HTTPException(status_code=403, detail="Cannot grant against a pool outside your company")

    if payload.trustee_id:
        trustee = db.query(Trustee).filter(Trustee.trustee_id == payload.trustee_id).first()
        if not trustee:
            raise HTTPException(status_code=404, detail="Trustee not found")

    if payload.total_options <= 0:
        raise HTTPException(status_code=400, detail="total_options must be positive")

    if payload.total_options > pool.unallocated_shares:
        raise HTTPException(
            status_code=400,
            detail=f"Not enough unallocated shares in pool (available: {pool.unallocated_shares})",
        )

    grant = Grant(
        employee_id=payload.employee_id,
        pool_id=payload.pool_id,
        trustee_id=payload.trustee_id,
        grant_date=payload.grant_date,
        grant_type=payload.grant_type,
        total_options=payload.total_options,
        exercise_price=payload.exercise_price,
        currency=payload.currency,
        post_termination_window_days=payload.post_termination_window_days,
    )
    db.add(grant)

    pool.allocated_shares += payload.total_options
    pool.unallocated_shares -= payload.total_options

    db.flush()

    schedule = VestingSchedule(
        grant_id=grant.grant_id,
        start_date=payload.grant_date,
        cliff_months=payload.cliff_months,
        total_months=payload.total_months,
    )
    db.add(schedule)

    record_audit_event(db, "Grant", grant.grant_id, "CREATE", current_user.user_id,
                        after={"employee_id": grant.employee_id, "pool_id": grant.pool_id,
                               "total_options": grant.total_options, "grant_type": grant.grant_type.value,
                               "exercise_price": grant.exercise_price})

    db.commit()
    db.refresh(grant)
    db.refresh(schedule)
    db.refresh(pool)

    return CreateGrantResponse(
        grant_id=grant.grant_id,
        employee_id=grant.employee_id,
        pool_id=grant.pool_id,
        total_options=grant.total_options,
        vesting_schedule_id=schedule.schedule_id,
        pool_allocated_shares=pool.allocated_shares,
        pool_unallocated_shares=pool.unallocated_shares,
    )


# ===================================================================
# TRUSTEE PORTAL
# ===================================================================

@router.get("/trustee/portfolio", response_model=List[TrusteePortfolioItem])
def trustee_portfolio(current_user: User = Depends(require_roles(UserRole.TRUSTEE)), db: Session = Depends(get_db)):
    grants = db.query(Grant).filter(Grant.trustee_id == current_user.trustee_id).all()
    today = date.today()
    result = []
    for g in grants:
        emp = g.employee
        vested = DeterministicESOPEngine.calculate_vested_options(g, g.vesting_schedule, today)
        is_met, end_date = DeterministicESOPEngine.check_trustee_holding_period(g, today)
        result.append(TrusteePortfolioItem(
            grant_id=g.grant_id,
            employee_id=emp.employee_id if emp else None,
            employee_name=f"{emp.first_name} {emp.last_name}" if emp else None,
            company_id=emp.company_id if emp else None,
            company_name=emp.company.name if (emp and emp.company) else None,
            total_options=g.total_options,
            vested_options=vested,
            trustee_deposit_date=g.trustee_deposit_date,
            holding_period_end_date=end_date,
            is_trustee_holding_period_met=is_met,
        ))
    return result


@router.get("/trustee/exercise-requests", response_model=List[ExerciseRequestOut])
def list_pending_requests_trustee(current_user: User = Depends(require_roles(UserRole.TRUSTEE)), db: Session = Depends(get_db)):
    grant_ids = [g.grant_id for g in db.query(Grant.grant_id).filter(Grant.trustee_id == current_user.trustee_id).all()]
    return db.query(ExerciseRequest).filter(ExerciseRequest.grant_id.in_(grant_ids)).all()


@router.patch("/trustee/exercise-requests/{request_id}", response_model=ExerciseRequestOut)
def review_exercise_request_trustee(request_id: str, payload: ExerciseRequestReview,
                                     current_user: User = Depends(require_roles(UserRole.TRUSTEE)), db: Session = Depends(get_db)):
    req = db.query(ExerciseRequest).filter(ExerciseRequest.request_id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    grant = db.query(Grant).filter(Grant.grant_id == req.grant_id).first()
    if grant.trustee_id != current_user.trustee_id:
        raise HTTPException(status_code=403, detail="Not your trusteeship")

    req.status = ExerciseRequestStatus.APPROVED if payload.approve else ExerciseRequestStatus.REJECTED
    req.reviewed_by_user_id = current_user.user_id
    req.reviewed_at = datetime.utcnow()
    req.review_notes = payload.notes
    record_audit_event(db, "ExerciseRequest", request_id,
                        "APPROVE" if payload.approve else "REJECT", current_user.user_id,
                        before={"status": "PENDING"}, after={"status": req.status.value, "notes": payload.notes})
    db.commit()
    db.refresh(req)
    return req


@router.patch("/trustee/confirm-deposit/{grant_id}")
def confirm_trustee_deposit(grant_id: str, deposit_date: date,
                             current_user: User = Depends(require_roles(UserRole.TRUSTEE)), db: Session = Depends(get_db)):
    grant = db.query(Grant).filter(Grant.grant_id == grant_id).first()
    if not grant:
        raise HTTPException(status_code=404, detail="Grant not found")
    if grant.trustee_id != current_user.trustee_id:
        raise HTTPException(status_code=403, detail="This grant is not under your trusteeship")

    before_deposit = grant.trustee_deposit_date
    grant.trustee_deposit_date = deposit_date
    record_audit_event(db, "Grant", grant_id, "DEPOSIT_CONFIRMED", current_user.user_id,
                        before={"trustee_deposit_date": before_deposit}, after={"trustee_deposit_date": deposit_date})
    db.commit()

    return {"grant_id": grant_id, "deposit_date": str(deposit_date), "status": "DEPOSIT_CONFIRMED"}


# ===================================================================
# EMPLOYEE PORTAL
# ===================================================================

@router.get("/employee/dashboard/{employee_id}")
def get_employee_dashboard(employee_id: str, current_user: User = Depends(require_roles(UserRole.EMPLOYEE)), db: Session = Depends(get_db)):
    # הערה: לא בודקים כאן ש-employee_id שווה ל-current_user.employee_id.
    employee = db.query(Employee).filter(Employee.employee_id == employee_id).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    grants_data = []
    today = date.today()

    for grant in employee.grants:
        vested = DeterministicESOPEngine.calculate_vested_options(grant, grant.vesting_schedule, today)
        is_trustee_met, end_date = DeterministicESOPEngine.check_trustee_holding_period(grant, today)
        is_within_ptw, ptw_deadline = DeterministicESOPEngine.check_post_termination_exercise_window(
            grant, employee, today
        )

        grants_data.append({
            "grant_id": grant.grant_id,
            "total_options": grant.total_options,
            "vested_options": vested,
            "exercise_price": grant.exercise_price,
            "is_trustee_holding_period_met": is_trustee_met,
            "holding_period_end_date": str(end_date),
            "is_within_post_termination_window": is_within_ptw,
            "post_termination_exercise_deadline": str(ptw_deadline) if ptw_deadline else None,
        })

    return {"employee_name": f"{employee.first_name} {employee.last_name}", "grants": grants_data}


@router.post("/employee/simulate-exercise", response_model=ExerciseSimulationResponse)
def simulate_exercise(payload: ExerciseSimulationRequest, current_user: User = Depends(require_roles(UserRole.EMPLOYEE)), db: Session = Depends(get_db)):
    grant = db.query(Grant).filter(Grant.grant_id == payload.grant_id).first()
    if not grant:
        raise HTTPException(status_code=404, detail="Grant not found")

    is_met, end_date = DeterministicESOPEngine.check_trustee_holding_period(grant, payload.exercise_date)
    is_within_ptw, ptw_deadline = DeterministicESOPEngine.check_post_termination_exercise_window(
        grant, grant.employee, payload.exercise_date
    )

    latest_price = db.query(StockPricesHistory).filter(StockPricesHistory.company_id == grant.employee.company_id).order_by(StockPricesHistory.price_date.desc()).first()
    stock_price = latest_price.fmv_price if latest_price else grant.exercise_price

    tax_rule = (
        db.query(TaxRatesHistory)
        .filter(
            TaxRatesHistory.grant_type == grant.grant_type,
            TaxRatesHistory.country_code == grant.employee.country_code,
            TaxRatesHistory.effective_start_date <= payload.exercise_date,
        )
        .order_by(TaxRatesHistory.effective_start_date.desc())
        .first()
    )
    tax_rate = tax_rule.capital_gains_rate if tax_rule else 0.25

    total_cost = payload.options_to_exercise * grant.exercise_price
    gain = max(0.0, (stock_price - grant.exercise_price) * payload.options_to_exercise)
    estimated_tax = gain * tax_rate

    return ExerciseSimulationResponse(
        grant_id=grant.grant_id,
        is_trustee_holding_period_met=is_met,
        holding_period_end_date=end_date,
        current_stock_price=stock_price,
        total_exercise_cost=total_cost,
        estimated_tax_amount=estimated_tax,
        applied_tax_rate=tax_rate,
        tax_rule_source=tax_rule.official_source_url if tax_rule else "https://www.gov.il/he/departments/tax_authority",
        is_within_post_termination_window=is_within_ptw,
        post_termination_exercise_deadline=ptw_deadline,
    )


@router.post("/employee/exercise-requests", response_model=ExerciseRequestOut)
def create_exercise_request(payload: ExerciseRequestCreate, current_user: User = Depends(require_roles(UserRole.EMPLOYEE)), db: Session = Depends(get_db)):
    grant = db.query(Grant).filter(Grant.grant_id == payload.grant_id).first()
    if not grant:
        raise HTTPException(status_code=404, detail="Grant not found")
    if grant.employee_id != current_user.employee_id:
        raise HTTPException(status_code=403, detail="This grant does not belong to you")

    is_within_ptw, ptw_deadline = DeterministicESOPEngine.check_post_termination_exercise_window(
        grant, grant.employee, date.today()
    )
    if not is_within_ptw:
        raise HTTPException(
            status_code=400,
            detail=f"Post-termination exercise window has closed (deadline was {ptw_deadline})",
        )

    # הערה: לא בודקים כאן מול vested_options בפועל, ולא מול בקשות אחרות ל-grant הזה
    # שכבר ב-PENDING/APPROVED - אפשר להגיש כמה בקשות שרוצים על אותו grant.
    req = ExerciseRequest(
        grant_id=grant.grant_id,
        employee_id=current_user.employee_id,
        options_requested=payload.options_to_exercise,
        status=ExerciseRequestStatus.PENDING,
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return req


@router.get("/employee/exercise-requests", response_model=List[ExerciseRequestOut])
def list_my_exercise_requests(current_user: User = Depends(require_roles(UserRole.EMPLOYEE)), db: Session = Depends(get_db)):
    return db.query(ExerciseRequest).filter(ExerciseRequest.employee_id == current_user.employee_id).all()
