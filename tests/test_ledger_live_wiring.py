"""v0.6.0 שלב 2 - חיווט חמש נקודות המוטציה + איחוד אישור הבקשות + תיקון backdating.

הבדיקות כאן עוברות דרך ה-API האמיתי (client fixture), לא קוראות ל-append_event
ישירות - כדי להוכיח שהחיווט בפועל ב-routes.py עובד, לא רק שהשירות עצמו עובד
(זה כבר הוכח ב-test_ledger_replay.py). מיפוי ל-QA_TESTBOOK.md: QA-060-20 עד QA-060-31.
"""

from datetime import date, datetime, timedelta

import pytest

from backend.app.types import utcnow
from backend.app.auth import hash_password
from backend.app.models import (
    Company, Employee, EmployeeStatus, ExerciseRequest, ExerciseRequestStatus,
    Grant, GrantType, LedgerEvent, LedgerOwnership, OptionPool, Trustee, User,
    UserRole, UserSession, VestingSchedule,
)
from backend.app.services.ledger import LEDGER_EPOCH, append_event, project, record_ownership

API = "/api/v1"
TODAY = date.today()


def _months_ago(months: int) -> date:
    total = TODAY.month - 1 - months
    return date(TODAY.year + total // 12, total % 12 + 1, min(TODAY.day, 28))


def _token(db, user: User) -> dict:
    token = f"tok-{user.user_id}"
    db.add(UserSession(token=token, user_id=user.user_id,
                       expires_at=utcnow() + timedelta(hours=1)))
    db.flush()
    return {"Authorization": f"Bearer {token}"}


def _user(db, user_id, role, **ids):
    pw_hash, salt = hash_password("Demo1234!")
    u = User(user_id=user_id, username=f"{user_id.lower()}@test.example",
             password_hash=pw_hash, password_salt=salt, role=role, is_active=True, **ids)
    db.add(u)
    db.flush()
    return u


@pytest.fixture
def world(db_session):
    """שם עדיין קיים ה"מוזר" הקטן שהחיווט חושף: פול ועובד שנוצרים ישירות
    (לא דרך create_grant/create_employee) לא מקבלים אירוע בסיס אוטומטית - בדיוק
    כמו כל פול/עובד אמיתיים לפני שהגיבוי (backfill_ledger.py) רץ פעם ראשונה
    (ראו R-060-05/06 ב-QA_TESTBOOK.md). לכן ה-fixture בונה אותם ידנית כאן -
    זה בדיוק מה שה-backfill היה עושה, ומדמה מערכת שכבר עברה גיבוי."""
    db = db_session
    db.add(Company(company_id="C-A", name="Alpha", country_code="IL"))
    db.flush()
    db.add(OptionPool(pool_id="P-A", company_id="C-A", total_shares=100000.0,
                      allocated_shares=0.0, unallocated_shares=100000.0))
    db.add(Trustee(trustee_id="T-1", company_id="C-A", name="Trustee Ltd",
                   registration_number="1"))
    db.add(Employee(employee_id="E-1", company_id="C-A", first_name="Yossi", last_name="Cohen",
                    email="e1@alpha.example", country_code="IL", status=EmployeeStatus.ACTIVE,
                    hire_date=date(2020, 1, 1), birth_date=date(1990, 1, 1)))
    db.flush()

    record_ownership(db, aggregate_id="P-A", aggregate_type="OptionPool", company_id="C-A")
    append_event(db, event_type="POOL_BALANCE_ESTABLISHED", aggregate_type="OptionPool",
                aggregate_id="P-A",
                payload={"allocated_shares": 0.0, "unallocated_shares": 100000.0, "total_shares": 100000.0},
                effective_date=LEDGER_EPOCH)
    record_ownership(db, aggregate_id="E-1", aggregate_type="Employee", company_id="C-A", employee_id="E-1")
    append_event(db, event_type="EMPLOYEE_STATE_ESTABLISHED", aggregate_type="Employee",
                aggregate_id="E-1", payload={"status": "ACTIVE", "termination_date": None},
                effective_date=date(2020, 1, 1))

    admin = _user(db, "U-ADMIN", UserRole.COMPANY_ADMIN, company_id="C-A")
    trustee_user = _user(db, "U-TRUSTEE", UserRole.TRUSTEE, trustee_id="T-1")
    emp_user = _user(db, "U-EMP", UserRole.EMPLOYEE, employee_id="E-1")
    from types import SimpleNamespace
    return SimpleNamespace(db=db, admin=_token(db, admin), trustee=_token(db, trustee_user),
                           emp=_token(db, emp_user))


# ===================================================================
# QA-060-20: create_grant מייצר שלושה אירועי בסיס + בעלות נכונה
# ===================================================================

def test_create_grant_appends_baseline_events_and_ownership(client, world):
    response = client.post(f"{API}/admin/grants", headers=world.admin, json={
        "employee_id": "E-1", "pool_id": "P-A", "trustee_id": "T-1",
        "grant_type": "IL_102_CAPITAL_GAINS", "total_options": 4800.0,
        "exercise_price": 1.0, "grant_date": str(_months_ago(13)),
        "cliff_months": 12, "total_months": 48,
    })
    assert response.status_code == 200, response.text
    grant_id = response.json()["grant_id"]
    schedule_id = response.json()["vesting_schedule_id"]

    grant_events = world.db.query(LedgerEvent).filter(
        LedgerEvent.aggregate_id == grant_id).all()
    assert [e.event_type for e in grant_events] == ["GRANT_CREATED"]

    pool_events = world.db.query(LedgerEvent).filter(
        LedgerEvent.aggregate_id == "P-A").all()
    # ה-world fixture כבר זרעה POOL_BALANCE_ESTABLISHED (מדמה גיבוי) - כאן בודקים
    # רק שנוסף אחריו בדיוק דלתא אחת מהמענק החדש.
    assert [e.event_type for e in pool_events] == ["POOL_BALANCE_ESTABLISHED", "POOL_ALLOCATED"]

    schedule_events = world.db.query(LedgerEvent).filter(
        LedgerEvent.aggregate_id == schedule_id).all()
    assert [e.event_type for e in schedule_events] == ["VESTING_SCHEDULE_ESTABLISHED"]

    grant_ownership = world.db.get(LedgerOwnership, grant_id)
    assert (grant_ownership.company_id, grant_ownership.trustee_id, grant_ownership.employee_id) \
        == ("C-A", "T-1", "E-1")


def test_pool_projection_still_correct_when_grant_predates_pool_row_creation(client, world):
    """רגרסיה על באג אמיתי שנתפס בזמן אימות ידני מול עותק של הדאטה החי (לא
    בבדיקה, ולא היפותטי): הבסיס של פול נבנה במקור עם effective_date=
    pool.created_at.date() - זמן יצירת השורה ב-DB, לא עובדה היסטורית. מענק חי
    עם grant_date ישן ממנו (המצב הנפוץ - הפול נוצר ב-seed, המענקים תוארכו
    לאחור) "הקדים" את הבסיס בקיפול, וה-POOL_ALLOCATED שלו התעלם בשקט כי
    ה-state עדיין None באותה נקודה במיון. עכשיו הבסיס נכתב עם LEDGER_EPOCH
    (תאריך מוקדם ביותר), ולכן תמיד ראשון - ראו ledger.LEDGER_EPOCH.
    """
    old_grant_date = date(2015, 1, 1)  # ודאי לפני כל pool.created_at אפשרי
    response = client.post(f"{API}/admin/grants", headers=world.admin, json={
        "employee_id": "E-1", "pool_id": "P-A", "grant_type": "IL_102_CAPITAL_GAINS",
        "total_options": 777.0, "exercise_price": 1.0, "grant_date": str(old_grant_date),
    })
    assert response.status_code == 200, response.text

    pool = world.db.get(OptionPool, "P-A")
    assert pool.allocated_shares == 777.0
    assert project(world.db, "OptionPool", "P-A") == {
        "allocated_shares": pool.allocated_shares,
        "unallocated_shares": pool.unallocated_shares,
        "total_shares": pool.total_shares,
    }


def test_create_grant_projection_matches_pool_after_two_grants(client, world):
    """QA-060-21: פרויקציית הפול אחרי שני מענקים תואמת את העמודה בפועל - לא רק
    שהאירוע נוצר, אלא שהקיפול שלו נותן את התוצאה הנכונה."""
    for amount in (1000.0, 2000.0):
        r = client.post(f"{API}/admin/grants", headers=world.admin, json={
            "employee_id": "E-1", "pool_id": "P-A", "grant_type": "IL_102_CAPITAL_GAINS",
            "total_options": amount, "exercise_price": 1.0, "grant_date": str(_months_ago(13)),
        })
        assert r.status_code == 200, r.text

    pool = world.db.get(OptionPool, "P-A")
    projected = project(world.db, "OptionPool", "P-A")
    assert projected == {"allocated_shares": pool.allocated_shares,
                         "unallocated_shares": pool.unallocated_shares,
                         "total_shares": pool.total_shares}
    assert pool.allocated_shares == 3000.0


# ===================================================================
# QA-060-22/23: עזיבה - שני הנתיבים (legacy status endpoint + soft-delete)
# ===================================================================

def test_employee_termination_appends_status_event_and_pool_return_events(client, world):
    grant_resp = client.post(f"{API}/admin/grants", headers=world.admin, json={
        "employee_id": "E-1", "pool_id": "P-A", "grant_type": "IL_102_CAPITAL_GAINS",
        "total_options": 4800.0, "exercise_price": 1.0, "grant_date": str(_months_ago(13)),
        "cliff_months": 12, "total_months": 48,
    })
    assert grant_resp.status_code == 200, grant_resp.text

    term_resp = client.patch(f"{API}/admin/employees/E-1/status", headers=world.admin, json={
        "status": "TERMINATED", "effective_date": str(TODAY), "return_unvested_to_pool": True,
    })
    assert term_resp.status_code == 200, term_resp.text

    status_events = world.db.query(LedgerEvent).filter(
        LedgerEvent.aggregate_id == "E-1", LedgerEvent.event_type == "EMPLOYEE_STATUS_CHANGED").all()
    assert len(status_events) == 1

    pool_return_events = world.db.query(LedgerEvent).filter(
        LedgerEvent.aggregate_id == "P-A", LedgerEvent.event_type == "POOL_UNVEST_RETURNED").all()
    assert len(pool_return_events) == 1

    employee = world.db.get(Employee, "E-1")
    assert project(world.db, "Employee", "E-1") == {
        "status": employee.status.value if hasattr(employee.status, "value") else employee.status,
        "termination_date": employee.termination_date,
    }


def test_terminating_twice_does_not_duplicate_pool_return_events(client, world):
    """שומר על ההתנהגות שכבר תוקנה ב-v0.5.0 (אידמפוטנטיות) - עכשיו גם ברמת
    האירועים, לא רק ברמת העמודה."""
    client.post(f"{API}/admin/grants", headers=world.admin, json={
        "employee_id": "E-1", "pool_id": "P-A", "grant_type": "IL_102_CAPITAL_GAINS",
        "total_options": 4800.0, "exercise_price": 1.0, "grant_date": str(_months_ago(13)),
    })
    payload = {"status": "TERMINATED", "effective_date": str(TODAY), "return_unvested_to_pool": True}
    client.patch(f"{API}/admin/employees/E-1/status", headers=world.admin, json=payload)
    client.patch(f"{API}/admin/employees/E-1/status", headers=world.admin, json=payload)

    pool_return_events = world.db.query(LedgerEvent).filter(
        LedgerEvent.aggregate_id == "P-A", LedgerEvent.event_type == "POOL_UNVEST_RETURNED").all()
    assert len(pool_return_events) == 1, "עזיבה כפולה יצרה יותר מאירוע החזרה אחד"

    status_events = world.db.query(LedgerEvent).filter(
        LedgerEvent.aggregate_id == "E-1", LedgerEvent.event_type == "EMPLOYEE_STATUS_CHANGED").all()
    assert len(status_events) == 2, "כל קריאה עדיין אמורה לרשום שינוי סטטוס, גם אם זהה לקודם"


def test_soft_delete_appends_status_event_too(client, world):
    """נתיב המחיקה הרכה (delete_employee) לא עובר דרך update_employee_status,
    ולכן צריך את אירוע ה-EMPLOYEE_STATUS_CHANGED משלו."""
    client.post(f"{API}/admin/grants", headers=world.admin, json={
        "employee_id": "E-1", "pool_id": "P-A", "grant_type": "IL_102_CAPITAL_GAINS",
        "total_options": 100.0, "exercise_price": 1.0, "grant_date": str(_months_ago(13)),
    })
    response = client.delete(f"{API}/admin/employees/E-1", headers=world.admin)
    assert response.status_code == 200
    assert response.json()["deleted"] == "soft"

    status_events = world.db.query(LedgerEvent).filter(
        LedgerEvent.aggregate_id == "E-1", LedgerEvent.event_type == "EMPLOYEE_STATUS_CHANGED").all()
    assert len(status_events) == 1


# ===================================================================
# QA-060-24: create_employee מייצר אירוע בסיס
# ===================================================================

def test_create_employee_appends_baseline_event(client, world):
    response = client.post(f"{API}/admin/employees", headers=world.admin, json={
        "first_name": "New", "last_name": "Hire", "email": "new.hire@alpha.example",
        "country_code": "IL", "hire_date": str(TODAY),
    })
    assert response.status_code == 200, response.text
    employee_id = response.json()["employee_id"]

    events = world.db.query(LedgerEvent).filter(LedgerEvent.aggregate_id == employee_id).all()
    assert [e.event_type for e in events] == ["EMPLOYEE_STATE_ESTABLISHED"]
    assert project(world.db, "Employee", employee_id) == {"status": "ACTIVE", "termination_date": None}


# ===================================================================
# QA-060-25..27: אישור/דחיית בקשת מימוש - שני הנתיבים דרך אותה נקודת כתיבה
# ===================================================================

@pytest.fixture
def grant_with_vesting(client, world):
    r = client.post(f"{API}/admin/grants", headers=world.admin, json={
        "employee_id": "E-1", "pool_id": "P-A", "trustee_id": "T-1",
        "grant_type": "IL_102_CAPITAL_GAINS", "total_options": 4800.0,
        "exercise_price": 1.0, "grant_date": str(_months_ago(13)),
        "cliff_months": 12, "total_months": 48,
    })
    assert r.status_code == 200, r.text
    return r.json()["grant_id"]


@pytest.fixture
def matured_grant(client, world):
    """מענק נפרד (לא grant_with_vesting המשותף, כדי לא לשבור את הנחות התאריכים
    של בדיקות אחרות) עם הפקדת נאמן ישנה מספיק: המענק עצמו נוצר לפני 50 חודשים,
    ההפקדה 30 חודשים אחריו (עדיין אחרי תאריך המענק) - וגם עברו 24+ חודשים מאז
    ההפקדה, כדי שכלל חסימת הנאמן (סעיף 102) לא יחסום את בדיקות האישור, שאינו
    הנושא הנבדק כאן."""
    r = client.post(f"{API}/admin/grants", headers=world.admin, json={
        "employee_id": "E-1", "pool_id": "P-A", "trustee_id": "T-1",
        "grant_type": "IL_102_CAPITAL_GAINS", "total_options": 4800.0,
        "exercise_price": 1.0, "grant_date": str(_months_ago(50)),
        "cliff_months": 12, "total_months": 48,
    })
    assert r.status_code == 200, r.text
    grant_id = r.json()["grant_id"]

    r = client.patch(
        f"{API}/trustee/confirm-deposit/{grant_id}?deposit_date={_months_ago(30)}",
        headers=world.trustee)
    assert r.status_code == 200, r.text
    return grant_id


def test_create_exercise_request_appends_baseline_event(client, world, grant_with_vesting):
    response = client.post(f"{API}/employee/exercise-requests", headers=world.emp, json={
        "grant_id": grant_with_vesting, "options_to_exercise": 100.0,
    })
    assert response.status_code == 200, response.text
    request_id = response.json()["request_id"]

    events = world.db.query(LedgerEvent).filter(LedgerEvent.aggregate_id == request_id).all()
    assert [e.event_type for e in events] == ["EXERCISE_REQUEST_SUBMITTED"]
    assert project(world.db, "ExerciseRequest", request_id)["status"] == "PENDING"


def test_admin_approval_appends_decided_event(client, world, matured_grant):
    req_id = client.post(f"{API}/employee/exercise-requests", headers=world.emp, json={
        "grant_id": matured_grant, "options_to_exercise": 100.0,
    }).json()["request_id"]

    response = client.patch(f"{API}/admin/exercise-requests/{req_id}", headers=world.admin,
                            json={"approve": True, "notes": "ok"})
    assert response.status_code == 200, response.text

    events = world.db.query(LedgerEvent).filter(LedgerEvent.aggregate_id == req_id).all()
    assert [e.event_type for e in events] == ["EXERCISE_REQUEST_SUBMITTED", "EXERCISE_REQUEST_DECIDED"]
    assert project(world.db, "ExerciseRequest", req_id)["status"] == "APPROVED"


def test_trustee_rejection_appends_decided_event_via_the_same_shared_function(client, world, matured_grant):
    """הנאמן משתמש באותה _decide_exercise_request - לא לוגיקה כפולה. דחייה לא
    עוברת דרך _assert_request_approvable בכלל, ולכן לא הייתה חייבת matured_grant -
    אבל שימוש בו בכל זאת שומר את הבדיקה עקבית עם test_admin_approval."""
    req_id = client.post(f"{API}/employee/exercise-requests", headers=world.emp, json={
        "grant_id": matured_grant, "options_to_exercise": 50.0,
    }).json()["request_id"]

    response = client.patch(f"{API}/trustee/exercise-requests/{req_id}", headers=world.trustee,
                            json={"approve": False, "notes": "not yet"})
    assert response.status_code == 200, response.text

    events = world.db.query(LedgerEvent).filter(
        LedgerEvent.aggregate_id == req_id, LedgerEvent.event_type == "EXERCISE_REQUEST_DECIDED").all()
    assert len(events) == 1
    assert project(world.db, "ExerciseRequest", req_id)["status"] == "REJECTED"


# ===================================================================
# QA-060-28..30: הפקדת נאמן - האירוע + הכלל הקשיח על backdating
# ===================================================================

def test_confirm_deposit_appends_event(client, world, grant_with_vesting):
    deposit_on = _months_ago(3)
    response = client.patch(
        f"{API}/trustee/confirm-deposit/{grant_with_vesting}?deposit_date={deposit_on}",
        headers=world.trustee)
    assert response.status_code == 200, response.text

    events = world.db.query(LedgerEvent).filter(
        LedgerEvent.aggregate_id == grant_with_vesting,
        LedgerEvent.event_type == "TRUSTEE_DEPOSIT_CONFIRMED").all()
    assert len(events) == 1
    assert project(world.db, "Grant", grant_with_vesting) == {"trustee_deposit_date": deposit_on}


def test_confirm_deposit_before_grant_date_is_rejected(client, world, grant_with_vesting):
    """הכלל הקשיח שאושר לשילוב ב-v0.6.0: אי אפשר להפקיד לפני שהמענק בכלל נוצר."""
    before_grant = _months_ago(20)  # המענק נוצר לפני 13 חודשים
    response = client.patch(
        f"{API}/trustee/confirm-deposit/{grant_with_vesting}?deposit_date={before_grant}",
        headers=world.trustee)

    assert response.status_code == 400
    assert "cannot precede the grant date" in response.json()["detail"]

    events = world.db.query(LedgerEvent).filter(
        LedgerEvent.aggregate_id == grant_with_vesting,
        LedgerEvent.event_type == "TRUSTEE_DEPOSIT_CONFIRMED").all()
    assert events == [], "אירוע נכתב למרות שהבקשה נדחתה"


def test_confirm_deposit_on_the_grant_date_itself_is_allowed(client, world, grant_with_vesting):
    """גבול הבדיקה: השוואה היא >=, לא >."""
    grant = world.db.get(Grant, grant_with_vesting)
    response = client.patch(
        f"{API}/trustee/confirm-deposit/{grant_with_vesting}?deposit_date={grant.grant_date}",
        headers=world.trustee)
    assert response.status_code == 200, response.text
