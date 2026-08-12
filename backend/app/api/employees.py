from datetime import date
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import Employee, EmployeeStatus, OptionPool, Grant, User, UserRole, UserSession
from backend.app.schemas import (
    EmployeeStatusUpdate, EmployeeCreateRequest, EmployeeUpdateRequest, EmployeeOut, EmployeeCreateResponse,
)
from backend.app.services.engine import DeterministicESOPEngine, MissingVestingScheduleError
from backend.app.services.audit import record_audit_event
from backend.app.services.ledger import append_event, record_ownership
from backend.app.auth import require_roles, generate_temporary_password, hash_password
from backend.app.types import business_today

router = APIRouter()


def _validate_termination_date(termination_date: date, hire_date: date, today: date) -> str | None:
    # תאריך סיום העסקה מזין את דדליין חלון המימוש - טעות הקלדה כאן מזיזה זכות
    # כספית אמיתית. שני הכיוונים נבדקים: לפני הגיוס אינו הגיוני, ועתידי חוסם
    # אירוע שעדיין לא קרה מלהיכנס ל-ledger append-only לפני שהתרחש בפועל.
    if termination_date < hire_date:
        return "termination_date cannot be before the employee's hire_date"
    if termination_date > today:
        return "termination_date cannot be in the future"
    return None


# ===================================================================
# COMPANY ADMIN PORTAL - Employees CRUD
# ===================================================================

@router.get("/admin/employees", response_model=List[EmployeeOut])
def list_employees(current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)), db: Session = Depends(get_db)):
    # סקופ לפי החברה של המשתמש. קודם הוחזרו כל העובדים בכל החברות (דליפה חוצת-לקוחות).
    return db.query(Employee).filter(Employee.company_id == current_user.company_id).all()


