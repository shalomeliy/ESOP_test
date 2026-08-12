"""חסימת נאמנות של סעיף 102 - שנתיים מיום ההפקדה אצל הנאמן.

הקצה המסוכן: היום שלפני תום התקופה מול היום עצמו. אישור מימוש יום אחד מוקדם
מדי מפיל את המענק ממסלול רווח הון למסלול הכנסת עבודה - הפרש מס אמיתי.
"""

from datetime import date, timedelta

import pytest

from backend.app.services.engine import DeterministicESOPEngine

DEPOSIT = date(2023, 6, 15)
EXPECTED_END = date(2025, 6, 15)  # deposit.year + 2, אותו יום ואותו חודש


@pytest.fixture
def deposited_grant(make_grant):
    return make_grant(trustee_deposit_date=DEPOSIT)


def test_day_before_holding_period_ends_is_not_met(deposited_grant):
    """2025-06-14 = יום אחד לפני תום השנתיים -> עדיין חסום, והדדליין המוחזר
    הוא 2025-06-15 (כדי שה-UI יוכל להציג ממתי מותר)."""
    met, end_date = DeterministicESOPEngine.check_trustee_holding_period(
        deposited_grant, date(2025, 6, 14)
    )
    assert (met, end_date) == (False, EXPECTED_END)


def test_exact_end_date_is_met(deposited_grant):
    """2025-06-15 עצמו - התנאי הוא check_date >= end_date, כלומר היום עצמו כבר מותר."""
    met, end_date = DeterministicESOPEngine.check_trustee_holding_period(
        deposited_grant, date(2025, 6, 15)
    )
    assert met is True
    assert end_date == EXPECTED_END


def test_no_deposit_means_not_met_and_no_real_deadline(make_grant):
    """מענק שמעולם לא הופקד אצל נאמן: החסימה לא התקיימה, והתאריך המוחזר הוא
    check_date עצמו (אין תאריך סיום אמיתי לחשב ממנו)."""
    check_date = date(2026, 1, 1)
    met, returned = DeterministicESOPEngine.check_trustee_holding_period(
        make_grant(trustee_deposit_date=None), check_date
    )
    assert (met, returned) == (False, check_date)

# ===================================================================
# העוגן הסטטוטורי (v0.9.1). אומת 09/08/2026 מול שכפול הפקודה ושלושה מקורות
# מקצועיים - לא מול נוסח ראשוני. ראו הדוקסטרינג ב-engine.py.
# ===================================================================

def test_the_anchor_is_the_later_of_grant_and_deposit(make_grant):
    """102(א): "24 חודשים מיום שבו הוקצו המניות **והופקדו** בידי נאמן" - היום
    שבו התקיימו שני התנאים, ולא המוקדם מביניהם.

    ה-API אוסר היום הפקדה לפני ההענקה, ולכן הבדיקה בונה את המענק ישירות: היא
    מגינה על *הכלל המסי*, שאינו תלוי בכלל הקלט שבמקרה מסתיר אותו. ספירה מ-
    grant_date לבדו הייתה מסיימת ב-2024-01-01, כלומר מאשרת מסלול רווח הון
    כשנה וחצי מוקדם מדי - ולפי 102(ב)(4) זה מסווג את מלוא ההטבה כהכנסת עבודה.
    """
    grant = make_grant(grant_date=date(2022, 1, 1), trustee_deposit_date=date(2023, 6, 15))

    met, end_date = DeterministicESOPEngine.check_trustee_holding_period(grant, date(2024, 1, 2))

    assert end_date == date(2025, 6, 15)
    assert met is False, "ההפקדה המאוחרת היא שקובעת, ולכן ב-2024 התקופה טרם תמה"


def test_a_deposit_recorded_before_the_grant_does_not_shorten_the_period(make_grant):
    """נתונים היסטוריים מלפני תיקון ה-backdating של v0.6.0 יכולים להכיל
    הפקדה שקדמה להענקה. ``max()`` מחזיק גם שם: העוגן נשאר ההענקה, ולא
    מתקצרת התקופה בזכות רשומה שגויה."""
    grant = make_grant(grant_date=date(2023, 6, 15), trustee_deposit_date=date(2022, 1, 1))

    _, end_date = DeterministicESOPEngine.check_trustee_holding_period(grant, date(2024, 1, 1))

    assert end_date == date(2025, 6, 15)


