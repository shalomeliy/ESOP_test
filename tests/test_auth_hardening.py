"""v0.5.1 - patch אבטחה: CORS מוגבל, ביטול הסיסמה הקבועה, ניקוי session, נעילת חשבון.

מיפוי ל-QA_TESTBOOK.md: QA-051-01 עד QA-051-13.
"""

from datetime import date, datetime, timedelta

import pytest

from backend.app.types import utcnow
from backend.app.auth import (
    MAX_FAILED_LOGIN_ATTEMPTS, hash_password, is_account_locked,
)
from backend.app.main import ALLOWED_ORIGINS
from backend.app.models import (
    Company, Employee, EmployeeStatus, OptionPool, User, UserRole, UserSession,
)

API = "/api/v1"
DISALLOWED_ORIGIN = "https://evil.example"


@pytest.fixture
def company_admin(db_session):
    """אדמין חברה עם סיסמה ידועה - לא נזרע דרך /admin/employees, כדי שבדיקות
    ה-lockout לא יתערבבו עם must_change_password."""
    db_session.add(Company(company_id="C-SEC", name="SecCo", country_code="IL"))
    pw_hash, salt = hash_password("Adm1nPass!")
    user = User(user_id="U-SEC-ADMIN", username="admin@secco.demo",
                password_hash=pw_hash, password_salt=salt,
                role=UserRole.COMPANY_ADMIN, company_id="C-SEC")
    db_session.add(user)
    db_session.flush()
    return user


# ===================================================================
# QA-051-01/02 - CORS
# ===================================================================

def test_wildcard_origin_is_no_longer_allowed(client):
    """*** לא בודקים "*" ישירות - CORSMiddleware לא מחזיר Access-Control-Allow-Origin
    כאשר Origin הנשלח אינו ברשימה, גם אם הבקשה עצמה מצליחה (זה בדיוק ההבדל בין
    "השרת דחה" ל"הדפדפן יחסום את הקריאה בצד הלקוח" - CORS הוא מנגנון אכיפה
    בדפדפן, לא בשרת). לכן הבדיקה היא על היעדר ה-header, לא על קוד סטטוס. ***
    """
    response = client.get(f"{API}/version", headers={"Origin": DISALLOWED_ORIGIN})

    assert response.status_code == 200  # ה-endpoint עצמו לא נחסם
    assert "access-control-allow-origin" not in response.headers, (
        "מקור לא מורשה קיבל header של CORS - דפדפן היה חושף את התשובה לו"
    )


def test_allowed_origin_gets_the_cors_header_back(client):
    allowed = ALLOWED_ORIGINS[0]
    response = client.get(f"{API}/version", headers={"Origin": allowed})

    assert response.headers.get("access-control-allow-origin") == allowed


def test_preflight_for_disallowed_origin_is_rejected(client):
    response = client.options(f"{API}/auth/login", headers={
        "Origin": DISALLOWED_ORIGIN,
        "Access-Control-Request-Method": "POST",
    })

    assert "access-control-allow-origin" not in response.headers


def test_credentials_flag_is_off(client):
    """allow_credentials=False בכוונה: האימות כולו Bearer, אין עוגיות, אף fetch
    בפורטלים לא שולח credentials:'include'. הדגל לא היה עושה כלום מלבד להרחיב
    את משטח החשיפה - ולכן מוסר, לא רק "מתוקן להיות ספציפי"."""
    allowed = ALLOWED_ORIGINS[0]
    response = client.get(f"{API}/version", headers={"Origin": allowed})

    assert response.headers.get("access-control-allow-credentials") != "true"


# ===================================================================
# QA-051-03..06 - סיסמה חד-פעמית ו-must_change_password
# ===================================================================

def test_new_employee_gets_a_random_password_not_welcome123(client, company_admin):
    payload = {"first_name": "New", "last_name": "Hire", "email": "new.hire@secco.example",
              "country_code": "IL", "hire_date": str(date.today())}
    token = _login(client, "admin@secco.demo", "Adm1nPass!")["token"]

    response = client.post(f"{API}/admin/employees", headers=_auth(token), json=payload)

    assert response.status_code == 200
    body = response.json()
    assert "temporary_password" in body
    assert body["temporary_password"] != "Welcome123!"
    assert len(body["temporary_password"]) >= 12