@router.post("/admin/employees", response_model=EmployeeCreateResponse)
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

    # v0.6.0: אירוע בסיס לעובד חדש - בלי זה, עובד שנוצר *אחרי* backfill_ledger.py
    # לא יהיה לו שום אירוע לקפל, ו-EMPLOYEE_STATUS_CHANGED עתידי (עזיבה וכו')
    # ייפול על state=None בלי אפקט (ראו project_employee).
    record_ownership(db, aggregate_id=emp.employee_id, aggregate_type="Employee",
                     company_id=emp.company_id, employee_id=emp.employee_id)
    append_event(db, event_type="EMPLOYEE_STATE_ESTABLISHED", aggregate_type="Employee",
                aggregate_id=emp.employee_id,
                payload={"status": EmployeeStatus.ACTIVE.value, "termination_date": None},
                effective_date=emp.hire_date, actor_user_id=current_user.user_id)

    # פרובייז אוטומטי של חשבון כניסה עם סיסמה חד-פעמית מוגרלת - לא הקבועה
    # Welcome123! שהייתה זהה לכל עובד חדש בכל חברה. must_change_password=True
    # חוסם כל endpoint עסקי (require_roles) עד שהעובד יחליף אותה בעצמו.
    temp_password = generate_temporary_password()
    pw_hash, salt = hash_password(temp_password)
    new_user = User(username=emp.email, password_hash=pw_hash, password_salt=salt,
                    role=UserRole.EMPLOYEE, employee_id=emp.employee_id,
                    must_change_password=True)
    db.add(new_user)

    db.commit()
    db.refresh(emp)
    return EmployeeCreateResponse(**EmployeeOut.model_validate(emp).model_dump(),
                                  temporary_password=temp_password)


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
def delete_employee(employee_id: str,
                     termination_date: date | None = Query(
                         None, description="חובה כשלעובד יש מענקים: תאריך סיום ההעסקה בפועל."),
                     current_user: User = Depends(require_roles(UserRole.COMPANY_ADMIN)),
                     db: Session = Depends(get_db)):
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
        # תאריך סיום ההעסקה הוא עובדת HR, לא רגע הלחיצה. הוא מזין את דדליין
        # חלון המימוש (termination_date + window_days) ואת נקודת העצירה של
        # ההבשלה, ולכן כל שעון - גם שעון עסקי מדויק - היה קובע כאן זכות כספית
        # לפי מתי אדמין הספיק להיכנס למערכת. עזיבה מדווחת כמעט תמיד בדיעבד.
        # השרת דורש את התאריך ואינו מנחש: 400 עדיף על ערך סביר-אך-שגוי שנרשם
        # ב-ledger_events, שהיא טבלה append-only שאין בה UPDATE.
        if termination_date is None:
            raise HTTPException(
                status_code=400,
                detail=("Employee has grants, so this is a termination, not a delete. "
                        "Pass the actual last day of employment as ?termination_date=YYYY-MM-DD "
                        "- it sets the post-termination exercise deadline and stops vesting."),
            )
        validation_error = _validate_termination_date(termination_date, emp.hire_date, business_today())
        if validation_error is not None:
            raise HTTPException(status_code=400, detail=validation_error)

        before_status = emp.status
        emp.status = EmployeeStatus.TERMINATED
        emp.termination_date = termination_date
        append_event(db, event_type="EMPLOYEE_STATUS_CHANGED", aggregate_type="Employee",
                    aggregate_id=employee_id,
                    payload={"status": "TERMINATED", "termination_date": emp.termination_date},
                    effective_date=emp.termination_date, actor_user_id=current_user.user_id)
        record_audit_event(db, "Employee", employee_id, "SOFT_DELETE_TERMINATE", current_user.user_id,
                            before={"status": before_status}, after={"status": "TERMINATED", "termination_date": emp.termination_date})
        db.commit()
        return {"employee_id": employee_id, "deleted": "soft", "new_status": "TERMINATED"}


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
    returned_options = 0.0

    if payload.status == EmployeeStatus.TERMINATED:
        # ההחזרה לפול מותרת רק במעבר *אמיתי* למצב עזיבה. קודם אותה קריאה פעמיים
        # (או מעבר TERMINATED -> TERMINATED עם תאריך אחר) הזרימה את האופציות שלא
        # הבשילו לפול שוב ושוב, וכל הרצה כזו הזיזה את יתרות הפול עוד צעד מהמענקים.
        already_terminated = before_status in (EmployeeStatus.TERMINATED, EmployeeStatus.DECEASED)

        validation_error = _validate_termination_date(payload.effective_date, employee.hire_date, business_today())
        if validation_error is not None:
            raise HTTPException(status_code=400, detail=validation_error)

        if payload.return_unvested_to_pool and not already_terminated:
            for grant in employee.grants:
                try:
                    vested = DeterministicESOPEngine.calculate_vested_options(
                        grant, grant.vesting_schedule, payload.effective_date)
                except MissingVestingScheduleError:
                    # חוסם את כל הפעולה: בלי לוח הבשלה אין דרך לדעת מה הבשיל, וניחוש
                    # כאן מזיז כספית גם את הפול וגם את זכויות העובד.
                    raise HTTPException(
                        status_code=409,
                        detail=(f"Grant {grant.grant_id} has no vesting schedule - cannot determine "
                                "how many options to return to the pool. Attach a schedule first."),
                    )
                unvested = grant.total_options - vested
                returned_options += unvested

                pool = db.query(OptionPool).filter(OptionPool.pool_id == grant.pool_id).first()
                if pool:
                    # allocated_shares מייצג את מה שמוקצה *בפועל* (outstanding), ולא את
                    # סך מה שהוענק היסטורית - לכן הוא קטן מסך total_options של המענקים
                    # אחרי עזיבה, וזה נכון ולא דריפט.
                    pool.unallocated_shares += unvested
                    pool.allocated_shares -= unvested
                    append_event(db, event_type="POOL_UNVEST_RETURNED", aggregate_type="OptionPool",
                                aggregate_id=pool.pool_id,
                                payload={"amount": unvested, "grant_id": grant.grant_id,
                                        "reason": "employee_terminated"},
                                effective_date=payload.effective_date, actor_user_id=current_user.user_id)

        employee.termination_date = payload.effective_date

    employee.status = payload.status

    # v0.6.0: אירוע לכל שינוי סטטוס, לא רק עזיבה - הקוד עצמו מבצע השמה גנרית.
    append_event(db, event_type="EMPLOYEE_STATUS_CHANGED", aggregate_type="Employee",
                aggregate_id=employee_id,
                payload={"status": employee.status.value if hasattr(employee.status, "value") else employee.status,
                        "termination_date": employee.termination_date},
                effective_date=payload.effective_date, actor_user_id=current_user.user_id)

    record_audit_event(db, "Employee", employee_id, "STATUS_CHANGE", current_user.user_id,
                        before={"status": before_status.value if hasattr(before_status, "value") else before_status},
                        after={"status": employee.status, "effective_date": payload.effective_date,
                               "returned_options": returned_options})
    db.commit()
    return {"employee_id": employee_id, "status": employee.status, "returned_options": returned_options}
