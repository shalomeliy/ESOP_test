"""טבלת הון (Cap Table) - v1.0.0 שלב א: סכמה + ledger + API, בלי חישוב דילול/UI.

מכסה: happy-path + הרשאות לכל ה-endpoints החדשים (POST/GET admin/pools,
admin/share-classes, admin/shareholders, admin/share-issuances), בדיקות
חוצות-חברות (403/404), ולידציית shares > 0 / total_shares > 0, ותקרת
Company.total_authorized_shares (None = בלי בדיקה, מוגדר = 400 בחריגה -
דפוס הכשל P4). מיפוי ל-QA_TESTBOOK.md: QA-100-01 עד QA-100-xx (ראו
docs/qa/v1.0.0.md).
"""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from backend.app.types import utcnow
from backend.app.auth import hash_password
from backend.app.models import (
    Company, Employee, EmployeeStatus, OptionPool, User, UserRole, UserSession,
)

API = "/api/v1"


def _token(db, user: User) -> dict:
    token = f"tok-{user.user_id}"
    db.add(UserSession(token=token, user_id=user.user_id, expires_at=utcnow() + timedelta(hours=1)))
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
    """שתי חברות, כדי שאפשר יהיה להוכיח דליפה/403 חוצה-חברות ולא רק היקף ריק.
    COMP-CT-A מקבל פול, סוג מניה ובעל-מניות משלו; COMP-CT-B מקבל את אותם
    הדברים בנפרד - כדי שבדיקת חצייה תפנה לישות שקיימת באמת, רק בחברה הלא נכונה."""
    db = db_session
    db.add_all([
        Company(company_id="COMP-CT-A", name="Alpha", country_code="IL"),
        Company(company_id="COMP-CT-B", name="Beta", country_code="IL"),
    ])
    db.add(Employee(employee_id="CT-EMP-A1", company_id="COMP-CT-A", first_name="Yossi",
                    last_name="Cohen", email="ct-a1@alpha.example", country_code="IL",
                    status=EmployeeStatus.ACTIVE, hire_date=date(2020, 1, 1)))
    db.add(Employee(employee_id="CT-EMP-B1", company_id="COMP-CT-B", first_name="Rivka",
                    last_name="Levi", email="ct-b1@beta.example", country_code="IL",
                    status=EmployeeStatus.ACTIVE, hire_date=date(2020, 1, 1)))
    db.add_all([
        OptionPool(pool_id="CT-POOL-A", company_id="COMP-CT-A", total_shares=1000.0,
                   allocated_shares=0.0, unallocated_shares=1000.0),
        OptionPool(pool_id="CT-POOL-B", company_id="COMP-CT-B", total_shares=1000.0,
                   allocated_shares=0.0, unallocated_shares=1000.0),
    ])
    db.flush()

    admin_a = _user(db, "U-CT-ADMIN-A", UserRole.COMPANY_ADMIN, company_id="COMP-CT-A")
    admin_b = _user(db, "U-CT-ADMIN-B", UserRole.COMPANY_ADMIN, company_id="COMP-CT-B")
    emp_a = _user(db, "U-CT-EMP-A1", UserRole.EMPLOYEE, employee_id="CT-EMP-A1")

    return SimpleNamespace(
        db=db,
        admin_a=_token(db, admin_a), admin_b=_token(db, admin_b), emp_a=_token(db, emp_a),
    )


@pytest.fixture
def share_class_a(client, world):
    """סוג מניה אמיתי בחברה A, נוצר דרך ה-endpoint עצמו (לא הזרקה ישירה) -
    כדי שכל בדיקה שמפנה אליו תעבור דרך אותו קוד שהיא בודקת."""
    resp = client.post(f"{API}/admin/share-classes", headers=world.admin_a,
                       json={"name": "Common", "class_type": "COMMON", "seniority_order": 10})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture
def share_class_b(client, world):
    resp = client.post(f"{API}/admin/share-classes", headers=world.admin_b,
                       json={"name": "Common", "class_type": "COMMON", "seniority_order": 10})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture
def shareholder_a(client, world):
    resp = client.post(f"{API}/admin/shareholders", headers=world.admin_a,
                       json={"name": "Founder Inc", "shareholder_type": "FOUNDER"})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture
def shareholder_b(client, world):
    resp = client.post(f"{API}/admin/shareholders", headers=world.admin_b,
                       json={"name": "Investor Fund", "shareholder_type": "INVESTOR"})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ===================================================================
# ShareClass - יצירה + רשימה + הרשאות
# ===================================================================

