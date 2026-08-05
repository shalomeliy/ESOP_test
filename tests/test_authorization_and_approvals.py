"""גבולות הרשאה וולידציות אישור, ברמת ה-endpoint.

הבדיקות כאן מכסות את הבאגים שתוקנו ב-``routes.py`` ולא ניתן לכסות אותן ברמת
המנוע, כי הכשל היה *היעדר בדיקה בשכבת ה-API* ולא חישוב שגוי: דליפה בין חברות,
IDOR על דשבורד וסימולציה, ואישור בקשת מימוש שחורג מהמוּבשל / מהמאושר / מחסימת
הנאמן. מיפוי למזהי הבדיקה ב-``QA_TESTBOOK.md``: QA-050-20 עד QA-050-38.
"""

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from backend.app.auth import hash_password
from backend.app.models import (
    Company, Employee, EmployeeStatus, ExerciseRequest, ExerciseRequestStatus,
    Grant, GrantType, OptionPool, Trustee, User, UserRole, UserSession,
    VestingSchedule,
)

API = "/api/v1"

# 4800 אופציות ב-48 חודשים = 100 לחודש. תחילת הבשלה שנתיים לפני "היום" של הבדיקה
# מחושבת מ-date.today() כי ה-endpoints קוראים את התאריך בעצמם.
TODAY = date.today()
PER_MONTH = 100.0


def _months_ago(months: int) -> date:
    """אותו יום בחודש, N חודשים אחורה - בלי להתרסק על יום שאינו קיים."""
    total = TODAY.month - 1 - months
    year, month = TODAY.year + total // 12, total % 12 + 1
    day = min(TODAY.day, 28)
    return date(year, month, day)


def _token(db, user: User) -> dict:
    """יוצר session ישירות ולא דרך /auth/login - הבדיקות כאן על ההרשאות,
    לא על זרימת ההתחברות (שמכוסה בנפרד)."""
    token = f"test-token-{user.user_id}"
    db.add(UserSession(token=token, user_id=user.user_id,
                       expires_at=datetime.utcnow() + timedelta(hours=1)))
    db.flush()
    return {"Authorization": f"Bearer {token}"}


def _user(db, user_id: str, role: UserRole, **ids) -> User:
    pw_hash, salt = hash_password("Demo1234!")
    u = User(user_id=user_id, username=f"{user_id.lower()}@test.example",
             password_hash=pw_hash, password_salt=salt, role=role, is_active=True, **ids)
    db.add(u)
    db.flush()
    return u