def test_new_employee_account_is_blocked_from_business_endpoints_until_password_change(
        client, company_admin, db_session):
    payload = {"first_name": "New", "last_name": "Hire2", "email": "new.hire2@secco.example",
              "country_code": "IL", "hire_date": str(date.today())}
    admin_token = _login(client, "admin@secco.demo", "Adm1nPass!")["token"]
    created = client.post(f"{API}/admin/employees", headers=_auth(admin_token), json=payload).json()

    login = _login(client, "new.hire2@secco.example", created["temporary_password"])
    assert login["must_change_password"] is True

    blocked = client.get(f"{API}/employee/dashboard/{created['employee_id']}",
                         headers=_auth(login["token"]))
    assert blocked.status_code == 403
    assert "change-password" in blocked.json()["detail"]


def test_change_password_clears_the_flag_and_unblocks_access(client, company_admin):
    payload = {"first_name": "New", "last_name": "Hire3", "email": "new.hire3@secco.example",
              "country_code": "IL", "hire_date": str(date.today())}
    admin_token = _login(client, "admin@secco.demo", "Adm1nPass!")["token"]
    created = client.post(f"{API}/admin/employees", headers=_auth(admin_token), json=payload).json()
    login = _login(client, "new.hire3@secco.example", created["temporary_password"])

    changed = client.post(f"{API}/auth/change-password", headers=_auth(login["token"]),
                          json={"current_password": created["temporary_password"],
                                "new_password": "MyOwnNewPass1"})
    assert changed.status_code == 200

    ok = client.get(f"{API}/employee/dashboard/{created['employee_id']}",
                    headers=_auth(login["token"]))
    assert ok.status_code == 200

    relogin = _login(client, "new.hire3@secco.example", "MyOwnNewPass1")
    assert relogin["must_change_password"] is False


def test_change_password_rejects_wrong_current_password(client, company_admin):
    admin_token = _login(client, "admin@secco.demo", "Adm1nPass!")["token"]
    response = client.post(f"{API}/auth/change-password", headers=_auth(admin_token),
                           json={"current_password": "WrongOne!", "new_password": "Whatever123"})
    assert response.status_code == 401


def test_change_password_rejects_too_short_or_identical(client, company_admin):
    token = _login(client, "admin@secco.demo", "Adm1nPass!")["token"]

    too_short = client.post(f"{API}/auth/change-password", headers=_auth(token),
                            json={"current_password": "Adm1nPass!", "new_password": "short"})
    assert too_short.status_code == 400

    same = client.post(f"{API}/auth/change-password", headers=_auth(token),
                       json={"current_password": "Adm1nPass!", "new_password": "Adm1nPass!"})
    assert same.status_code == 400


def test_change_password_invalidates_other_sessions_but_not_the_current_one(
        client, company_admin, db_session):
    first_token = _login(client, "admin@secco.demo", "Adm1nPass!")["token"]
    second_token = _login(client, "admin@secco.demo", "Adm1nPass!")["token"]

    client.post(f"{API}/auth/change-password", headers=_auth(second_token),
               json={"current_password": "Adm1nPass!", "new_password": "BrandNewPass9"})

    still_valid = client.get(f"{API}/auth/me", headers=_auth(second_token))
    revoked = client.get(f"{API}/auth/me", headers=_auth(first_token))

    assert still_valid.status_code == 200, "ה-session שביצע את השינוי הנוכחי נותק בטעות"
    assert revoked.status_code == 401, "session אחר לא בוטל אחרי שינוי סיסמה"


# ===================================================================
# QA-051-07..10 - נעילת חשבון
# ===================================================================

