"""ExerciseTaxRecord - הרשומה הבודדת שמחשבת ומתעדת מס על מימוש **אמיתי** (v0.9.1
שלב ב), לא סימולציה. עד לתיקון הזה _decide_exercise_request מעולם לא קרא
ל-TaxCalculationEngine בכלל - הבדיקות כאן היו נכשלות מול הקוד הישן, לא רק
עוברות בטעות (ראו CLAUDE.md: "בדיקה שירוקה מול הבאג שהיא נכתבה כדי לתפוס").

מיפוי ל-PLAN.md §6, שורה "2. Durable per-exercise tax record at approval".
"""

from datetime import date, timedelta

import pytest

from backend.app.types import utcnow
from backend.app.auth import hash_password
from backend.app.models import (
    Company, Employee, EmployeeStatus, ExerciseRequest, ExerciseRequestStatus,
    ExerciseTaxRecord, Grant, GrantType, OptionPool, TaxRatesHistory, TaxRulePack,
    User, UserRole, UserSession, VestingSchedule,
)

API = "/api/v1"
TODAY = date.today()
SRC = "https://test.invalid/qa-fixture-not-a-real-tax-source"


def _months_ago(months: int) -> date:
    total = TODAY.month - 1 - months
    return date(TODAY.year + total // 12, total % 12 + 1, min(TODAY.day, 28))


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
    """מענק ללא נאמן (מדלג על בדיקת חסימת נאמנות - לא רלוונטית לבדיקה הזו),
    13 חודשי הבשלה (cliff=12) => 1300 מתוך 4800 הבשילו, בקשה על 500 בטוחה."""
    db = db_session
    db.add(Company(company_id="COMP-T", name="TaxCo", country_code="IL"))
    db.add(OptionPool(pool_id="POOL-T", company_id="COMP-T", total_shares=100000.0,
                      allocated_shares=4800.0, unallocated_shares=95200.0))
    db.add(Employee(employee_id="EMP-T", company_id="COMP-T", first_name="Tal", last_name="Mor",
                    email="tal@taxco.example", country_code="IL", status=EmployeeStatus.ACTIVE,
                    hire_date=date(2020, 1, 1), birth_date=date(1990, 1, 1)))
    db.add(Grant(grant_id="GRANT-T", employee_id="EMP-T", pool_id="POOL-T",
                grant_date=_months_ago(13), grant_type=GrantType.IL_102_CAPITAL_GAINS,
                total_options=4800.0, exercise_price=1.0, post_termination_window_days=90))
    db.add(VestingSchedule(schedule_id="SCHED-T", grant_id="GRANT-T",
                           start_date=_months_ago(13), cliff_months=12,
                           total_months=48, paused_days_total=0))
    db.flush()

    admin = _user(db, "U-ADMIN-T", UserRole.COMPANY_ADMIN, company_id="COMP-T")
    from types import SimpleNamespace
    return SimpleNamespace(db=db, admin=_token(db, admin))


def _add_flat_pack(db, effective_start_date: date, rate: float) -> str:
    pack = TaxRulePack(country_code="IL", grant_type=GrantType.IL_102_CAPITAL_GAINS.value,
                       effective_start_date=effective_start_date, calculation_method="FLAT_RATE",
                       official_source_url=SRC)
    db.add(pack)
    db.flush()
    db.add(TaxRatesHistory(country_code="IL", grant_type=GrantType.IL_102_CAPITAL_GAINS.value,
                           effective_start_date=effective_start_date, capital_gains_rate=rate,
                           official_source_url=SRC, pack_id=pack.pack_id))
    db.flush()
    return pack.pack_id


def _pending_request(db, request_id: str, options: float, requested_at) -> ExerciseRequest:
    req = ExerciseRequest(request_id=request_id, grant_id="GRANT-T", employee_id="EMP-T",
                          options_requested=options, status=ExerciseRequestStatus.PENDING,
                          requested_at=requested_at)
    db.add(req)
    db.flush()
    return req


def test_approving_an_exercise_request_writes_an_exercise_tax_record(client, world):
    """הליבה של הפער: לפני התיקון, _decide_exercise_request לא קרא ל-
    TaxCalculationEngine כלל - השורה הזו לא הייתה קיימת אחרי אישור."""
    _add_flat_pack(world.db, date(2000, 1, 1), 0.25)
    _pending_request(world.db, "REQ-T1", 500.0, utcnow())

    response = client.patch(f"{API}/admin/exercise-requests/REQ-T1",
                            headers=world.admin, json={"approve": True})
    assert response.status_code == 200, response.text

    records = world.db.query(ExerciseTaxRecord).filter(
        ExerciseTaxRecord.request_id == "REQ-T1").all()
    assert len(records) == 1, "רשומת מס אחת בדיוק אמורה להיווצר על אישור אמיתי"

    record = records[0]
    # gain = (stock_price - exercise_price) * options; בלי StockPricesHistory
    # stock_price נופל למחיר המימוש עצמו => gain=0, tax_amount=0 - עדיין רשומה
    # מבנית, לא "אין רשומה כי אין רווח".
    assert record.gain == 0.0
    assert record.tax_amount == 0.0
    assert record.calculation_method == "FLAT_RATE"
    assert record.country_code == "IL"
    assert record.grant_type == "IL_102_CAPITAL_GAINS"
    assert record.official_source_url == SRC


def test_exercise_tax_record_has_no_pack_id_column(world):
    """שומר את ההכרעה: pack_id מתחדש בכל seed/backfill ולא שורד בין שני מופעי
    DB (ראו tax-domain-expert בתכנון) - הזהות היחידה שנשמרת היא המפתח הטבעי."""
    assert not hasattr(ExerciseTaxRecord, "pack_id"), (
        "ExerciseTaxRecord לא אמור לשמור pack_id - הזהות היא "
        "(country_code, grant_type, effective_start_date), לא UUID שמתחדש בכל seed"
    )


def test_tax_amount_reflects_the_requests_date_not_the_approval_day(client, world):
    """שני עידונים של חבילת מס: 25% מ-2000, 40% מ-2030. הבקשה הוגשה ב-2010
    (לפני העידכון), אבל האישור קורה "היום" (אחרי 2030 לוגית לצורך הבדיקה, כי
    TODAY האמיתי כבר עבר את זה) - התאריך שקובע חייב להיות תאריך הבקשה, לא
    תאריך האישור. זו הבדיקה שהייתה נכשלת מול הניסוח הראשון (business_today()
    בזמן אישור), ותפסה את ההתנגשות עם test_the_clock_is_never_the_source_of_a_tax_date."""
    _add_flat_pack(world.db, date(2000, 1, 1), 0.25)
    _add_flat_pack(world.db, date(2030, 1, 1), 0.40)

    # אותה חברה כמו world (COMP-T/POOL-T) - כדי ש-world.admin יהיה מורשה לאשר,
    # בלי לבנות עוד admin/token רק בשביל בדיקת בחירת תאריך.
    world.db.add(Employee(employee_id="EMP-T2", company_id="COMP-T", first_name="Old", last_name="Req",
                          email="old@taxco.example", country_code="IL", status=EmployeeStatus.ACTIVE,
                          hire_date=date(2005, 1, 1), birth_date=date(1980, 1, 1)))
    world.db.add(Grant(grant_id="GRANT-T2", employee_id="EMP-T2", pool_id="POOL-T",
                       grant_date=date(2008, 1, 1), grant_type=GrantType.IL_102_CAPITAL_GAINS,
                       total_options=1000.0, exercise_price=1.0, post_termination_window_days=90))
    world.db.add(VestingSchedule(schedule_id="SCHED-T2", grant_id="GRANT-T2",
                                 start_date=date(2008, 1, 1), cliff_months=12,
                                 total_months=48, paused_days_total=0))
    world.db.flush()

    from datetime import datetime, timezone
    req = ExerciseRequest(request_id="REQ-T2", grant_id="GRANT-T2", employee_id="EMP-T2",
                          options_requested=100.0, status=ExerciseRequestStatus.PENDING,
                          requested_at=datetime(2010, 6, 1, tzinfo=timezone.utc))
    world.db.add(req)
    world.db.flush()

    response = client.patch(f"{API}/admin/exercise-requests/REQ-T2",
                            headers=world.admin, json={"approve": True})
    assert response.status_code == 200, response.text

    record = world.db.query(ExerciseTaxRecord).filter(
        ExerciseTaxRecord.request_id == "REQ-T2").one()
    assert record.effective_start_date == date(2000, 1, 1), (
        "תאריך הבקשה (2010) חל על חבילת 2000, לא על חבילת 2030 - "
        f"אבל נבחרה חבילה מ-{record.effective_start_date}"
    )
    assert record.effective_rate == 0.25