@pytest.fixture
def world(db_session):
    """שתי חברות, שני עובדים ב-COMP-A, עובד אחד ב-COMP-B, ונאמן.

    למענק של EMP-A1 חלפו 13 חודשי הבשלה (cliff=12) => 1300 הבשילו מתוך 4800.
    זה בדיוק היחס של באג #17 המקורי (vested=1300, בקשה על 4000).
    """
    db = db_session
    db.add_all([
        Company(company_id="COMP-A", name="Alpha", country_code="IL"),
        Company(company_id="COMP-B", name="Beta", country_code="IL"),
    ])
    db.add_all([
        OptionPool(pool_id="POOL-A", company_id="COMP-A", total_shares=100000.0,
                   allocated_shares=4800.0, unallocated_shares=95200.0),
        OptionPool(pool_id="POOL-B", company_id="COMP-B", total_shares=50000.0,
                   allocated_shares=0.0, unallocated_shares=50000.0),
    ])
    db.add(Trustee(trustee_id="TRUST-1", company_id="COMP-A", name="Trustee Ltd",
                   registration_number="123456"))
    db.add_all([
        Employee(employee_id="EMP-A1", company_id="COMP-A", first_name="Yossi",
                 last_name="Cohen", email="a1@alpha.example", country_code="IL",
                 status=EmployeeStatus.ACTIVE, hire_date=date(2020, 1, 1),
                 birth_date=date(1990, 5, 20)),
        Employee(employee_id="EMP-A2", company_id="COMP-A", first_name="Dana",
                 last_name="Katz", email="a2@alpha.example", country_code="IL",
                 status=EmployeeStatus.ACTIVE, hire_date=date(2020, 1, 1),
                 birth_date=date(1992, 3, 10)),
        Employee(employee_id="EMP-B1", company_id="COMP-B", first_name="Rivka",
                 last_name="Levi", email="b1@beta.example", country_code="IL",
                 status=EmployeeStatus.ACTIVE, hire_date=date(2020, 1, 1),
                 birth_date=date(1988, 8, 8)),
        # קטין - לבדיקת חסימת הענקה (באגים #3/#4)
        Employee(employee_id="EMP-MINOR", company_id="COMP-A", first_name="Noam",
                 last_name="Small", email="minor@alpha.example", country_code="IL",
                 status=EmployeeStatus.ACTIVE, hire_date=TODAY,
                 birth_date=_months_ago(15 * 12)),
        # בלי birth_date - "לא בדקנו" אינו "עבר את הבדיקה"
        Employee(employee_id="EMP-NOBIRTH", company_id="COMP-A", first_name="Unknown",
                 last_name="Age", email="nobirth@alpha.example", country_code="IL",
                 status=EmployeeStatus.ACTIVE, hire_date=TODAY, birth_date=None),
    ])
    db.add_all([
        Grant(grant_id="GRANT-A1", employee_id="EMP-A1", pool_id="POOL-A",
              grant_date=_months_ago(13), grant_type=GrantType.IL_102_CAPITAL_GAINS,
              total_options=4800.0, exercise_price=1.0, post_termination_window_days=90),
        Grant(grant_id="GRANT-A2", employee_id="EMP-A2", pool_id="POOL-A",
              grant_date=_months_ago(13), grant_type=GrantType.IL_102_CAPITAL_GAINS,
              total_options=4800.0, exercise_price=1.0, post_termination_window_days=90),
        # מענק בנאמנות שהופקד לפני 3 חודשים בלבד => חסימה לא הסתיימה (באג #19)
        Grant(grant_id="GRANT-HELD", employee_id="EMP-A2", pool_id="POOL-A",
              trustee_id="TRUST-1", grant_date=_months_ago(13),
              grant_type=GrantType.IL_102_CAPITAL_GAINS, total_options=4800.0,
              exercise_price=1.0, post_termination_window_days=90,
              trustee_deposit_date=_months_ago(3)),
        # מענק בלי VestingSchedule (באג #12) - נבדק דרך הדשבורד
        Grant(grant_id="GRANT-NOSCHED", employee_id="EMP-A1", pool_id="POOL-A",
              grant_date=date(2015, 1, 1), grant_type=GrantType.IL_102_CAPITAL_GAINS,
              total_options=5000.0, exercise_price=1.0, post_termination_window_days=90),
    ])
    for grant_id in ("GRANT-A1", "GRANT-A2", "GRANT-HELD"):
        db.add(VestingSchedule(schedule_id=f"SCHED-{grant_id}", grant_id=grant_id,
                               start_date=_months_ago(13), cliff_months=12,
                               total_months=48, paused_days_total=0))
    db.flush()

    admin_a = _user(db, "USER-ADMIN-A", UserRole.COMPANY_ADMIN, company_id="COMP-A")
    emp_a1 = _user(db, "USER-EMP-A1", UserRole.EMPLOYEE, employee_id="EMP-A1")
    emp_a2 = _user(db, "USER-EMP-A2", UserRole.EMPLOYEE, employee_id="EMP-A2")
    trustee = _user(db, "USER-TRUSTEE", UserRole.TRUSTEE, trustee_id="TRUST-1")

    return SimpleNamespace(
        db=db,
        admin_a=_token(db, admin_a), emp_a1=_token(db, emp_a1),
        emp_a2=_token(db, emp_a2), trustee=_token(db, trustee),
    )


# ===================================================================
# באג #1 - דליפת עובדים בין חברות
# ===================================================================

def test_admin_employees_returns_only_own_company(client, world):
    response = client.get(f"{API}/admin/employees", headers=world.admin_a)

    assert response.status_code == 200
    company_ids = {e["company_id"] for e in response.json()}
    assert company_ids == {"COMP-A"}, f"דליפה בין חברות: {company_ids}"
    assert "EMP-B1" not in {e["employee_id"] for e in response.json()}


# ===================================================================
# באג #2 - IDOR על דשבורד העובד, ובנוסף על הסימולציה
# ===================================================================

def test_employee_cannot_read_another_employees_dashboard(client, world):
    response = client.get(f"{API}/employee/dashboard/EMP-A2", headers=world.emp_a1)
    assert response.status_code == 403