def test_create_share_class_happy_path(client, world):
    response = client.post(f"{API}/admin/share-classes", headers=world.admin_a, json={
        "name": "Preferred A", "class_type": "PREFERRED", "seniority_order": 1,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["company_id"] == "COMP-CT-A"
    assert body["name"] == "Preferred A"
    assert body["seniority_order"] == 1


def test_list_share_classes_scoped_to_own_company(client, world, share_class_a, share_class_b):
    response = client.get(f"{API}/admin/share-classes", headers=world.admin_a)
    assert response.status_code == 200
    ids = {c["share_class_id"] for c in response.json()}
    assert ids == {share_class_a["share_class_id"]}, f"דליפה בין חברות: {ids}"


def test_create_share_class_requires_company_admin_role(client, world):
    response = client.post(f"{API}/admin/share-classes", headers=world.emp_a, json={
        "name": "Common", "class_type": "COMMON", "seniority_order": 10,
    })
    assert response.status_code == 403


# ===================================================================
# Shareholder - יצירה (עם/בלי employee_id) + רשימה + הרשאות
# ===================================================================

def test_create_shareholder_external_investor_without_employee_id(client, world):
    """משקיע חיצוני - employee_id=None, ראו models.py.Shareholder.employee_id."""
    response = client.post(f"{API}/admin/shareholders", headers=world.admin_a, json={
        "name": "Acme Ventures", "shareholder_type": "INVESTOR",
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["employee_id"] is None
    assert body["shareholder_type"] == "INVESTOR"


def test_create_shareholder_linked_to_an_existing_employee(client, world):
    """עובד שמימש לכדי מניות ממשיות - מקושר לזהות הקיימת, לא משוכפל."""
    response = client.post(f"{API}/admin/shareholders", headers=world.admin_a, json={
        "name": "Yossi Cohen", "shareholder_type": "EMPLOYEE", "employee_id": "CT-EMP-A1",
    })
    assert response.status_code == 200, response.text
    assert response.json()["employee_id"] == "CT-EMP-A1"


def test_list_shareholders_scoped_to_own_company(client, world, shareholder_a, shareholder_b):
    response = client.get(f"{API}/admin/shareholders", headers=world.admin_a)
    assert response.status_code == 200
    ids = {s["shareholder_id"] for s in response.json()}
    assert ids == {shareholder_a["shareholder_id"]}, f"דליפה בין חברות: {ids}"


def test_create_shareholder_requires_company_admin_role(client, world):
    response = client.post(f"{API}/admin/shareholders", headers=world.emp_a, json={
        "name": "Someone", "shareholder_type": "FOUNDER",
    })
    assert response.status_code == 403


def test_create_shareholder_linked_to_another_companys_employee_is_403(client, world):
    """נועל את התיקון בקוד הייצור (backend-engineer, cap_table.py::create_shareholder):
    admin_a לא יכול לקשר Shareholder ל-employee_id ששייך ל-COMP-CT-B - קישור
    זהות חוצה-חברות שגוי, אפילו בלי לחשוף דאטה של B ישירות. לפני התיקון
    (נמצא בסקירת ה-QA הזו) זה הצליח בשקט עם 200."""
    response = client.post(f"{API}/admin/shareholders", headers=world.admin_a, json={
        "name": "Sneaky", "shareholder_type": "EMPLOYEE", "employee_id": "CT-EMP-B1",
    })
    assert response.status_code == 403


def test_create_shareholder_with_unknown_employee_id_is_404_not_500(client, world):
    """נועל את התיקון בקוד הייצור: employee_id שלא קיים בכלל חייב להישאר 404
    נקי. לפני התיקון (נמצא בסקירת ה-QA הזו) זה הפיל IntegrityError לא מטופל
    (500) - אין בדיקת קיום מוקדמת לפני ה-INSERT."""
    response = client.post(f"{API}/admin/shareholders", headers=world.admin_a, json={
        "name": "Totally Bogus", "shareholder_type": "EMPLOYEE", "employee_id": "DOES-NOT-EXIST-AT-ALL",
    })
    assert response.status_code == 404


# ===================================================================
# POST/GET /admin/pools - פול נוסף, עם/בלי share_class_id
# ===================================================================

def test_create_pool_without_share_class(client, world):
    response = client.post(f"{API}/admin/pools", headers=world.admin_a, json={
        "total_shares": 5000.0, "established_date": "2024-01-01",
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["share_class_id"] is None
    assert body["total_shares"] == 5000.0
    assert body["allocated_shares"] == 0.0
    assert body["unallocated_shares"] == 5000.0


def test_create_pool_with_own_share_class(client, world, share_class_a):
    response = client.post(f"{API}/admin/pools", headers=world.admin_a, json={
        "total_shares": 5000.0, "share_class_id": share_class_a["share_class_id"],
        "established_date": "2024-01-01",
    })
    assert response.status_code == 200, response.text
    assert response.json()["share_class_id"] == share_class_a["share_class_id"]


def test_create_pool_with_another_companys_share_class_is_403(client, world, share_class_b):
    response = client.post(f"{API}/admin/pools", headers=world.admin_a, json={
        "total_shares": 5000.0, "share_class_id": share_class_b["share_class_id"],
        "established_date": "2024-01-01",
    })
    assert response.status_code == 403


def test_create_pool_with_unknown_share_class_is_404(client, world):
    response = client.post(f"{API}/admin/pools", headers=world.admin_a, json={
        "total_shares": 5000.0, "share_class_id": "NO-SUCH-SHARE-CLASS",
        "established_date": "2024-01-01",
    })
    assert response.status_code == 404


@pytest.mark.parametrize("bad_total", [0.0, -100.0])
def test_create_pool_rejects_non_positive_total_shares(client, world, bad_total):
    response = client.post(f"{API}/admin/pools", headers=world.admin_a, json={
        "total_shares": bad_total, "established_date": "2024-01-01",
    })
    assert response.status_code == 400
    assert "total_shares" in response.json()["detail"]


def test_list_pools_scoped_to_own_company(client, world):
    response = client.get(f"{API}/admin/pools", headers=world.admin_a)
    assert response.status_code == 200
    ids = {p["pool_id"] for p in response.json()}
    assert ids == {"CT-POOL-A"}, f"דליפה בין חברות: {ids}"


def test_create_pool_requires_company_admin_role(client, world):
    response = client.post(f"{API}/admin/pools", headers=world.emp_a, json={
        "total_shares": 100.0, "established_date": "2024-01-01",
    })
    assert response.status_code == 403


def test_create_pool_writes_a_live_pool_balance_established_event(client, world):
    """אותו סוג אירוע בסיס בדיוק כמו backfill (POOL_BALANCE_ESTABLISHED), רק
    source=LIVE - ראו התכנון."""
    from backend.app.services.ledger import project

    response = client.post(f"{API}/admin/pools", headers=world.admin_a, json={
        "total_shares": 7500.0, "established_date": "2024-03-01",
    })
    pool_id = response.json()["pool_id"]

    state = project(world.db, "OptionPool", pool_id)
    assert state == {"allocated_shares": 0.0, "unallocated_shares": 7500.0, "total_shares": 7500.0}


# ===================================================================
# POST/GET /admin/share-issuances - הקצאת מניות
# ===================================================================

def test_create_share_issuance_happy_path(client, world, shareholder_a, share_class_a):
    response = client.post(f"{API}/admin/share-issuances", headers=world.admin_a, json={
        "shareholder_id": shareholder_a["shareholder_id"],
        "share_class_id": share_class_a["share_class_id"],
        "shares": 1000.0, "issue_date": "2023-06-15",
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["company_id"] == "COMP-CT-A"
    assert body["shares"] == 1000.0
    assert body["issue_date"] == "2023-06-15"


def test_create_share_issuance_with_a_historical_issue_date(client, world, shareholder_a, share_class_a):
    """issue_date קלט מפורש, לא נגזר מהשעון - יכול להיות בעבר הרחוק (הזנת
    נתונים היסטוריים), ראו models.py.ShareIssuance.issue_date."""
    response = client.post(f"{API}/admin/share-issuances", headers=world.admin_a, json={
        "shareholder_id": shareholder_a["shareholder_id"],
        "share_class_id": share_class_a["share_class_id"],
        "shares": 250.0, "issue_date": "2015-01-01",
    })
    assert response.status_code == 200, response.text
    assert response.json()["issue_date"] == "2015-01-01"


def test_create_share_issuance_unknown_shareholder_is_404(client, world, share_class_a):
    response = client.post(f"{API}/admin/share-issuances", headers=world.admin_a, json={
        "shareholder_id": "NO-SUCH-SHAREHOLDER", "share_class_id": share_class_a["share_class_id"],
        "shares": 100.0, "issue_date": "2024-01-01",
    })
    assert response.status_code == 404


def test_create_share_issuance_to_another_companys_shareholder_is_404_like_a_missing_one(
        client, world, shareholder_b, share_class_a, shareholder_a):
    """*** שינוי התנהגות מכוון, v1.2.0 (מפרט §7, קריטריון 8). ***
    עד כאן זה היה 403, וזה היה אורקל קיום חוצה-טננטים: 403 אמר "המזהה הזה
    אמיתי, הוא פשוט לא שלך", בעוד מזהה מומצא החזיר 404. שני המקרים זהים עכשיו,
    כולל גוף התשובה - הבדל בטקסט היה מחזיר את אותו אורקל בדלת האחורית."""
    real_but_foreign = client.post(f"{API}/admin/share-issuances", headers=world.admin_a, json={
        "shareholder_id": shareholder_b["shareholder_id"], "share_class_id": share_class_a["share_class_id"],
        "shares": 100.0, "issue_date": "2024-01-01",
    })
    invented = client.post(f"{API}/admin/share-issuances", headers=world.admin_a, json={
        "shareholder_id": "SH-DOES-NOT-EXIST", "share_class_id": share_class_a["share_class_id"],
        "shares": 100.0, "issue_date": "2024-01-01",
    })

    assert real_but_foreign.status_code == 404
    assert invented.status_code == 404
    assert real_but_foreign.json() == invented.json(), "התשובות חייבות להיות בלתי-מבחינות"


def test_create_share_issuance_unknown_share_class_is_404(client, world, shareholder_a):
    response = client.post(f"{API}/admin/share-issuances", headers=world.admin_a, json={
        "shareholder_id": shareholder_a["shareholder_id"], "share_class_id": "NO-SUCH-CLASS",
        "shares": 100.0, "issue_date": "2024-01-01",
    })
    assert response.status_code == 404


def test_create_share_issuance_of_another_companys_share_class_is_404_like_a_missing_one(
        client, world, shareholder_a, share_class_b):
    """אותו שינוי מכוון כמו למעלה, בצד סוג המניה."""
    real_but_foreign = client.post(f"{API}/admin/share-issuances", headers=world.admin_a, json={
        "shareholder_id": shareholder_a["shareholder_id"], "share_class_id": share_class_b["share_class_id"],
        "shares": 100.0, "issue_date": "2024-01-01",
    })
    invented = client.post(f"{API}/admin/share-issuances", headers=world.admin_a, json={
        "shareholder_id": shareholder_a["shareholder_id"], "share_class_id": "SC-DOES-NOT-EXIST",
        "shares": 100.0, "issue_date": "2024-01-01",
    })

    assert real_but_foreign.status_code == 404
    assert invented.status_code == 404
    assert real_but_foreign.json() == invented.json()


@pytest.mark.parametrize("bad_shares", [0.0, -50.0])
def test_create_share_issuance_rejects_non_positive_shares(client, world, shareholder_a, share_class_a, bad_shares):
    response = client.post(f"{API}/admin/share-issuances", headers=world.admin_a, json={
        "shareholder_id": shareholder_a["shareholder_id"], "share_class_id": share_class_a["share_class_id"],
        "shares": bad_shares, "issue_date": "2024-01-01",
    })
    assert response.status_code == 400
    assert "shares" in response.json()["detail"]


def test_list_share_issuances_scoped_to_own_company(client, world, shareholder_a, share_class_a,
                                                     shareholder_b, share_class_b):
    a = client.post(f"{API}/admin/share-issuances", headers=world.admin_a, json={
        "shareholder_id": shareholder_a["shareholder_id"], "share_class_id": share_class_a["share_class_id"],
        "shares": 100.0, "issue_date": "2024-01-01",
    })
    client.post(f"{API}/admin/share-issuances", headers=world.admin_b, json={
        "shareholder_id": shareholder_b["shareholder_id"], "share_class_id": share_class_b["share_class_id"],
        "shares": 200.0, "issue_date": "2024-01-01",
    })

    response = client.get(f"{API}/admin/share-issuances", headers=world.admin_a)
    assert response.status_code == 200
    ids = {i["share_issuance_id"] for i in response.json()}
    assert ids == {a.json()["share_issuance_id"]}, f"דליפה בין חברות: {ids}"


def test_create_share_issuance_requires_company_admin_role(client, world, shareholder_a, share_class_a):
    response = client.post(f"{API}/admin/share-issuances", headers=world.emp_a, json={
        "shareholder_id": shareholder_a["shareholder_id"], "share_class_id": share_class_a["share_class_id"],
        "shares": 100.0, "issue_date": "2024-01-01",
    })
    assert response.status_code == 403


# ===================================================================
# תקרת Company.total_authorized_shares - דפוס הכשל P4 שסוכן ה-QA-expert
# סימן במפורש בסקירה המקדימה (התכנון, סעיף data model). None = בלי בדיקה
# בכלל; מוגדר = 400 בחריגה.
# ===================================================================

def test_share_issuance_succeeds_without_limit_when_authorized_shares_is_none(client, world, shareholder_a, share_class_a):
    """Company.total_authorized_shares == None כברירת מחדל (חברה קיימת/מזורעת
    שלא הוזן לה ערך) - שום תקרה לא נבדקת, לא 0%. ראו models.py.Company."""
    comp = world.db.get(Company, "COMP-CT-A")
    assert comp.total_authorized_shares is None  # הנחת המוצא של הבדיקה

    response = client.post(f"{API}/admin/share-issuances", headers=world.admin_a, json={
        "shareholder_id": shareholder_a["shareholder_id"], "share_class_id": share_class_a["share_class_id"],
        "shares": 10_000_000.0, "issue_date": "2024-01-01",
    })
    assert response.status_code == 200, response.text


def test_share_issuance_exceeding_authorized_shares_cap_is_rejected(client, world, shareholder_a, share_class_a):
    """1,000 מוכרזות; מנפיקים 1,000 (עובר), ואז עוד 1 (חוצה) => 400."""
    cap_response = client.put(f"{API}/admin/company", headers=world.admin_a,
                              json={"total_authorized_shares": 1000.0})
    assert cap_response.status_code == 200
    assert cap_response.json()["total_authorized_shares"] == 1000.0

    first = client.post(f"{API}/admin/share-issuances", headers=world.admin_a, json={
        "shareholder_id": shareholder_a["shareholder_id"], "share_class_id": share_class_a["share_class_id"],
        "shares": 1000.0, "issue_date": "2024-01-01",
    })
    assert first.status_code == 200, first.text

    second = client.post(f"{API}/admin/share-issuances", headers=world.admin_a, json={
        "shareholder_id": shareholder_a["shareholder_id"], "share_class_id": share_class_a["share_class_id"],
        "shares": 1.0, "issue_date": "2024-02-01",
    })
    assert second.status_code == 400
    assert "total_authorized_shares" in second.json()["detail"]


def test_share_issuance_exactly_at_the_authorized_shares_cap_succeeds(client, world, shareholder_a, share_class_a):
    """גבול מדויק: 1,000 מוכרזות, מנפיקים בדיוק 1,000 - זה *לא* חריגה
    (הבדיקה בקוד היא ``>``, לא ``>=``): שוויון מותר."""
    client.put(f"{API}/admin/company", headers=world.admin_a, json={"total_authorized_shares": 1000.0})

    response = client.post(f"{API}/admin/share-issuances", headers=world.admin_a, json={
        "shareholder_id": shareholder_a["shareholder_id"], "share_class_id": share_class_a["share_class_id"],
        "shares": 1000.0, "issue_date": "2024-01-01",
    })
    assert response.status_code == 200, response.text


def test_share_issuance_cap_accounts_for_previously_issued_shares_across_calls(client, world, shareholder_a, share_class_a):
    """התקרה נבדקת מול הסכום שכבר קיים + הבקשה הנוכחית, לא רק מול הבקשה
    הבודדת - שני מימושים קטנים שסוכמים לחריגה חייבים להיחסם באחד מהם."""
    client.put(f"{API}/admin/company", headers=world.admin_a, json={"total_authorized_shares": 1500.0})

    first = client.post(f"{API}/admin/share-issuances", headers=world.admin_a, json={
        "shareholder_id": shareholder_a["shareholder_id"], "share_class_id": share_class_a["share_class_id"],
        "shares": 1000.0, "issue_date": "2024-01-01",
    })
    assert first.status_code == 200

    second = client.post(f"{API}/admin/share-issuances", headers=world.admin_a, json={
        "shareholder_id": shareholder_a["shareholder_id"], "share_class_id": share_class_a["share_class_id"],
        "shares": 600.0, "issue_date": "2024-02-01",
    })
    assert second.status_code == 400
    assert "500" in second.json()["detail"], (
        f"available אמור לדווח 500 (1500-1000): {second.json()['detail']}"
    )


def test_authorized_shares_cap_is_scoped_per_company(client, world, shareholder_a, share_class_a,
                                                      shareholder_b, share_class_b):
    """תקרה שהוגדרה לחברה A לא אמורה להשפיע על חברה B - סכימת existing_total
    מסוננת לפי company_id, לא גלובלית."""
    client.put(f"{API}/admin/company", headers=world.admin_a, json={"total_authorized_shares": 100.0})
    # B בלי תקרה בכלל - מנפיקה כמות שהייתה נחשבת "חריגה" אם התקרה של A הייתה גלובלית.
    response = client.post(f"{API}/admin/share-issuances", headers=world.admin_b, json={
        "shareholder_id": shareholder_b["shareholder_id"], "share_class_id": share_class_b["share_class_id"],
        "shares": 500.0, "issue_date": "2024-01-01",
    })
    assert response.status_code == 200, response.text


# ===================================================================
# CompanyUpdateRequest.total_authorized_shares - None = "לא נגיעה", לא איפוס
# ===================================================================

def test_updating_company_name_does_not_reset_total_authorized_shares(client, world):
    client.put(f"{API}/admin/company", headers=world.admin_a, json={"total_authorized_shares": 2000.0})

    response = client.put(f"{API}/admin/company", headers=world.admin_a, json={"name": "Alpha Renamed"})
    assert response.status_code == 200
    assert response.json()["total_authorized_shares"] == 2000.0
    assert response.json()["name"] == "Alpha Renamed"


# ===================================================================
# Audit log - ענפי ShareClass/Shareholder/ShareIssuance ב-GET /admin/audit-log
# ===================================================================

def test_audit_log_records_and_scopes_share_class_creation(client, world, share_class_a):
    response = client.get(f"{API}/admin/audit-log", headers=world.admin_a,
                          params={"entity_type": "ShareClass", "entity_id": share_class_a["share_class_id"]})
    assert response.status_code == 200
    assert any(row["action"] == "CREATE" for row in response.json())


def test_audit_log_for_share_class_is_403_for_another_company(client, world, share_class_a):
    response = client.get(f"{API}/admin/audit-log", headers=world.admin_b,
                          params={"entity_type": "ShareClass", "entity_id": share_class_a["share_class_id"]})
    assert response.status_code == 403


def test_audit_log_for_share_issuance_is_403_for_another_company(client, world, shareholder_a, share_class_a):
    issuance = client.post(f"{API}/admin/share-issuances", headers=world.admin_a, json={
        "shareholder_id": shareholder_a["shareholder_id"], "share_class_id": share_class_a["share_class_id"],
        "shares": 10.0, "issue_date": "2024-01-01",
    }).json()

    own = client.get(f"{API}/admin/audit-log", headers=world.admin_a,
                     params={"entity_type": "ShareIssuance", "entity_id": issuance["share_issuance_id"]})
    assert own.status_code == 200
    assert any(row["action"] == "CREATE" for row in own.json())

    other = client.get(f"{API}/admin/audit-log", headers=world.admin_b,
                       params={"entity_type": "ShareIssuance", "entity_id": issuance["share_issuance_id"]})
    assert other.status_code == 403


# ===================================================================
# Ledger replay-equivalence ל-ShareIssuance - QA-100 (ראו גם test_ledger_replay.py,
# שאינו מכסה את זה אוטומטית: test_replay_equivalence_for_every_aggregate_type
# אינו parametrized, וה-seeded_world שלו לא כולל ShareIssuance כי backfill_ledger.py
# לא מכיר ישות שנוצרה רק אחרי v1.0.0 - נבדק ישירות במקום להניח.
# ===================================================================

def test_share_issuance_ledger_replay_matches_the_mutable_row(client, world, shareholder_a, share_class_a):
    from backend.app.services.ledger import project

    response = client.post(f"{API}/admin/share-issuances", headers=world.admin_a, json={
        "shareholder_id": shareholder_a["shareholder_id"], "share_class_id": share_class_a["share_class_id"],
        "shares": 4242.0, "issue_date": "2019-09-09",
    })
    assert response.status_code == 200, response.text
    issuance_id = response.json()["share_issuance_id"]

    from backend.app.models import ShareIssuance
    row = world.db.get(ShareIssuance, issuance_id)

    state = project(world.db, "ShareIssuance", issuance_id)
    assert state == {
        "shares": row.shares, "shareholder_id": row.shareholder_id,
        "share_class_id": row.share_class_id, "issue_date": row.issue_date,
    }


def test_share_issuance_ledger_ownership_is_scoped_to_the_issuing_company(client, world, shareholder_a, share_class_a):
    from backend.app.models import LedgerOwnership

    response = client.post(f"{API}/admin/share-issuances", headers=world.admin_a, json={
        "shareholder_id": shareholder_a["shareholder_id"], "share_class_id": share_class_a["share_class_id"],
        "shares": 77.0, "issue_date": "2022-02-02",
    })
    issuance_id = response.json()["share_issuance_id"]

    ownership = world.db.get(LedgerOwnership, issuance_id)
    assert ownership is not None
    assert ownership.company_id == "COMP-CT-A"


# ===================================================================
# בדיקת שקילות רגרסיה ל-v1.2.0 (מפרט §11.1) - *** נכתבת לפני הפיצ'ר ***.
#
# למה היא קיימת: v1.2.0 מעבירה את צד המונפק ב-compute_cap_table_snapshot
# מסינון-עמודה ישיר (issue_date <= as_of) ל-replay מלא של ה-ledger. הסיכון
# הגדול בגרסה אינו "הרכישה העצמית לא עובדת" - זה ייכשל ברעש - אלא שהמעבר
# ישנה *בשקט* מספר היסטורי שכבר דווח למשתמש.
#
# הצורה: האלגוריתם *הישן* מוקפא כאן בתוך הבדיקה (_frozen_column_algorithm),
# ולא נקרא מקוד הייצור. כך אחרי שקוד הייצור יוחלף ב-replay, הבדיקה עדיין
# מחזיקה את ההתנהגות המקורית ומשווה מולה - וזו ההוכחה שהמעבר לא שינה דבר.
# לו הבדיקה הייתה קוראת לקוד הייצור לשני הצדדים, היא הייתה עוברת תמיד.
# ===================================================================

def _frozen_column_algorithm(db, company_id, as_of):
    """העתק מוקפא של צד המונפק כפי שהוא ב-services/cap_table.py לפני v1.2.0.
    *** אין לעדכן אותו כשקוד הייצור משתנה *** - זו כל הנקודה שלו."""
    from backend.app.models import ShareIssuance

    rows = (
        db.query(ShareIssuance)
        .filter(ShareIssuance.company_id == company_id, ShareIssuance.issue_date <= as_of)
        .all()
    )
    outstanding = 0.0
    by_key: dict[tuple[str, str], float] = {}
    for row in rows:
        key = (row.shareholder_id, row.share_class_id)
        by_key[key] = by_key.get(key, 0.0) + row.shares
        outstanding += row.shares
    return outstanding, by_key


@pytest.fixture
def issuance_history(client, world, share_class_a, shareholder_a):
    """היסטוריית הנפקות אמיתית בשתי חברות ובשני בעלי מניות, כדי שהשקילות
    תיבדק על פילוח לא-טריוויאלי ולא על שורה בודדת. חברה B מקבלת הנפקה משלה
    באותם תאריכים - שקילות שמתעלמת מבידוד הטננטים אינה שווה כלום."""
    second = client.post(f"{API}/admin/shareholders", headers=world.admin_a,
                         json={"name": "Seed Fund", "shareholder_type": "INVESTOR"})
    assert second.status_code == 200, second.text
    second_id = second.json()["shareholder_id"]

    for shareholder_id, shares, issue_date in [
        (shareholder_a["shareholder_id"], 1000.0, "2021-03-01"),
        (second_id, 250.0, "2022-06-15"),
        (shareholder_a["shareholder_id"], 400.0, "2022-06-15"),   # אותו תאריך, בעל מניות אחר
        (shareholder_a["shareholder_id"], 125.5, "2024-01-31"),   # מנה שנייה לאותו צמד
    ]:
        resp = client.post(f"{API}/admin/share-issuances", headers=world.admin_a, json={
            "shareholder_id": shareholder_id, "share_class_id": share_class_a["share_class_id"],
            "shares": shares, "issue_date": issue_date,
        })
        assert resp.status_code == 200, resp.text

    return SimpleNamespace(second_shareholder_id=second_id)


@pytest.mark.parametrize("as_of", [
    "2020-01-01",   # לפני כל הנפקה
    "2021-02-28",   # יום לפני הראשונה
    "2021-03-01",   # בדיוק ביום הראשונה (גבול <=)
    "2021-03-02",
    "2022-06-14",
    "2022-06-15",   # שתי הנפקות באותו יום
    "2023-12-31",
    "2024-01-31",   # המנה השנייה לאותו צמד
    "2025-07-04",
])
def test_cap_table_outstanding_side_matches_the_frozen_pre_v120_algorithm(
        client, world, issuance_history, as_of):
    """§11.1: לכל תאריך היסטורי, צד המונפק חייב להחזיר בדיוק את מה שהחזיר
    לפני המעבר ל-replay - הסכום *וגם* הפילוח."""
    from backend.app.services.cap_table import compute_cap_table_snapshot

    as_of_date = date.fromisoformat(as_of)
    expected_total, expected_by_key = _frozen_column_algorithm(world.db, "COMP-CT-A", as_of_date)

    snapshot = compute_cap_table_snapshot(world.db, "COMP-CT-A", as_of_date)

    assert snapshot["outstanding_shares"] == expected_total, (
        f"as_of={as_of}: המעבר ל-replay שינה את סך המניות המונפקות"
    )
    actual_by_key = {
        (row["shareholder_id"], row["share_class_id"]): row["shares"]
        for row in snapshot["by_shareholder_and_class"]
    }
    assert actual_by_key == expected_by_key, f"as_of={as_of}: הפילוח השתנה"


def test_cap_table_outstanding_side_stays_scoped_to_one_company_across_the_replay_switch(
        client, world, issuance_history, share_class_b, shareholder_b):
    """בידוד טננטים הוא חלק מהשקילות ולא בדיקה נפרדת: שאילתת ה-replay החדשה
    מקבצת אירועים לפי aggregate_id, וקיבוץ שמאבד את חתך החברה היה מדליף
    החזקות של חברה אחרת אל תוך הסכום - בלי שאף בדיקה קיימת תבחין."""
    from backend.app.services.cap_table import compute_cap_table_snapshot

    resp = client.post(f"{API}/admin/share-issuances", headers=world.admin_b, json={
        "shareholder_id": shareholder_b["shareholder_id"],
        "share_class_id": share_class_b["share_class_id"],
        "shares": 9999.0, "issue_date": "2021-03-01",
    })
    assert resp.status_code == 200, resp.text

    as_of_date = date(2025, 7, 4)
    snapshot_a = compute_cap_table_snapshot(world.db, "COMP-CT-A", as_of_date)
    expected_a, _ = _frozen_column_algorithm(world.db, "COMP-CT-A", as_of_date)

    assert snapshot_a["outstanding_shares"] == expected_a
    assert 9999.0 not in [row["shares"] for row in snapshot_a["by_shareholder_and_class"]]


def _establish_pool_ledger_history(db, pool_id: str, company_id: str):
    """נותן לפול היסטוריית ledger, כמו ש-backfill_ledger.py עושה על דאטה אמיתית.
    בלי זה כל תמונת מצב היסטורית מדליקה partial *מצד הפול*, וזה היה מסתיר את
    מה שהבדיקות למטה בודקות בצד המונפק."""
    from backend.app.models import OptionPool
    from backend.app.services.ledger import LEDGER_EPOCH, append_event, record_ownership

    pool = db.get(OptionPool, pool_id)
    record_ownership(db, aggregate_id=pool_id, aggregate_type="OptionPool", company_id=company_id)
    append_event(db, event_type="POOL_BALANCE_ESTABLISHED", aggregate_type="OptionPool",
                 aggregate_id=pool_id, effective_date=LEDGER_EPOCH,
                 payload={"allocated_shares": pool.allocated_shares,
                          "unallocated_shares": pool.unallocated_shares,
                          "total_shares": pool.total_shares})
    db.flush()


@pytest.mark.parametrize("as_of", ["2020-01-01", "2021-02-28", "2022-01-01", "2023-06-30"])
def test_historical_snapshot_is_not_flagged_partial_just_because_an_issuance_is_newer(
        client, world, issuance_history, as_of):
    """*** קריטריון 14 - הבדיקה שתופסת את רגרסיית ה-partial. ***

    אירוע הבסיס של ShareIssuance מתועד ב-effective_date=issue_date אמיתי ולא
    ב-LEDGER_EPOCH. לכן שאילתת אירועים שחתוכה ב-as_of מחזירה קבוצה *ריקה* לכל
    הנפקה מאוחרת מ-as_of. מימוש replay שמפרש קבוצה ריקה כ"אין היסטוריה" היה
    מדליק partial ואזהרות על כל תמונת מצב היסטורית בכל חברה בריאה - וכל
    הבדיקות האחרות היו ממשיכות לעבור.

    'הנפקה שטרם קרתה' ו'הנפקה בלי היסטוריה' הם שני מצבים שונים לגמרי.
    """
    from backend.app.services.cap_table import compute_cap_table_snapshot

    _establish_pool_ledger_history(world.db, "CT-POOL-A", "COMP-CT-A")

    snapshot = compute_cap_table_snapshot(world.db, "COMP-CT-A", date.fromisoformat(as_of))

    assert snapshot["partial"] is False, (
        f"as_of={as_of}: partial הודלק על דאטה בריאה. אזהרות: {snapshot['warnings']}"
    )
    assert snapshot["warnings"] == []


def test_an_issuance_row_without_any_ledger_history_is_flagged_and_never_counted_as_zero(
        client, world, share_class_a, shareholder_a):
    """הצד השני של אותו מטבע: שורה שקיימת באמת בלי אף אירוע *כן* מדליקה partial.
    המצב מיוצר כמו ש-import_.py מייצר אותו - שורה שנוחתת בלי האירוע שלה."""
    from backend.app.models import ShareIssuance
    from backend.app.services.cap_table import compute_cap_table_snapshot

    _establish_pool_ledger_history(world.db, "CT-POOL-A", "COMP-CT-A")
    world.db.add(ShareIssuance(
        share_issuance_id="ISS-NO-LEDGER", company_id="COMP-CT-A",
        shareholder_id=shareholder_a["shareholder_id"],
        share_class_id=share_class_a["share_class_id"],
        shares=500.0, issue_date=date(2021, 1, 1)))
    world.db.flush()

    snapshot = compute_cap_table_snapshot(world.db, "COMP-CT-A", date(2023, 1, 1))

    assert snapshot["partial"] is True
    assert any("ISS-NO-LEDGER" in w for w in snapshot["warnings"])
    assert snapshot["outstanding_shares"] == 0.0, "הוחרג מהסכום, לא נספר כ-0 שקרי"
    row = next(r for r in snapshot["by_shareholder_and_class"]
               if r["shareholder_id"] == shareholder_a["shareholder_id"])
    assert row["shares"] is None, "צמד עם שורה חסרת היסטוריה מדווח None, לא סכום חלקי"


def test_share_issuance_aggregate_type_is_registered_everywhere_ledger_needs_it():
    """אימות ישיר (לא הנחה) ש-ShareIssuance רשום בכל שלוש הנקודות שבלעדיהן
    append_event/project היו נכשלים בשקט או בחריגה: LEDGER_AGGREGATE_TYPES,
    ו-PROJECTORS."""
    from backend.app.models import LEDGER_AGGREGATE_TYPES, LEDGER_EVENT_TYPES
    from backend.app.services.ledger import PROJECTORS

    assert "ShareIssuance" in LEDGER_AGGREGATE_TYPES
    assert "SHARE_ISSUANCE_ESTABLISHED" in LEDGER_EVENT_TYPES
    assert "ShareIssuance" in PROJECTORS
