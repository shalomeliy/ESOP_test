"""v0.6.0 שלב 4 (אחרון) - הקפאת הבשלה (חופשה ללא תשלום).

סוגר את הפער ב-VestingSchedule.paused_days_total שלא הייתה לו שום דרך להיכתב
לפני הגרסה הזו (פער תיעוד ידוע, לא הבשלה - ראה QA_TESTBOOK.md, החלטת התכנון).
מיפוי ל-QA_TESTBOOK.md: QA-060-60 עד QA-060-69.
"""

from datetime import date, datetime, timedelta

import pytest

from backend.app.auth import hash_password
from backend.app.models import (
    Company, Employee, EmployeeStatus, Grant, GrantType, LedgerEvent, OptionPool,
    Trustee, User, UserRole, UserSession, VestingSchedule,
)
from backend.app.services.engine import DeterministicESOPEngine
from backend.app.services.ledger import project

API = "/api/v1"
TODAY = date.today()


def _months_ago(months: int) -> date:
    total = TODAY.month - 1 - months
    return date(TODAY.year + total // 12, total % 12 + 1, min(TODAY.day, 28))


def _token(db, user):
    token = f"tok-{user.user_id}"
    db.add(UserSession(token=token, user_id=user.user_id,
                       expires_at=datetime.utcnow() + timedelta(hours=1)))
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
    db = db_session
    db.add_all([
        Company(company_id="C-A", name="Alpha", country_code="IL"),
        Company(company_id="C-B", name="Beta", country_code="IL"),
    ])
    db.flush()
    db.add(OptionPool(pool_id="P-A", company_id="C-A", total_shares=100000.0,
                      allocated_shares=0.0, unallocated_shares=100000.0))
    db.add(Trustee(trustee_id="T-1", company_id="C-A", name="Trustee Ltd", registration_number="1"))
    db.add(Employee(employee_id="E-1", company_id="C-A", first_name="Yossi", last_name="Cohen",
                    email="e1@alpha.example", country_code="IL", status=EmployeeStatus.ACTIVE,
                    hire_date=date(2020, 1, 1), birth_date=date(1990, 1, 1)))
    db.flush()

    admin_a = _user(db, "U-ADMIN-A", UserRole.COMPANY_ADMIN, company_id="C-A")
    admin_b = _user(db, "U-ADMIN-B", UserRole.COMPANY_ADMIN, company_id="C-B")
    trustee_a = _user(db, "U-TRUSTEE-A", UserRole.TRUSTEE, trustee_id="T-1")
    employee_a = _user(db, "U-EMPLOYEE-A", UserRole.EMPLOYEE, employee_id="E-1")
    from types import SimpleNamespace
    return SimpleNamespace(db=db, admin_a=_token(db, admin_a), admin_b=_token(db, admin_b),
                           trustee_a=_token(db, trustee_a), employee_a=_token(db, employee_a))


@pytest.fixture
def grant_id(client, world):
    r = client.post(f"{API}/admin/grants", headers=world.admin_a, json={
        "employee_id": "E-1", "pool_id": "P-A", "grant_type": "IL_102_CAPITAL_GAINS",
        "total_options": 4800.0, "exercise_price": 1.0, "grant_date": str(_months_ago(20)),
        "cliff_months": 12, "total_months": 48,
    })
    assert r.status_code == 200, r.text
    return r.json()["grant_id"]


# ===================================================================
# QA-060-60/61: התיעוד הבסיסי - אירוע + עדכון עמודה + פרויקציה
# ===================================================================

def test_recording_a_pause_appends_event_and_updates_column(client, world, grant_id):
    start, end = _months_ago(10), _months_ago(9)  # 30 יום בערך, לפי min(day,28)
    response = client.post(f"{API}/admin/grants/{grant_id}/vesting-pause", headers=world.admin_a,
                           json={"start_date": str(start), "end_date": str(end)})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["days_added"] == (end - start).days
    assert body["paused_days_total"] == (end - start).days

    schedule = world.db.query(VestingSchedule).filter(
        VestingSchedule.schedule_id == body["schedule_id"]).first()
    assert schedule.paused_days_total == body["paused_days_total"]

    events = world.db.query(LedgerEvent).filter(
        LedgerEvent.aggregate_id == schedule.schedule_id,
        LedgerEvent.event_type == "VESTING_PAUSE_RECORDED").all()
    assert len(events) == 1

    projected = project(world.db, "VestingSchedule", schedule.schedule_id)
    assert projected["paused_days_total"] == schedule.paused_days_total


def test_two_non_overlapping_pauses_accumulate(client, world, grant_id):
    r1 = client.post(f"{API}/admin/grants/{grant_id}/vesting-pause", headers=world.admin_a,
                     json={"start_date": str(_months_ago(15)), "end_date": str(_months_ago(14))})
    assert r1.status_code == 200, r1.text
    r2 = client.post(f"{API}/admin/grants/{grant_id}/vesting-pause", headers=world.admin_a,
                     json={"start_date": str(_months_ago(10)), "end_date": str(_months_ago(9))})
    assert r2.status_code == 200, r2.text

    assert r2.json()["paused_days_total"] == r1.json()["days_added"] + r2.json()["days_added"]


# ===================================================================
# QA-060-62..65: ולידציה
# ===================================================================

def test_end_date_before_start_date_is_rejected(client, world, grant_id):
    response = client.post(f"{API}/admin/grants/{grant_id}/vesting-pause", headers=world.admin_a,
                           json={"start_date": str(_months_ago(5)), "end_date": str(_months_ago(6))})
    assert response.status_code == 400
    assert "end_date must be after start_date" in response.json()["detail"]


def test_zero_length_pause_is_rejected(client, world, grant_id):
    same = str(_months_ago(5))
    response = client.post(f"{API}/admin/grants/{grant_id}/vesting-pause", headers=world.admin_a,
                           json={"start_date": same, "end_date": same})
    assert response.status_code == 400


def test_overlapping_pause_period_is_rejected(client, world, grant_id):
    client.post(f"{API}/admin/grants/{grant_id}/vesting-pause", headers=world.admin_a,
               json={"start_date": str(_months_ago(15)), "end_date": str(_months_ago(10))})

    response = client.post(f"{API}/admin/grants/{grant_id}/vesting-pause", headers=world.admin_a,
                           json={"start_date": str(_months_ago(12)), "end_date": str(_months_ago(8))})
    assert response.status_code == 400
    assert "Overlaps an existing pause period" in response.json()["detail"]

    # ולא נכתב אירוע נוסף - הכפילות נחסמה לפני הכתיבה, לא אחריה.
    schedule = world.db.query(VestingSchedule).filter(VestingSchedule.grant_id == grant_id).first()
    events = world.db.query(LedgerEvent).filter(
        LedgerEvent.aggregate_id == schedule.schedule_id,
        LedgerEvent.event_type == "VESTING_PAUSE_RECORDED").all()
    assert len(events) == 1


def test_adjacent_non_overlapping_pause_is_allowed(client, world, grant_id):
    """גבול הבדיקה: תקופה שנוגעת בקצה הקודמת (לא חופפת בפועל) מותרת."""
    end_of_first = _months_ago(10)
    r1 = client.post(f"{API}/admin/grants/{grant_id}/vesting-pause", headers=world.admin_a,
                     json={"start_date": str(_months_ago(15)), "end_date": str(end_of_first)})
    assert r1.status_code == 200, r1.text

    r2 = client.post(f"{API}/admin/grants/{grant_id}/vesting-pause", headers=world.admin_a,
                     json={"start_date": str(end_of_first), "end_date": str(_months_ago(5))})
    assert r2.status_code == 200, r2.text


def test_grant_without_vesting_schedule_returns_409(client, world):
    r = client.post(f"{API}/admin/grants", headers=world.admin_a, json={
        "employee_id": "E-1", "pool_id": "P-A", "grant_type": "IL_102_CAPITAL_GAINS",
        "total_options": 100.0, "exercise_price": 1.0, "grant_date": str(_months_ago(20)),
    })
    grant_no_schedule_id = r.json()["grant_id"]
    # מוחקים את לוח ההבשלה שנוצר אוטומטית, כדי לדמות את המצב שבו הוא חסר.
    sched = world.db.query(VestingSchedule).filter(
        VestingSchedule.grant_id == grant_no_schedule_id).first()
    world.db.delete(sched)
    world.db.flush()

    response = client.post(f"{API}/admin/grants/{grant_no_schedule_id}/vesting-pause",
                           headers=world.admin_a,
                           json={"start_date": str(_months_ago(5)), "end_date": str(_months_ago(4))})
    assert response.status_code == 409


# ===================================================================
# QA-060-66/67: הרשאות
# ===================================================================

def test_cross_company_grant_is_blocked(client, world, grant_id):
    response = client.post(f"{API}/admin/grants/{grant_id}/vesting-pause", headers=world.admin_b,
                           json={"start_date": str(_months_ago(5)), "end_date": str(_months_ago(4))})
    assert response.status_code == 403


@pytest.mark.parametrize("role_header", ["trustee_a", "employee_a"])
def test_non_admin_roles_are_rejected(client, world, grant_id, role_header):
    """הפיצ'ר admin-only בלבד - לא רק בקוד (require_roles), גם בפועל."""
    response = client.post(f"{API}/admin/grants/{grant_id}/vesting-pause",
                           headers=getattr(world, role_header),
                           json={"start_date": str(_months_ago(5)), "end_date": str(_months_ago(4))})
    assert response.status_code == 403


def test_unknown_grant_returns_404(client, world):
    response = client.post(f"{API}/admin/grants/does-not-exist/vesting-pause", headers=world.admin_a,
                           json={"start_date": str(_months_ago(5)), "end_date": str(_months_ago(4))})
    assert response.status_code == 404


# ===================================================================
# QA-060-68/69: החישוב בפועל משתנה - לא רק העמודה, גם התוצאה שהעובד רואה
# ===================================================================

def test_pause_actually_shifts_the_cliff_in_the_existing_engine(client, world, grant_id):
    """הקישור בין שלב 4 לחישוב שכבר קיים ומתועד (test_vesting_engine.py) -
    הבדיקה הזו לא בודקת מתמטיקה חדשה, רק שהעמודה שה-endpoint מעדכן היא בדיוק
    מה ש-DeterministicESOPEngine כבר קורא."""
    grant = world.db.query(Grant).filter(Grant.grant_id == grant_id).first()
    schedule = grant.vesting_schedule
    original_start = schedule.start_date

    check_date = original_start + timedelta(days=370)  # קצת אחרי cliff של 12 חודש מקורי
    vested_before = DeterministicESOPEngine.calculate_vested_options(grant, schedule, check_date)
    assert vested_before > 0.0, "לפני ההקפאה, ה-cliff המקורי כבר אמור היה לחלוף"

    client.post(f"{API}/admin/grants/{grant_id}/vesting-pause", headers=world.admin_a,
               json={"start_date": str(original_start), "end_date": str(original_start + timedelta(days=60))})
    world.db.refresh(schedule)

    vested_after = DeterministicESOPEngine.calculate_vested_options(grant, schedule, check_date)
    assert vested_after < vested_before, "הקפאה של 60 יום אמורה לדחות את ה-cliff ולהקטין את ההבשלה באותו תאריך"