def test_employee_can_read_own_dashboard(client, world):
    response = client.get(f"{API}/employee/dashboard/EMP-A1", headers=world.emp_a1)

    assert response.status_code == 200
    grants = {g["grant_id"]: g for g in response.json()["grants"]}
    assert grants["GRANT-A1"]["vested_options"] == 13 * PER_MONTH
    assert grants["GRANT-A1"]["vesting_data_missing"] is False


def test_dashboard_marks_missing_vesting_schedule_instead_of_reporting_zero(client, world):
    """באג #12 בשכבת ה-API: המענק מ-2015 בלי לוח הבשלה מסומן במפורש כנתון
    חסר, ולא מוצג כ-0 הבשילו."""
    response = client.get(f"{API}/employee/dashboard/EMP-A1", headers=world.emp_a1)

    no_sched = next(g for g in response.json()["grants"] if g["grant_id"] == "GRANT-NOSCHED")
    assert no_sched["vesting_data_missing"] is True
    assert no_sched["vested_options"] is None


def test_employee_cannot_simulate_exercise_on_someone_elses_grant(client, world):
    """לא היה במפת הבאגים: הסימולציה חשפה מחיר מימוש, שווי וסכום מס של מענק זר."""
    response = client.post(f"{API}/employee/simulate-exercise", headers=world.emp_a1,
                           json={"grant_id": "GRANT-A2", "options_to_exercise": 10,
                                 "exercise_date": str(TODAY)})
    assert response.status_code == 403


# ===================================================================
# באג #17 - אישור מעל ה-vested
# ===================================================================

def test_approving_more_than_vested_is_rejected(client, world):
    """vested=1300 (13 חודשים × 100), בקשה על 4000."""
    world.db.add(ExerciseRequest(request_id="REQ-OVER", grant_id="GRANT-A1",
                                 employee_id="EMP-A1", options_requested=4000.0,
                                 status=ExerciseRequestStatus.PENDING,
                                 requested_at=datetime.utcnow()))
    world.db.flush()

    response = client.patch(f"{API}/admin/exercise-requests/REQ-OVER",
                            headers=world.admin_a, json={"approve": True})

    assert response.status_code == 400
    assert "vested" in response.json()["detail"].lower()


def test_rejecting_an_over_vested_request_is_still_allowed(client, world):
    """בקרה: החסימה היא על *אישור* בלבד. דחייה חייבת להישאר אפשרית, אחרת
    בקשה שגויה נתקעת PENDING לנצח."""
    world.db.add(ExerciseRequest(request_id="REQ-OVER-2", grant_id="GRANT-A1",
                                 employee_id="EMP-A1", options_requested=4000.0,
                                 status=ExerciseRequestStatus.PENDING,
                                 requested_at=datetime.utcnow()))
    world.db.flush()

    response = client.patch(f"{API}/admin/exercise-requests/REQ-OVER-2",
                            headers=world.admin_a, json={"approve": False})

    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"


# ===================================================================
# באג #18 - שתי בקשות חופפות
# ===================================================================

def test_second_overlapping_request_is_blocked_at_submission(client, world):
    """vested=1300. בקשה ראשונה על 1000 עוברת; שנייה על 1000 נחסמת כבר בהגשה,
    כי 1000 כבר תפוסות ונשארו 300."""
    first = client.post(f"{API}/employee/exercise-requests", headers=world.emp_a1,
                        json={"grant_id": "GRANT-A1", "options_to_exercise": 1000})
    assert first.status_code == 200

    second = client.post(f"{API}/employee/exercise-requests", headers=world.emp_a1,
                         json={"grant_id": "GRANT-A1", "options_to_exercise": 1000})
    assert second.status_code == 400
    assert "available" in second.json()["detail"].lower()


def test_two_pending_requests_cannot_both_be_approved(client, world):
    """הגנת השכבה השנייה: גם אם שתי בקשות PENDING כבר קיימות ב-DB (דאטה
    היסטורי מלפני התיקון), אישור שתיהן יחד חורג מה-vested ולכן השנייה נחסמת."""
    for suffix, amount in (("A", 1000.0), ("B", 1000.0)):
        world.db.add(ExerciseRequest(request_id=f"REQ-DUP-{suffix}", grant_id="GRANT-A1",
                                     employee_id="EMP-A1", options_requested=amount,
                                     status=ExerciseRequestStatus.PENDING,
                                     requested_at=datetime.utcnow()))
    world.db.flush()

    first = client.patch(f"{API}/admin/exercise-requests/REQ-DUP-A",
                         headers=world.admin_a, json={"approve": True})
    assert first.status_code == 200

    second = client.patch(f"{API}/admin/exercise-requests/REQ-DUP-B",
                          headers=world.admin_a, json={"approve": True})
    assert second.status_code == 400
    assert "already approved" in second.json()["detail"].lower()


