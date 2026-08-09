"""v0.6.0 שלב 3 - שכבת השאילתה הבי-טמפורלית (ציר זמן + as-of), admin-only.

מיפוי ל-QA_TESTBOOK.md: QA-060-40 עד QA-060-49.
"""

from datetime import date, datetime, timedelta

import pytest

from backend.app.types import utcnow
from backend.app.auth import hash_password
from backend.app.models import (
    Company, Employee, EmployeeStatus, OptionPool, Trustee, User, UserRole,
    UserSession,
)
from backend.app.services.ledger import LEDGER_EPOCH, append_event, record_ownership

API = "/api/v1"
TODAY = date.today()


def _months_ago(months: int) -> date:
    total = TODAY.month - 1 - months
    return date(TODAY.year + total // 12, total % 12 + 1, min(TODAY.day, 28))


def _token(db, user):
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
    """שתי חברות, שני אדמינים - כדי לבדוק גם את מקרה החסימה (IDOR), לא רק את
    מקרה ההצלחה. הפול כבר "עבר גיבוי" (POOL_BALANCE_ESTABLISHED מ-LEDGER_EPOCH),
    בדיוק כמו ב-test_ledger_live_wiring.py."""
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

    record_ownership(db, aggregate_id="P-A", aggregate_type="OptionPool", company_id="C-A")
    append_event(db, event_type="POOL_BALANCE_ESTABLISHED", aggregate_type="OptionPool",
                aggregate_id="P-A",
                payload={"allocated_shares": 0.0, "unallocated_shares": 100000.0, "total_shares": 100000.0},
                effective_date=LEDGER_EPOCH)

    admin_a = _user(db, "U-ADMIN-A", UserRole.COMPANY_ADMIN, company_id="C-A")
    admin_b = _user(db, "U-ADMIN-B", UserRole.COMPANY_ADMIN, company_id="C-B")
    trustee_a = _user(db, "U-TRUSTEE-A", UserRole.TRUSTEE, trustee_id="T-1")
    employee_a = _user(db, "U-EMPLOYEE-A", UserRole.EMPLOYEE, employee_id="E-1")
    from types import SimpleNamespace
    return SimpleNamespace(db=db, admin_a=_token(db, admin_a), admin_b=_token(db, admin_b),
                           trustee_a=_token(db, trustee_a), employee_a=_token(db, employee_a))


@pytest.fixture
def grant_with_deposit(client, world):
    """מענק ישן מספיק שההפקדה עליו קרתה זמן קצר אחריו - בדיוק התרחיש
    שהוכיח את הבי-טמפורליות ב-test_ledger_replay.py, עכשיו דרך ה-API החי."""
    r = client.post(f"{API}/admin/grants", headers=world.admin_a, json={
        "employee_id": "E-1", "pool_id": "P-A", "trustee_id": "T-1",
        "grant_type": "IL_102_CAPITAL_GAINS", "total_options": 4800.0,
        "exercise_price": 1.0, "grant_date": str(_months_ago(30)),
        "cliff_months": 12, "total_months": 48,
    })
    assert r.status_code == 200, r.text
    grant_id = r.json()["grant_id"]

    r = client.patch(
        f"{API}/trustee/confirm-deposit/{grant_id}?deposit_date={_months_ago(29)}",
        headers=_token(world.db, _user(world.db, "U-TRUSTEE", UserRole.TRUSTEE, trustee_id="T-1")))
    assert r.status_code == 200, r.text
    return grant_id


# ===================================================================
# QA-060-40..42: ציר זמן (events)
# ===================================================================

def test_timeline_returns_events_in_order_with_parsed_payload(client, world, grant_with_deposit):
    response = client.get(f"{API}/admin/ledger/Grant/{grant_with_deposit}/events",
                          headers=world.admin_a)
    assert response.status_code == 200, response.text
    events = response.json()

    assert [e["event_type"] for e in events] == ["GRANT_CREATED", "TRUSTEE_DEPOSIT_CONFIRMED"]
    assert events[0]["source"] == "LIVE"
    # payload חייב להגיע כ-dict מפורש, לא כמחרוזת JSON גולמית שהלקוח צריך לפענח שוב.
    assert isinstance(events[1]["payload"], dict)
    assert events[1]["payload"]["deposit_date"] == str(_months_ago(29))


def test_timeline_rejects_unknown_aggregate_type(client, world, grant_with_deposit):
    response = client.get(f"{API}/admin/ledger/NotAThing/{grant_with_deposit}/events",
                          headers=world.admin_a)
    assert response.status_code == 400
    assert "Unsupported aggregate_type" in response.json()["detail"]


def test_timeline_blocks_cross_company_access(client, world, grant_with_deposit):
    """QA-060-42: זו בדיוק ההגנה שסקירת האבטחה דרשה - מסך חדש שמאשר גישה מול
    ledger_ownership, לא מול דאטה משוחזר. אדמין של חברה אחרת לא רואה כלום."""
    response = client.get(f"{API}/admin/ledger/Grant/{grant_with_deposit}/events",
                          headers=world.admin_b)
    assert response.status_code == 403


def test_timeline_for_unknown_aggregate_id_is_also_blocked_not_leaked_as_empty(client, world):
    """אין שורת ledger_ownership בכלל -> 403, לא 200 עם רשימה ריקה. אחרת אפשר
    להבחין בין 'קיים אבל לא שלי' ל'לא קיים' לפי קוד התשובה - וזה בעצמו דלף."""
    response = client.get(f"{API}/admin/ledger/Grant/does-not-exist/events",
                          headers=world.admin_a)
    assert response.status_code == 403


# ===================================================================
# QA-060-43..47: שאילתה בי-טמפורלית (as-of)
# ===================================================================

def test_as_of_with_no_params_returns_current_state(client, world, grant_with_deposit):
    response = client.get(f"{API}/admin/ledger/Grant/{grant_with_deposit}/as-of",
                          headers=world.admin_a)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"]["trustee_deposit_date"] == str(_months_ago(29))
    assert body["as_of_effective_date"] is None
    assert body["as_of_knowledge_date"] is None


def test_as_of_effective_date_before_deposit_shows_no_deposit_yet(client, world, grant_with_deposit):
    """QA-060-44, הדוגמה המחושבת ביד: 'מה נכון בעולם' יום לפני ההפקדה - עדיין
    None, למרות שההפקדה כן קיימת ברשומה 'עכשיו'."""
    cutoff = _months_ago(29) - timedelta(days=1)
    response = client.get(
        f"{API}/admin/ledger/Grant/{grant_with_deposit}/as-of?effective_date={cutoff}",
        headers=world.admin_a)
    assert response.status_code == 200, response.text
    assert response.json()["state"]["trustee_deposit_date"] is None


def test_as_of_effective_date_on_and_after_deposit_shows_it(client, world, grant_with_deposit):
    cutoff = _months_ago(29)
    response = client.get(
        f"{API}/admin/ledger/Grant/{grant_with_deposit}/as-of?effective_date={cutoff}",
        headers=world.admin_a)
    assert response.status_code == 200, response.text
    assert response.json()["state"]["trustee_deposit_date"] == str(cutoff)


def test_as_of_knowledge_date_before_backfill_or_creation_returns_no_data(client, world, grant_with_deposit):
    """QA-060-46: שאילתת ידיעה על רגע *לפני* שהמענק בכלל נוצר - None, לא
    מתחזה לידע שאין. זו אותה הדגמה כמו QA-060-10 (שלב 1), עכשיו חשופה ב-API חי."""
    long_before = utcnow() - timedelta(days=3650)
    # params= ולא שרשור ל-URL: isoformat() של חותמת aware מסתיים ב-"+00:00",
    # וה-"+" בתוך query string פירושו רווח. שרשור ידני היה מחזיר 422 על קלט תקין.
    response = client.get(
        f"{API}/admin/ledger/Grant/{grant_with_deposit}/as-of",
        params={"knowledge_date": long_before.isoformat()},
        headers=world.admin_a)
    assert response.status_code == 200, response.text
    assert response.json()["state"] is None


def test_as_of_rejects_unknown_aggregate_type(client, world, grant_with_deposit):
    response = client.get(f"{API}/admin/ledger/NotAThing/{grant_with_deposit}/as-of",
                          headers=world.admin_a)
    assert response.status_code == 400


def test_as_of_blocks_cross_company_access(client, world, grant_with_deposit):
    response = client.get(f"{API}/admin/ledger/Grant/{grant_with_deposit}/as-of",
                          headers=world.admin_b)
    assert response.status_code == 403


# ===================================================================
# תפקידים - שני מסכי v0.6.0 הם admin-only (דרך א'), לא רק בקוד אלא גם בפועל.
# ===================================================================

@pytest.mark.parametrize("role_header", ["trustee_a", "employee_a"])
def test_timeline_rejects_non_admin_roles(client, world, grant_with_deposit, role_header):
    response = client.get(f"{API}/admin/ledger/Grant/{grant_with_deposit}/events",
                          headers=getattr(world, role_header))
    assert response.status_code == 403


@pytest.mark.parametrize("role_header", ["trustee_a", "employee_a"])
def test_as_of_rejects_non_admin_roles(client, world, grant_with_deposit, role_header):
    response = client.get(f"{API}/admin/ledger/Grant/{grant_with_deposit}/as-of",
                          headers=getattr(world, role_header))
    assert response.status_code == 403


# ===================================================================
# aggregate_type חייב לתאום לסוג האמיתי שנשמר ב-ledger_ownership, לא רק
# ל-company_id - אחרת מזהה תקין של ישות אחת "מתחזה" לישות אחרת מאותה חברה.
# ===================================================================

def test_timeline_rejects_mismatched_aggregate_type_even_same_company(client, world, grant_with_deposit):
    """grant_with_deposit רשום ב-ledger_ownership עם aggregate_type='Grant'.
    בקשה לאותו aggregate_id תחת aggregate_type='Employee' (סוג תקין, חברה
    נכונה) חייבת להיחסם - לא לעבור בשקט מול הפרויקטור הלא נכון."""
    response = client.get(f"{API}/admin/ledger/Employee/{grant_with_deposit}/events",
                          headers=world.admin_a)
    assert response.status_code == 403


def test_as_of_rejects_mismatched_aggregate_type_even_same_company(client, world, grant_with_deposit):
    response = client.get(f"{API}/admin/ledger/Employee/{grant_with_deposit}/as-of",
                          headers=world.admin_a)
    assert response.status_code == 403