def test_account_locks_after_max_failed_attempts(client, company_admin):
    for _ in range(MAX_FAILED_LOGIN_ATTEMPTS):
        r = client.post(f"{API}/auth/login",
                        json={"username": "admin@secco.demo", "password": "WrongPassword"})
        assert r.status_code == 401

    locked = client.post(f"{API}/auth/login",
                         json={"username": "admin@secco.demo", "password": "Adm1nPass!"})
    assert locked.status_code == 423, "החשבון לא ננעל למרות שהגיע לסף הכשלונות"
    assert "locked" in locked.json()["detail"].lower()


def test_locked_account_rejects_the_correct_password_too(client, company_admin):
    """הנעילה נבדקת *לפני* אימות הסיסמה - סיסמה נכונה לא אמורה "לעקוף" נעילה."""
    for _ in range(MAX_FAILED_LOGIN_ATTEMPTS):
        client.post(f"{API}/auth/login",
                   json={"username": "admin@secco.demo", "password": "WrongPassword"})

    response = client.post(f"{API}/auth/login",
                           json={"username": "admin@secco.demo", "password": "Adm1nPass!"})
    assert response.status_code == 423


def test_successful_login_resets_the_failed_attempt_counter(client, company_admin, db_session):
    for _ in range(MAX_FAILED_LOGIN_ATTEMPTS - 1):
        client.post(f"{API}/auth/login",
                   json={"username": "admin@secco.demo", "password": "WrongPassword"})

    ok = client.post(f"{API}/auth/login",
                     json={"username": "admin@secco.demo", "password": "Adm1nPass!"})
    assert ok.status_code == 200

    refreshed = db_session.query(User).filter(User.user_id == "U-SEC-ADMIN").first()
    assert refreshed.failed_login_attempts == 0
    assert not is_account_locked(refreshed)


def test_locked_out_flag_helper(company_admin):
    company_admin.locked_until = utcnow() + timedelta(minutes=5)
    assert is_account_locked(company_admin) is True

    company_admin.locked_until = utcnow() - timedelta(minutes=1)
    assert is_account_locked(company_admin) is False


# ===================================================================
# QA-051-11 - ניקוי session-ים שפגו
# ===================================================================

def test_login_cleans_up_expired_sessions(client, company_admin, db_session):
    db_session.add(UserSession(token="expired-tok", user_id=company_admin.user_id,
                               expires_at=utcnow() - timedelta(hours=1)))
    db_session.add(UserSession(token="still-valid-tok", user_id=company_admin.user_id,
                               expires_at=utcnow() + timedelta(hours=1)))
    db_session.flush()

    client.post(f"{API}/auth/login", json={"username": "admin@secco.demo", "password": "Adm1nPass!"})

    remaining = {s.token for s in db_session.query(UserSession)
                 .filter(UserSession.user_id == company_admin.user_id).all()}
    assert "expired-tok" not in remaining
    assert "still-valid-tok" in remaining


# ===================================================================
# QA-051-12 - חשבונות QA שנזרעים ישירות לא נפגעים
# ===================================================================

def test_seeded_qa_style_account_is_not_forced_to_change_password(client, db_session):
    """חשבון שנוצר ישירות (כמו seed_data.py, לא דרך /admin/employees) לא מסמן
    must_change_password - אחרת כל ספר הבדיקות (Demo1234! לכולם) היה נשבר."""
    db_session.add(Company(company_id="C-QA", name="QaCo", country_code="IL"))
    pw_hash, salt = hash_password("Demo1234!")
    db_session.add(User(user_id="U-QA-STYLE", username="qa.style@qaco.demo",
                        password_hash=pw_hash, password_salt=salt,
                        role=UserRole.COMPANY_ADMIN, company_id="C-QA"))
    db_session.flush()

    login = _login(client, "qa.style@qaco.demo", "Demo1234!")
    assert login["must_change_password"] is False

    ok = client.get(f"{API}/admin/employees", headers=_auth(login["token"]))
    assert ok.status_code == 200


# ---------------------------------------------------------------------------

def _login(client, username, password):
    response = client.post(f"{API}/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}
