"""מרכז ההתראות - סקופ, מונה, סגירה והעדפות.

ה-endpoints האלה נכנסו ב-v0.5.0 step 2 בלי כיסוי אוטומטי, וה-UI של step 3 נשען
עליהם. הבדיקות כאן ממפות למזהי QA-050-01 עד QA-050-09 ב-``QA_TESTBOOK.md``.

הערה על תאריכים: ה-endpoints קוראים ``date.today()`` בעצמם, ולכן הדאטה כאן נבנה
*ביחס* להיום. כלל שדורש שליטה בתאריך נבדק דרך שכבת ה-service, שמקבלת ``today=``.
"""

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from backend.app.types import utcnow
from backend.app.auth import hash_password
from backend.app.models import (
    Company, Employee, EmployeeStatus, ExerciseRequest, ExerciseRequestStatus,
    Grant, GrantType, NotificationDismissal, OptionPool, Trustee, User, UserRole,
    UserSession, VestingSchedule, NOTIFICATION_DEFAULT_LEAD_DAYS,
)
from backend.app.services import notifications as notif

API = "/api/v1"
TODAY = date.today()
MAX_FEED_ITEMS = notif.MAX_FEED_ITEMS


def _months_ago(months: int) -> date:
    """יום בחודש נחתך ל-28 כדי שהבדיקות לא ייפלו על 29-31 בחודש קצר."""
    total = TODAY.month - 1 - months
    return date(TODAY.year + total // 12, total % 12 + 1, min(TODAY.day, 28))


def _user(db, user_id, role, **ids):
    pw_hash, salt = hash_password("Demo1234!")
    u = User(user_id=user_id, username=f"{user_id.lower()}@test.example",
             password_hash=pw_hash, password_salt=salt, role=role, is_active=True, **ids)
    db.add(u)
    db.flush()
    return u


def _token(db, user):
    token = f"tok-{user.user_id}"
    db.add(UserSession(token=token, user_id=user.user_id,
                       expires_at=utcnow() + timedelta(hours=1)))
    db.flush()
    return {"Authorization": f"Bearer {token}"}


def _fully_vested_grant(db, grant_id, employee_id, pool_id, months_since_start=24,
                        total_months=12, trustee_id=None, deposit=None):
    """מענק שהבשיל במלואו לפני זמן רב => מייצר FULLY_VESTED_UNEXERCISED
    (ברירת המחדל דורשת 90 ימים מההבשלה המלאה)."""
    db.add(Grant(grant_id=grant_id, employee_id=employee_id, pool_id=pool_id,
                 trustee_id=trustee_id, grant_date=_months_ago(months_since_start),
                 grant_type=GrantType.IL_102_CAPITAL_GAINS, total_options=1200.0,
                 exercise_price=1.0, post_termination_window_days=90,
                 trustee_deposit_date=deposit))
    db.add(VestingSchedule(schedule_id=f"S-{grant_id}", grant_id=grant_id,
                           start_date=_months_ago(months_since_start), cliff_months=0,
                           total_months=total_months, paused_days_total=0))


@pytest.fixture
def world(db_session):
    db = db_session
    db.add_all([
        Company(company_id="C-A", name="Alpha", country_code="IL"),
        Company(company_id="C-B", name="Beta", country_code="IL"),
    ])
    db.add_all([
        OptionPool(pool_id="P-A", company_id="C-A", total_shares=1000000.0,
                   allocated_shares=0.0, unallocated_shares=1000000.0),
        OptionPool(pool_id="P-B", company_id="C-B", total_shares=100000.0,
                   allocated_shares=0.0, unallocated_shares=100000.0),
    ])
    db.add_all([
        Trustee(trustee_id="T-1", company_id="C-A", name="Trustee One", registration_number="1"),
        Trustee(trustee_id="T-2", company_id="C-B", name="Trustee Two", registration_number="2"),
    ])
    db.add_all([
        Employee(employee_id="E-1", company_id="C-A", first_name="One", last_name="Emp",
                 email="e1@a.example", country_code="IL", status=EmployeeStatus.ACTIVE,
                 hire_date=date(2019, 1, 1), birth_date=date(1990, 1, 1)),
        Employee(employee_id="E-2", company_id="C-A", first_name="Two", last_name="Emp",
                 email="e2@a.example", country_code="IL", status=EmployeeStatus.ACTIVE,
                 hire_date=date(2019, 1, 1), birth_date=date(1990, 1, 1)),
        Employee(employee_id="E-3", company_id="C-B", first_name="Three", last_name="Emp",
                 email="e3@b.example", country_code="IL", status=EmployeeStatus.ACTIVE,
                 hire_date=date(2019, 1, 1), birth_date=date(1990, 1, 1)),
    ])
    db.flush()

    _fully_vested_grant(db, "G-1", "E-1", "P-A", trustee_id="T-1")
    _fully_vested_grant(db, "G-2", "E-2", "P-A", trustee_id="T-1")
    _fully_vested_grant(db, "G-3", "E-3", "P-B", trustee_id="T-2")
    db.flush()

    u1 = _user(db, "U-E1", UserRole.EMPLOYEE, employee_id="E-1")
    u2 = _user(db, "U-E2", UserRole.EMPLOYEE, employee_id="E-2")
    ua = _user(db, "U-ADM", UserRole.COMPANY_ADMIN, company_id="C-A")
    ut = _user(db, "U-TR1", UserRole.TRUSTEE, trustee_id="T-1")

    return SimpleNamespace(db=db, e1=_token(db, u1), e2=_token(db, u2),
                            admin=_token(db, ua), trustee=_token(db, ut),
                            u1=u1, ua=ua)


# ===================================================================
# QA-050-01 - סקופ
# ===================================================================

def test_employee_feed_contains_only_their_own_grants(client, world):
    feed = client.get(f"{API}/notifications", headers=world.e1).json()

    grant_ids = {i["entity_id"] for i in feed["items"] if i["entity_type"] == "Grant"}
    assert grant_ids == {"G-1"}, f"עובד ראה התראות על מענקים שאינם שלו: {grant_ids}"


def test_admin_feed_is_scoped_to_their_company(client, world):
    feed = client.get(f"{API}/notifications", headers=world.admin).json()

    grant_ids = {i["entity_id"] for i in feed["items"] if i["entity_type"] == "Grant"}
    assert grant_ids == {"G-1", "G-2"}, "אדמין קיבל מענקים מחוץ לחברה שלו"


def test_trustee_feed_is_scoped_to_their_trusteeship(client, world):
    feed = client.get(f"{API}/notifications", headers=world.trustee).json()

    grant_ids = {i["entity_id"] for i in feed["items"] if i["entity_type"] == "Grant"}
    assert "G-3" not in grant_ids, "נאמן קיבל מענק שאינו בנאמנות שלו"
    assert grant_ids == {"G-1", "G-2"}


# ===================================================================
# QA-050-02 - המונה מול הפיד הקטוע
# ===================================================================

def test_unread_count_matches_feed_total(client, world):
    feed = client.get(f"{API}/notifications", headers=world.e1).json()
    count = client.get(f"{API}/notifications/unread-count", headers=world.e1).json()

    assert count["count"] == feed["total"] == 1


def test_feed_is_capped_but_the_count_reports_the_real_number(client, world):
    """התקרה היא MAX_FEED_ITEMS. אם המונה היה סופר את הפיד, המשתמש היה רואה
    את התקרה כאילו היא העובדה - וזה בדיוק מה שהמונה הנפרד מונע."""
    extra = MAX_FEED_ITEMS + 5
    for n in range(extra):
        _fully_vested_grant(world.db, f"G-BULK-{n}", "E-1", "P-A")
    world.db.flush()

    feed = client.get(f"{API}/notifications", headers=world.e1).json()
    count = client.get(f"{API}/notifications/unread-count", headers=world.e1).json()

    assert len(feed["items"]) == MAX_FEED_ITEMS
    assert feed["total"] == extra + 1  # ה-bulk + G-1 המקורי
    assert count["count"] == feed["total"]


# ===================================================================
# QA-050-03 - סגירה אידמפוטנטית
# ===================================================================

def test_dismiss_removes_the_item_and_writes_exactly_one_row(client, world):
    feed = client.get(f"{API}/notifications", headers=world.e1).json()
    key = feed["items"][0]["key"]

    response = client.post(f"{API}/notifications/{key}/dismiss", headers=world.e1)

    assert response.status_code == 204
    rows = (world.db.query(NotificationDismissal)
            .filter(NotificationDismissal.user_id == world.u1.user_id,
                    NotificationDismissal.notification_key == key).count())
    assert rows == 1

    after = client.get(f"{API}/notifications", headers=world.e1).json()
    assert (after["total"], after["items"]) == (0, [])


def test_dismissing_the_same_key_twice_is_idempotent(client, world):
    """הסגירה נשענת על ה-unique index ברמת ה-DB ולא על check-then-insert
    (שהוא race שמייצר כפילויות בדיוק בלחיצה כפולה).

    *** הבדיקה לא נוגעת ב-ORM אחרי הקריאה השנייה בכוונה ***: הנתיב הזה מגיע
    ל-``db.rollback()`` בתוך ה-endpoint, וה-harness לא עוטף אותו ב-savepoint,
    ולכן ה-fixture כבר לא קיים בנקודה הזו. ראו ההערה ב-tests/conftest.py.
    """
    feed = client.get(f"{API}/notifications", headers=world.e1).json()
    key = feed["items"][0]["key"]

    first = client.post(f"{API}/notifications/{key}/dismiss", headers=world.e1)
    second = client.post(f"{API}/notifications/{key}/dismiss", headers=world.e1)

    assert (first.status_code, second.status_code) == (204, 204), "לחיצה כפולה החזירה שגיאה"


def test_dismissal_belongs_to_one_user_only(client, world):
    """סגירה של משתמש אחד לא מסתירה את ההתראה מהאדמין שרואה את אותו מענק."""
    feed = client.get(f"{API}/notifications", headers=world.e1).json()
    client.post(f"{API}/notifications/{feed['items'][0]['key']}/dismiss", headers=world.e1)

    admin_feed = client.get(f"{API}/notifications", headers=world.admin).json()
    assert "G-1" in {i["entity_id"] for i in admin_feed["items"]}


# ===================================================================
# QA-050-04 עד QA-050-07 - העדפות
# ===================================================================

def test_preferences_default_to_every_rule_enabled(client, world):
    body = client.get(f"{API}/notifications/preferences", headers=world.e1).json()

    prefs = {p["rule"]: p for p in body["preferences"]}
    assert set(prefs) == set(NOTIFICATION_DEFAULT_LEAD_DAYS)
    assert all(p["enabled"] for p in prefs.values()), "התראות אמורות להיות opt-out"
    for rule, days in NOTIFICATION_DEFAULT_LEAD_DAYS.items():
        assert prefs[rule]["lead_days"] == days


def test_unknown_rule_is_rejected(client, world):
    response = client.put(f"{API}/notifications/preferences", headers=world.e1, json={
        "preferences": [{"rule": "NOT_A_RULE", "enabled": True, "lead_days": 5}]})

    assert response.status_code == 400
    assert "Unknown notification rule" in response.json()["detail"]


def test_negative_lead_days_is_rejected(client, world):
    response = client.put(f"{API}/notifications/preferences", headers=world.e1, json={
        "preferences": [{"rule": "PTEW_CLOSING", "enabled": True, "lead_days": -1}]})

    assert response.status_code == 400
    assert "must not be negative" in response.json()["detail"]


def test_an_invalid_row_rejects_the_whole_payload(client, world):
    """הוולידציה עוברת על כל הרשימה *לפני* הכתיבה הראשונה. בלי זה, בקשה עם
    שורה תקינה ושורה פסולה הייתה נשמרת חלקית."""
    client.put(f"{API}/notifications/preferences", headers=world.e1, json={
        "preferences": [{"rule": "PTEW_CLOSING", "enabled": False, "lead_days": 3},
                        {"rule": "BAD_RULE", "enabled": True, "lead_days": 1}]})

    prefs = {p["rule"]: p for p in
             client.get(f"{API}/notifications/preferences", headers=world.e1).json()["preferences"]}
    assert prefs["PTEW_CLOSING"]["enabled"] is True, "שורה תקינה נשמרה למרות שהבקשה נדחתה"
    assert prefs["PTEW_CLOSING"]["lead_days"] == NOTIFICATION_DEFAULT_LEAD_DAYS["PTEW_CLOSING"]


def test_disabling_a_rule_removes_its_items_from_the_feed(client, world):
    assert client.get(f"{API}/notifications", headers=world.e1).json()["total"] == 1

    client.put(f"{API}/notifications/preferences", headers=world.e1, json={
        "preferences": [{"rule": "FULLY_VESTED_UNEXERCISED", "enabled": False, "lead_days": 90}]})

    assert client.get(f"{API}/notifications", headers=world.e1).json()["total"] == 0


def test_preferences_are_per_user(client, world):
    """שני משתמשים על אותו כלל לא דורסים זה את זה - ה-UniqueConstraint הוא על
    (user_id, rule) ולא על rule."""
    client.put(f"{API}/notifications/preferences", headers=world.e1, json={
        "preferences": [{"rule": "FULLY_VESTED_UNEXERCISED", "enabled": False, "lead_days": 90}]})

    other = {p["rule"]: p for p in
             client.get(f"{API}/notifications/preferences", headers=world.e2).json()["preferences"]}
    assert other["FULLY_VESTED_UNEXERCISED"]["enabled"] is True


# ===================================================================
# QA-050-08 / QA-050-09 - כלל ההבשלה, דרך ה-service (שליטה ב-today)
# ===================================================================

def test_vesting_event_near_fires_only_inside_the_lead_window(world):
    """מענק שמבשיל חודשית: ההתראה מופיעה רק כשהאירוע הבא בתוך lead_days.

    חישוב ידני: start = לפני 3 חודשים, 48 חודשי הבשלה. האירוע הבא הוא
    באותו יום-בחודש, כלומר בעוד ~30 יום. עם lead_days=1 אין התראה; עם
    lead_days=31 היא מופיעה.
    """
    db = world.db
    db.add(Grant(grant_id="G-VEST", employee_id="E-1", pool_id="P-A",
                 grant_date=_months_ago(3), grant_type=GrantType.IL_102_CAPITAL_GAINS,
                 total_options=4800.0, exercise_price=1.0, post_termination_window_days=90))
    db.add(VestingSchedule(schedule_id="S-G-VEST", grant_id="G-VEST",
                           start_date=_months_ago(3), cliff_months=0,
                           total_months=48, paused_days_total=0))
    db.flush()

    def rules_for(lead_days):
        feed = notif.for_employee(db, "E-1", world.u1.user_id, today=TODAY)
        return feed  # ברירות המחדל; הכיול נעשה למטה דרך הפונקציה הפרטית

    narrow = notif._rule_vesting_event_near(
        db.query(Grant).filter(Grant.grant_id == "G-VEST").first(), TODAY, 1)
    wide = notif._rule_vesting_event_near(
        db.query(Grant).filter(Grant.grant_id == "G-VEST").first(), TODAY, 31)

    assert narrow is None, "התראת הבשלה הופיעה כשהאירוע רחוק מחלון ההתרעה"
    assert wide is not None and wide.rule == "VESTING_EVENT_NEAR"
    assert wide.trigger_date > TODAY


def test_terminated_employee_gets_no_future_vesting_promise(world):
    """עובד שעזב: ההבשלה קפואה מיום העזיבה, ולכן אין אירוע הבשלה עתידי -
    גם עם חלון התרעה רחב. אחרת המערכת מבטיחה לו אופציות שלא יבשילו לעולם."""
    db = world.db
    leaver = Employee(employee_id="E-GONE", company_id="C-A", first_name="Gone",
                      last_name="Emp", email="gone@a.example", country_code="IL",
                      status=EmployeeStatus.TERMINATED, hire_date=date(2019, 1, 1),
                      termination_date=_months_ago(2), birth_date=date(1990, 1, 1))
    db.add(leaver)
    db.add(Grant(grant_id="G-GONE", employee_id="E-GONE", pool_id="P-A",
                 grant_date=_months_ago(6), grant_type=GrantType.IL_102_CAPITAL_GAINS,
                 total_options=4800.0, exercise_price=1.0, post_termination_window_days=90))
    db.add(VestingSchedule(schedule_id="S-G-GONE", grant_id="G-GONE",
                           start_date=_months_ago(6), cliff_months=0,
                           total_months=48, paused_days_total=0))
    db.flush()

    item = notif._rule_vesting_event_near(
        db.query(Grant).filter(Grant.grant_id == "G-GONE").first(), TODAY, 60)

    assert item is None


def test_pending_request_notification_respects_the_waiting_threshold(world):
    """REQUEST_PENDING_TOO_LONG הוא הכלל היחיד שנמדד *אחרי* האירוע ולא לפניו -
    lead_days כאן הוא סף המתנה, לא התרעה מוקדמת."""
    db = world.db
    req = ExerciseRequest(request_id="R-WAIT", grant_id="G-1", employee_id="E-1",
                          options_requested=100.0, status=ExerciseRequestStatus.PENDING,
                          requested_at=utcnow() - timedelta(days=10))
    db.add(req)
    db.flush()

    assert notif._rule_request_pending_too_long(req, TODAY, 7) is not None
    assert notif._rule_request_pending_too_long(req, TODAY, 30) is None