# ===================================================================
# v1.0.1 (debt item 2) - "היום" הכוזב ב-holding_period_end_date. המנוע עצמו
# (למעלה) מחזיר בכוונה (False, check_date) כשאין הפקדה - זה החוזה הנכון של
# check_trustee_holding_period. הבאג היה בשלושת ה-endpoints שהעבירו את
# check_date הזה כמו שהוא ל-response, שם הוא נקרא כ"התקופה מסתיימת היום" -
# שקרי על מסמך מס. _trustee_holding_status (exercise_requests.py) הוא
# ה-helper המשותף החדש שסוגר את זה בנקודת כניסה אחת. הבדיקות כאן הן
# ברמת ה-HTTP - שלושת ה-call sites בפועל, לא המנוע (שכבר מכוסה למעלה).
# ===================================================================

from types import SimpleNamespace as _SimpleNamespace

from backend.app.auth import hash_password
from backend.app.models import (
    Company, Employee, EmployeeStatus, ExerciseRequest, ExerciseRequestStatus,
    Grant, GrantType, OptionPool, TaxRatesHistory, TaxRulePack, Trustee, User,
    UserRole, UserSession, VestingSchedule,
)
from backend.app.types import utcnow

API = "/api/v1"
_TAX_SRC = "https://test.invalid/qa-fixture-not-a-real-tax-source"


