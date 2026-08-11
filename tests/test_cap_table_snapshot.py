"""GET /admin/cap-table/snapshot (חישוב דילול) - v1.0.0 שלב ב.

מכסה: הדוגמה המחושבת-ביד (outstanding/fully-diluted/אחוזים), דפוס הכשל P4
(``total_authorized_shares is None`` => אחוזים ``None`` לא ``0``), פול בלי
``share_class_id`` ("unassigned") שלא נשמט מהחישוב, פול בלי היסטוריית ledger
בכלל בתאריך היסטורי (``partial=True`` + warning, לא 500 ולא 0 שקרי), היקף
חוצה-חברות, תאריך ``as_of`` עתידי ששקול ל"היום", ו-``total_authorized_shares
== 0.0`` (ערך ממשי, לא None - נתפס בסקירה עצמאית כ-ZeroDivisionError לא-מטופל,
ראו הבדיקה האחרונה בקובץ). מיפוי ל-QA_TESTBOOK.md: QA-100-42 עד QA-100-49
(ראו docs/qa/v1.0.0.md).

לא ניגשים ל-``world``/fixtures הקיימים ב-test_cap_table.py כי אלה כבר יוצרים
פול+עובד+אדמין קבועים ל-COMP-CT-A/B (המספרים שלהם היו מתערבבים עם הדוגמה
המחושבת-ביד כאן) - פיקסצ'ר ייעודי, קטן, בלי דאטה קבועה מלבד שתי החברות
והאדמינים עצמם; כל בדיקה בונה את הפול/ההנפקה שהיא צריכה בעצמה.
"""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from backend.app.types import utcnow, business_today
from backend.app.auth import hash_password
from backend.app.models import Company, Employee, EmployeeStatus, OptionPool, User, UserRole, UserSession

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
    """שתי חברות ריקות (בלי פול/הנפקה קבועים - כל בדיקה בונה את מה שהיא
    צריכה) + אדמין לכל אחת + עובד רגיל בחברה A (לבדיקת 403 חוצת-תפקיד)."""
    db = db_session
    db.add_all([
        Company(company_id="COMP-CTS-A", name="Snap Alpha", country_code="IL"),
        Company(company_id="COMP-CTS-B", name="Snap Beta", country_code="IL"),
    ])
    db.add(Employee(employee_id="CTS-EMP-A1", company_id="COMP-CTS-A", first_name="Dana",
                    last_name="Bar", email="cts-a1@alpha.example", country_code="IL",
                    status=EmployeeStatus.ACTIVE, hire_date=date(2020, 1, 1)))
    db.flush()

    admin_a = _user(db, "U-CTS-ADMIN-A", UserRole.COMPANY_ADMIN, company_id="COMP-CTS-A")
    admin_b = _user(db, "U-CTS-ADMIN-B", UserRole.COMPANY_ADMIN, company_id="COMP-CTS-B")
    emp_a = _user(db, "U-CTS-EMP-A1", UserRole.EMPLOYEE, employee_id="CTS-EMP-A1")

    return SimpleNamespace(
        db=db,
        admin_a=_token(db, admin_a), admin_b=_token(db, admin_b), emp_a=_token(db, emp_a),
    )


def _create_share_class(client, headers, name="Common"):
    resp = client.post(f"{API}/admin/share-classes", headers=headers,
                       json={"name": name, "class_type": "COMMON", "seniority_order": 10})
    assert resp.status_code == 200, resp.text
    return resp.json()["share_class_id"]


def _create_shareholder(client, headers, name="Founder Inc"):
    resp = client.post(f"{API}/admin/shareholders", headers=headers,
                       json={"name": name, "shareholder_type": "FOUNDER"})
    assert resp.status_code == 200, resp.text
    return resp.json()["shareholder_id"]