# ===================================================================
# באג #19 - אישור לפני תום חסימת הנאמן
# ===================================================================

def test_approving_before_trustee_holding_period_is_blocked(client, world):
    """הפקדה לפני 3 חודשים, חסימה של 24 - הבקשה בתוך ה-vested אבל אסורה."""
    world.db.add(ExerciseRequest(request_id="REQ-EARLY", grant_id="GRANT-HELD",
                                 employee_id="EMP-A2", options_requested=500.0,
                                 status=ExerciseRequestStatus.PENDING,
                                 requested_at=datetime.utcnow()))
    world.db.flush()

    response = client.patch(f"{API}/admin/exercise-requests/REQ-EARLY",
                            headers=world.admin_a, json={"approve": True})

    assert response.status_code == 400
    assert "holding period" in response.json()["detail"].lower()


def test_trustee_approval_path_enforces_the_same_rules(client, world):
    """נתיב הנאמן היה פרוץ בדיוק כמו נתיב ה-admin - וזה החמור מהשניים, כי
    הנאמן הוא הצד שאמור לאכוף את תנאי סעיף 102."""
    world.db.add(ExerciseRequest(request_id="REQ-EARLY-T", grant_id="GRANT-HELD",
                                 employee_id="EMP-A2", options_requested=500.0,
                                 status=ExerciseRequestStatus.PENDING,
                                 requested_at=datetime.utcnow()))
    world.db.flush()

    response = client.patch(f"{API}/trustee/exercise-requests/REQ-EARLY-T",
                            headers=world.trustee, json={"approve": True})

    assert response.status_code == 400
    assert "holding period" in response.json()["detail"].lower()


def test_a_reviewed_request_cannot_be_reviewed_again(client, world):
    """לא היה במפה: אישור חוזר של בקשה שכבר טופלה דרס את הרשומה בשקט."""
    world.db.add(ExerciseRequest(request_id="REQ-DONE", grant_id="GRANT-A1",
                                 employee_id="EMP-A1", options_requested=100.0,
                                 status=ExerciseRequestStatus.APPROVED,
                                 requested_at=datetime.utcnow()))
    world.db.flush()

    response = client.patch(f"{API}/admin/exercise-requests/REQ-DONE",
                            headers=world.admin_a, json={"approve": True})
    assert response.status_code == 409


# ===================================================================
# באגים #3/#4 - הענקה לקטין
# ===================================================================

def test_grant_to_a_minor_is_rejected(client, world):
    response = client.post(f"{API}/admin/grants", headers=world.admin_a, json={
        "employee_id": "EMP-MINOR", "pool_id": "POOL-A",
        "grant_type": "IL_102_CAPITAL_GAINS", "total_options": 100.0,
        "exercise_price": 1.0, "grant_date": str(TODAY),
    })

    assert response.status_code == 400
    assert "under 18" in response.json()["detail"]


def test_grant_without_known_birth_date_is_rejected(client, world):
    response = client.post(f"{API}/admin/grants", headers=world.admin_a, json={
        "employee_id": "EMP-NOBIRTH", "pool_id": "POOL-A",
        "grant_type": "IL_102_CAPITAL_GAINS", "total_options": 100.0,
        "exercise_price": 1.0, "grant_date": str(TODAY),
    })

    assert response.status_code == 400
    assert "birth_date" in response.json()["detail"]


def test_grant_to_an_adult_still_succeeds(client, world):
    """בקרה חיובית: בדיקת הגיל לא חוסמת את הזרימה התקינה."""
    response = client.post(f"{API}/admin/grants", headers=world.admin_a, json={
        "employee_id": "EMP-A1", "pool_id": "POOL-A",
        "grant_type": "IL_102_CAPITAL_GAINS", "total_options": 100.0,
        "exercise_price": 1.0, "grant_date": str(TODAY),
    })

    assert response.status_code == 200, response.text
    assert response.json()["total_options"] == 100.0