def _thp_months_ago(months: int) -> date:
    today = date.today()
    total = today.month - 1 - months
    return date(today.year + total // 12, total % 12 + 1, min(today.day, 28))


def _thp_token(db, user: User) -> dict:
    token = f"tok-{user.user_id}"
    db.add(UserSession(token=token, user_id=user.user_id, expires_at=utcnow() + timedelta(hours=1)))
    db.flush()
    return {"Authorization": f"Bearer {token}"}


def _thp_user(db, user_id: str, role: UserRole, **ids) -> User:
    pw_hash, salt = hash_password("Demo1234!")
    u = User(user_id=user_id, username=f"{user_id.lower()}@test.example",
             password_hash=pw_hash, password_salt=salt, role=role, is_active=True, **ids)
    db.add(u)
    db.flush()
    return u


@pytest.fixture
def thp_world(db_session):
    """שלושה מענקים תחת נאמן אחד: בלי הפקדה בכלל (הבאג), הפקדה מספיק ישנה
    (met=True), והפקדה טרייה (met=False, אבל עדיין עם תאריך יעד אמיתי -
    לא None). כולם עם לוח הבשלה ותיק כדי שגם ה-vested לא ייחסם."""
    db = db_session
    db.add(Company(company_id="C-THP", name="TrusteeHP Ltd", country_code="IL"))
    db.flush()
    db.add(OptionPool(pool_id="P-THP", company_id="C-THP", total_shares=10000.0,
                      allocated_shares=3000.0, unallocated_shares=7000.0))
    db.add(Trustee(trustee_id="T-THP", company_id="C-THP", name="Trustee Ltd", registration_number="1"))
    db.add(Employee(employee_id="E-THP", company_id="C-THP", first_name="Yossi", last_name="Cohen",
                    email="thp@alpha.example", country_code="IL", status=EmployeeStatus.ACTIVE,
                    hire_date=date(2015, 1, 1), birth_date=date(1990, 1, 1)))
    db.flush()

    grant_date = _thp_months_ago(30)
    db.add_all([
        Grant(grant_id="G-THP-NODEP", employee_id="E-THP", pool_id="P-THP", trustee_id="T-THP",
              grant_date=grant_date, grant_type=GrantType.IL_102_CAPITAL_GAINS,
              total_options=1000.0, exercise_price=1.0, currency="USD",
              trustee_deposit_date=None, post_termination_window_days=90),
        Grant(grant_id="G-THP-MET", employee_id="E-THP", pool_id="P-THP", trustee_id="T-THP",
              grant_date=grant_date, grant_type=GrantType.IL_102_CAPITAL_GAINS,
              total_options=1000.0, exercise_price=1.0, currency="USD",
              trustee_deposit_date=_thp_months_ago(28), post_termination_window_days=90),
        Grant(grant_id="G-THP-UNMET", employee_id="E-THP", pool_id="P-THP", trustee_id="T-THP",
              grant_date=grant_date, grant_type=GrantType.IL_102_CAPITAL_GAINS,
              total_options=1000.0, exercise_price=1.0, currency="USD",
              trustee_deposit_date=_thp_months_ago(3), post_termination_window_days=90),
    ])
    for grant_id in ("G-THP-NODEP", "G-THP-MET", "G-THP-UNMET"):
        db.add(VestingSchedule(schedule_id=f"S-{grant_id}", grant_id=grant_id, start_date=grant_date,
                               cliff_months=12, total_months=48, paused_days_total=0))

    # v0.9.1 שלב ב: אישור/סימולציה אמיתיים מחשבים מס - בלי חבילה זו, כל אישור/
    # סימולציה כאן היה נחסם ב-409 (MissingTaxRuleError) לפני שמגיע לבדיקה שהוא
    # בפועל אמור לתפוס (ראו test_authorization_and_approvals.py, אותו דפוס).
    tax_pack = TaxRulePack(country_code="IL", grant_type=GrantType.IL_102_CAPITAL_GAINS.value,
                           effective_start_date=date(2000, 1, 1), calculation_method="FLAT_RATE",
                           official_source_url=_TAX_SRC)
    db.add(tax_pack)
    db.flush()
    db.add(TaxRatesHistory(country_code="IL", grant_type=GrantType.IL_102_CAPITAL_GAINS.value,
                           effective_start_date=date(2000, 1, 1), capital_gains_rate=0.25,
                           official_source_url=_TAX_SRC, pack_id=tax_pack.pack_id))
    db.flush()

    admin = _thp_user(db, "U-THP-ADMIN", UserRole.COMPANY_ADMIN, company_id="C-THP")
    employee = _thp_user(db, "U-THP-EMP", UserRole.EMPLOYEE, employee_id="E-THP")
    trustee = _thp_user(db, "U-THP-TRUSTEE", UserRole.TRUSTEE, trustee_id="T-THP")

    return _SimpleNamespace(db=db, admin=_thp_token(db, admin), employee=_thp_token(db, employee),
                            trustee=_thp_token(db, trustee))


def test_trustee_portfolio_with_no_deposit_reports_null_holding_period_end_date(client, thp_world):
    response = client.get(f"{API}/trustee/portfolio", headers=thp_world.trustee)
    assert response.status_code == 200
    item = next(g for g in response.json() if g["grant_id"] == "G-THP-NODEP")
    assert item["holding_period_end_date"] is None
    assert item["is_trustee_holding_period_met"] is False


def test_trustee_portfolio_with_a_deposit_reports_a_real_end_date_met_and_unmet(client, thp_world):
    response = client.get(f"{API}/trustee/portfolio", headers=thp_world.trustee)
    assert response.status_code == 200
    by_id = {g["grant_id"]: g for g in response.json()}

    met = by_id["G-THP-MET"]
    assert met["is_trustee_holding_period_met"] is True
    assert met["holding_period_end_date"] is not None
    # end_date הוא deposit + 24 חודשים קלנדריים (max(grant,deposit)=deposit כאן) - תאריך אמיתי, לא None.
    expected_met_end = date(_thp_months_ago(28).year + 2, _thp_months_ago(28).month, _thp_months_ago(28).day)
    assert met["holding_period_end_date"] == str(expected_met_end)

    unmet = by_id["G-THP-UNMET"]
    assert unmet["is_trustee_holding_period_met"] is False
    assert unmet["holding_period_end_date"] is not None
    expected_unmet_end = date(_thp_months_ago(3).year + 2, _thp_months_ago(3).month, _thp_months_ago(3).day)
    assert unmet["holding_period_end_date"] == str(expected_unmet_end)


def test_employee_dashboard_with_no_deposit_reports_null_holding_period_end_date(client, thp_world):
    response = client.get(f"{API}/employee/dashboard/E-THP", headers=thp_world.employee)
    assert response.status_code == 200
    grant = next(g for g in response.json()["grants"] if g["grant_id"] == "G-THP-NODEP")
    assert grant["holding_period_end_date"] is None, (
        "לא None, ולא המחרוזת 'None', ולא תאריך היום - ראו employee_dashboard.py")
    assert grant["is_trustee_holding_period_met"] is False


def test_simulate_exercise_with_no_deposit_returns_200_with_null_holding_period_end_date(client, thp_world):
    response = client.post(f"{API}/employee/simulate-exercise", headers=thp_world.employee, json={
        "grant_id": "G-THP-NODEP", "exercise_date": str(date.today()), "options_to_exercise": 10,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["holding_period_end_date"] is None
    assert body["is_trustee_holding_period_met"] is False


def test_approving_with_no_deposit_is_blocked_without_claiming_todays_date_as_the_deadline(client, thp_world):
    thp_world.db.add(ExerciseRequest(request_id="REQ-THP-NODEP", grant_id="G-THP-NODEP",
                                     employee_id="E-THP", options_requested=10.0,
                                     status=ExerciseRequestStatus.PENDING, requested_at=utcnow()))
    thp_world.db.flush()

    response = client.patch(f"{API}/admin/exercise-requests/REQ-THP-NODEP",
                            headers=thp_world.admin, json={"approve": True})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "holding period" in detail.lower()
    # לפני התיקון end_date היה business_today() - הדדליין המדווח היה "היום",
    # שקרי (משתמע שמותר מחר, אבל האמת היא שאין שום דדליין ידוע כלל).
    assert str(date.today()) not in detail
    assert "deposit" in detail.lower()