def _create_issuance(client, headers, shareholder_id, share_class_id, shares, issue_date):
    resp = client.post(f"{API}/admin/share-issuances", headers=headers, json={
        "shareholder_id": shareholder_id, "share_class_id": share_class_id,
        "shares": shares, "issue_date": issue_date.isoformat() if isinstance(issue_date, date) else issue_date,
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


def _create_pool(client, headers, total_shares, share_class_id=None, established_date="2020-01-01"):
    body = {"total_shares": total_shares, "established_date": established_date}
    if share_class_id is not None:
        body["share_class_id"] = share_class_id
    resp = client.post(f"{API}/admin/pools", headers=headers, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _snapshot(client, headers, as_of=None):
    params = {}
    if as_of is not None:
        params["as_of"] = as_of.isoformat() if isinstance(as_of, date) else as_of
    return client.get(f"{API}/admin/cap-table/snapshot", headers=headers, params=params)


# ===================================================================
# 1) דוגמה מחושבת-ביד: total_authorized_shares=10000, הנפקה יחידה של 6000,
# פול יחיד עם total_shares=1000.
# ===================================================================

def test_snapshot_worked_example_outstanding_and_fully_diluted(client, world):
    client.put(f"{API}/admin/company", headers=world.admin_a,
              json={"total_authorized_shares": 10000.0})

    share_class_id = _create_share_class(client, world.admin_a)
    shareholder_id = _create_shareholder(client, world.admin_a)
    _create_issuance(client, world.admin_a, shareholder_id, share_class_id, 6000.0, "2020-01-01")
    _create_pool(client, world.admin_a, total_shares=1000.0)

    response = _snapshot(client, world.admin_a)
    assert response.status_code == 200, response.text
    body = response.json()

    # חישוב ביד:
    #   outstanding_shares          = sum(ShareIssuance.shares)             = 6000
    #   fully_diluted_shares        = outstanding_shares + sum(pool.total_shares) = 6000 + 1000 = 7000
    #   outstanding_pct_of_authorized   = 6000 / 10000 = 0.6   (60%)
    #   fully_diluted_pct_of_authorized = 7000 / 10000 = 0.7   (70%)
    assert body["outstanding_shares"] == 6000.0
    assert body["fully_diluted_shares"] == 7000.0
    assert body["total_authorized_shares"] == 10000.0
    assert body["outstanding_pct_of_authorized"] == pytest.approx(0.6)
    assert body["fully_diluted_pct_of_authorized"] == pytest.approx(0.7)
    assert body["partial"] is False
    assert body["warnings"] == []


# ===================================================================
# 2) total_authorized_shares is None (ברירת המחדל) => שני האחוזים None,
# לעולם לא 0 - דפוס הכשל P4 שכבר תועד ב-QA-100-25..29.
# ===================================================================

def test_snapshot_percentages_are_none_not_zero_when_authorized_shares_unset(client, world):
    share_class_id = _create_share_class(client, world.admin_a)
    shareholder_id = _create_shareholder(client, world.admin_a)
    _create_issuance(client, world.admin_a, shareholder_id, share_class_id, 500.0, "2021-01-01")

    from backend.app.models import Company
    comp = world.db.get(Company, "COMP-CTS-A")
    assert comp.total_authorized_shares is None  # הנחת המוצא של הבדיקה

    response = _snapshot(client, world.admin_a)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["total_authorized_shares"] is None
    assert body["outstanding_pct_of_authorized"] is None
    assert body["fully_diluted_pct_of_authorized"] is None
    # המספרים המוחלטים ממשיכים להיות מחושבים כרגיל - רק האחוזים תלויי-תקרה.
    assert body["outstanding_shares"] == 500.0
    assert body["fully_diluted_shares"] == 500.0


# ===================================================================
# 3) פול עם share_class_id=None ("unassigned") - נשאר בחישוב fully_diluted
# ומופיע ברשימת ה-pools, לא נשמט.
# ===================================================================

def test_snapshot_includes_unassigned_pool_without_share_class(client, world):
    pool = _create_pool(client, world.admin_a, total_shares=2500.0, share_class_id=None)
    assert pool["share_class_id"] is None  # הנחת המוצא

    response = _snapshot(client, world.admin_a)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["outstanding_shares"] == 0.0
    assert body["fully_diluted_shares"] == 2500.0

    matching = [p for p in body["pools"] if p["pool_id"] == pool["pool_id"]]
    assert len(matching) == 1, f"הפול לא מופיע ברשימת ה-pools בכלל: {body['pools']}"
    assert matching[0]["share_class_id"] is None
    assert matching[0]["total_shares"] == 2500.0


# ===================================================================
# 4) as_of היסטורי לפני שלפול יש אירוע POOL_BALANCE_ESTABLISHED - פול שנכנס
# ישירות ל-DB בלי לעבור דרך POST /admin/pools (ולכן לא כתב שום אירוע ledger,
# בשונה מ-create_pool ב-grants.py שכן כותב POOL_BALANCE_ESTABLISHED). חייב
# להיות מוחרג מהחישוב, לא לקרוס (500) ולא להיחשב 0 שקרי.
# ===================================================================

def test_snapshot_historical_as_of_before_pool_ledger_baseline_is_partial_not_500(client, world):
    today = business_today()
    as_of = today - timedelta(days=30)  # בעבר => branch של project(), לא העמודה המוטטת
    issue_date = today - timedelta(days=60)  # לפני as_of, כדי שההנפקה תיכלל

    share_class_id = _create_share_class(client, world.admin_a)
    shareholder_id = _create_shareholder(client, world.admin_a)
    _create_issuance(client, world.admin_a, shareholder_id, share_class_id, 500.0, issue_date)

    # פול "יתום" - הוזרק ישירות ל-DB, בלי POST /admin/pools ובלי append_event/
    # record_ownership, כדי לשחזר במדויק "פול קיים בלי שום היסטוריית ledger".
    orphan_pool = OptionPool(
        company_id="COMP-CTS-A", total_shares=9999.0,
        allocated_shares=0.0, unallocated_shares=9999.0, share_class_id=None,
    )
    world.db.add(orphan_pool)
    world.db.flush()
    orphan_pool_id = orphan_pool.pool_id

    response = _snapshot(client, world.admin_a, as_of=as_of)
    assert response.status_code == 200, response.text  # לא 500 - זה הליבה של הבדיקה
    body = response.json()

    assert body["partial"] is True
    assert body["warnings"], "פול בלי היסטוריית ledger חייב להפיק warning"
    assert any(orphan_pool_id in w for w in body["warnings"]), body["warnings"]

    matching = [p for p in body["pools"] if p["pool_id"] == orphan_pool_id]
    assert len(matching) == 1
    assert matching[0]["total_shares"] is None, "לא 0 שקרי - הפול מסומן 'לא זמין'"

    # הפול היתום מוחרג מהסכום - fully_diluted שווה בדיוק ל-outstanding
    # (500), בלי תוספת כלשהי ובלי 0 מוסתר.
    assert body["outstanding_shares"] == 500.0
    assert body["fully_diluted_shares"] == 500.0


# ===================================================================
# 5) היקף חוצה-חברות: פולים/הנפקות של חברה B לא דולפים ל-snapshot של חברה A.
# ===================================================================

def test_snapshot_scoped_to_own_company_not_leaking_other_companys_data(client, world):
    # A: פול + הנפקה משלה.
    share_class_a = _create_share_class(client, world.admin_a)
    shareholder_a = _create_shareholder(client, world.admin_a, name="A Founder")
    _create_issuance(client, world.admin_a, shareholder_a, share_class_a, 300.0, "2022-01-01")
    pool_a = _create_pool(client, world.admin_a, total_shares=700.0)

    # B: פול + הנפקה גדולים בהרבה משלה - אם הם ידלפו ל-A, המספרים למטה יזוהו.
    share_class_b = _create_share_class(client, world.admin_b, name="B Common")
    shareholder_b = _create_shareholder(client, world.admin_b, name="B Founder")
    _create_issuance(client, world.admin_b, shareholder_b, share_class_b, 90_000.0, "2022-01-01")
    pool_b = _create_pool(client, world.admin_b, total_shares=50_000.0)

    response = _snapshot(client, world.admin_a)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["outstanding_shares"] == 300.0, "דליפה מחברה B ל-outstanding של A"
    assert body["fully_diluted_shares"] == 1000.0, "דליפה מחברה B ל-fully_diluted של A"

    pool_ids = {p["pool_id"] for p in body["pools"]}
    assert pool_ids == {pool_a["pool_id"]}, f"פול של B דלף לרשימת ה-pools של A: {pool_ids}"

    shareholder_ids = {row["shareholder_id"] for row in body["by_shareholder_and_class"]}
    assert shareholder_ids == {shareholder_a}, f"בעל-מניות של B דלף ל-breakdown של A: {shareholder_ids}"
    assert pool_b["pool_id"] not in pool_ids


# ===================================================================
# 6) as_of עתידי - מתקבל (לא נדחה), ומניב את אותם מספרים בדיוק כמו קריאה בלי
# as_of (שתיהן "מה שקיים כרגע" - ראו compute_cap_table_snapshot).
# ===================================================================

def test_snapshot_future_as_of_matches_no_as_of_given(client, world):
    share_class_id = _create_share_class(client, world.admin_a)
    shareholder_id = _create_shareholder(client, world.admin_a)
    _create_issuance(client, world.admin_a, shareholder_id, share_class_id, 1200.0, "2021-06-01")
    _create_pool(client, world.admin_a, total_shares=300.0)

    no_as_of = _snapshot(client, world.admin_a)
    assert no_as_of.status_code == 200, no_as_of.text

    future_date = business_today() + timedelta(days=365)
    with_future_as_of = _snapshot(client, world.admin_a, as_of=future_date)
    assert with_future_as_of.status_code == 200, with_future_as_of.text

    body_no_as_of = no_as_of.json()
    body_future = with_future_as_of.json()

    # as_of עצמו שונה בין השתי קריאות (זה בדיוק מה שהתבקש) - כל שאר השדות
    # חייבים להיות זהים.
    assert body_future["as_of"] == future_date.isoformat()
    del body_no_as_of["as_of"]
    del body_future["as_of"]
    assert body_no_as_of == body_future


# ===================================================================
# 7) תפקיד שאינו COMPANY_ADMIN - 403, כמו כל endpoint אחר בראוטר הזה.
# ===================================================================

def test_snapshot_requires_company_admin_role(client, world):
    response = _snapshot(client, world.emp_a)
    assert response.status_code == 403


# ===================================================================
# 8) total_authorized_shares=0.0 - ערך ממשי (לא None!) שאין עליו ולידציית
# positivity ב-CompanyUpdateRequest/PUT /admin/company, ולכן הגיע בפועל
# דרך ה-UI החדש של השלב הזה (שדה "סה"כ מניות מאושרות" בטאב company).
# נתפס בסקירה עצמאית: `is not None` בלבד לא מספיק - 0.0 עבר את הבדיקה
# והפיל ZeroDivisionError לא-מטופל (500). מבחינת דילול, 0 אינו מכנה תקין
# בדיוק כמו None - שני האחוזים חייבים להישאר None, לא לקרוס ולא 0%/100% שקריים.
# ===================================================================

def test_snapshot_zero_authorized_shares_does_not_crash_and_percentages_are_none(client, world):
    # סדר הפעולות תואם בדיוק את השחזור החי: מנפיקים כשאין עדיין תקרה
    # (create_share_issuance בודק את התקרה רק כש-total_authorized_shares
    # לא None - ראו cap_table.py - ולכן 500 מניות עוברות בלי בעיה כאן),
    # ורק אחר-כך האדמין קובע תקרה של 0 בטאב company. הפוך - קביעת 0 לפני
    # ההנפקה - הייתה נכשלת ב-400 "would exceed" כבר ב-create_share_issuance
    # (התנהגות תקינה וקיימת של שלב א: תקרה של 0 חוסמת כל הנפקה חדשה), ולא
    # הייתה בודקת את הבאג הזה בכלל.
    share_class_id = _create_share_class(client, world.admin_a)
    shareholder_id = _create_shareholder(client, world.admin_a)
    _create_issuance(client, world.admin_a, shareholder_id, share_class_id, 500.0, "2021-01-01")

    put_resp = client.put(f"{API}/admin/company", headers=world.admin_a,
                          json={"total_authorized_shares": 0.0})
    assert put_resp.status_code == 200, put_resp.text
    assert put_resp.json()["total_authorized_shares"] == 0.0  # הנחת המוצא: 0.0 ולא None

    response = _snapshot(client, world.admin_a)
    assert response.status_code == 200, response.text  # לא 500 - זה הליבה של הבדיקה
    body = response.json()

    assert body["total_authorized_shares"] == 0.0
    assert body["outstanding_pct_of_authorized"] is None
    assert body["fully_diluted_pct_of_authorized"] is None
    # המספרים המוחלטים ממשיכים להיות מחושבים כרגיל - רק האחוזים תלויי-מכנה-תקין.
    assert body["outstanding_shares"] == 500.0
