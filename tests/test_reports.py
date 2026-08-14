"""דוחות, ייצוא ו-BI (v1.1.0) - שנים-עשר ה-endpoints של backend/app/api/reports.py.

מיפוי ל-docs/qa/v1.1.0.md: QA-110-01 עד QA-110-33.

מה הקובץ הזה מגן עליו, לפי סדר חשיבות:

1. **בידוד חוצה-חברות על כל 12 ה-endpoints.** אף אחת מ-Grant/VestingSchedule/
   ExerciseRequest/AuditLog/LedgerEvent אין לה עמודת company_id ישירה, ולכן כל
   דוח *חייב* לצרוך CompanyScope ולא לשחזר סינון עצמאי. זה בדיוק הדפוס שיצר
   IDOR אמיתי ב-create_shareholder (v1.0.0) ובדפוס P2 של QA_TESTBOOK.
2. **הוצאת תגמול הוני** - הכלל ה"קשה" מהתכנון: FMV נלקח מהשורה **הקודמת הקרובה
   ביותר** ב-StockPricesHistory (price_date <= grant_date), לעולם לא מאוחרת
   (look-ahead bias), לעולם לא נפילה חזרה ל-exercise_price. שלוש סיבות ההחרגה
   נשארות מבחינות זו-מזו, ומענק מוחרג תורם ``None`` ולא ``0.0`` (דפוס P4).
3. **ASC 718 = checklist בלי אף מספר כספי** - אילוץ ציות, לא העדפת סגנון.
   נעול מבנית (כל ערך בכל שורה הוא bool/str, לעולם לא מספר) כדי שהוספת שדה
   כספי בעתיד תיפול כאן ולא תעבור בשקט לדוח שמוגש לרואה-חשבון.
4. **דוח התנועה חייב להצהיר במפורש שאין מעקב נאמנים** - ``summary.trustees ==
   {tracked: False, message: ...}``, לא חלק ריק ולא חלק מושמט (אותה משמעת של
   "לא זמין" מול "0 שקרי").
5. **audit על כל בקשת דוח** - זהו נתיב bulk-egress חדש ל-PII/כסף שלא היה
   מתועד בכלל לפני v1.1.0, ו-notes לעולם לא מכיל PII גולמי.

הפיקסצ'ר בונה שלוש חברות עם דאטה *חופפת* (אותם תאריכים, אותם סטטוסים, מספרים
שונים בסדר גודל) - חברה B ריקה לא הייתה מוכיחה בידוד, רק היקף ריק. המזהים
נבחרו בלי חפיפת-תת-מחרוזת בין A ל-B בכוונה, כדי ש-``assert token not in
json.dumps(body)`` יהיה בדיקת דליפה אמיתית ולא כמעט-התאמה.
"""

import csv
import io
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.app.auth import hash_password
from backend.app.models import (
    AuditLog, Company, Employee, EmployeeStatus, ExerciseRequest, ExerciseRequestStatus,
    Grant, GrantType, LEDGER_SOURCE_LIVE, LedgerEvent, LedgerOwnership, OptionPool,
    SAVED_REPORT_TYPES,
    SavedReport, ShareClass, Shareholder, StockPricesHistory, Trustee, User, UserRole,
    UserSession, VestingSchedule,
)
from backend.app.types import utcnow

API = "/api/v1"
REPORTS = f"{API}/admin/reports"

# טווח "כל הזמנים" לדוחות שמקבלים תאריכים - כדי שבדיקת בידוד לא תיכשל/תעבור
# בגלל גבול תאריך במקום בגלל היקף.
ALL_TIME = {"date_from": "2000-01-01", "date_to": "2099-12-31"}

# שמונת ה-endpoints שמחזירים דוח (הארבעה של saved נבדקים בנפרד - CRUD, לא דוח).
REPORT_ENDPOINTS = {
    "pool-status": {},
    "trustee-exposure": {},
    "deadline-risk": {},
    "exercise-activity": dict(ALL_TIME),
    "compensation-expense": {},
    "movement": dict(ALL_TIME),
    "asc718-readiness": {},
    "dashboard": {},
}

# טביעות אצבע של כל חברה: הסיומת ``-RPT-A``/``-RPT-B`` משותפת לכל מזהה של
# אותה חברה (COMP/POOL/TRU/EMP/GRANT/REQ/SH), ואף אחת מהן אינה תת-מחרוזת של
# האחרת - כלומר ``token in json.dumps(body)`` הוא בדיקת דליפה אמיתית ולא
# כמעט-התאמה. שמות/אימיילים נוספים במפורש: דליפת PII לא תמיד נושאת מזהה.
B_TOKENS = ("-RPT-B", "Trustee Beta", "Beta Founder", "Rivka Levi", "b1@beta.example")
A_TOKENS = ("-RPT-A", "Trustee Alpha", "Alpha Founder", "Dana Bar", "a1@alpha.example")


def _user(db, user_id: str, role: UserRole, **ids) -> User:
    pw_hash, salt = hash_password("Demo1234!")
    u = User(user_id=user_id, username=f"{user_id.lower()}@test.example",
             password_hash=pw_hash, password_salt=salt, role=role, is_active=True, **ids)
    db.add(u)
    db.flush()
    return u


def _token(db, user: User) -> dict:
    token = f"tok-{user.user_id}"
    db.add(UserSession(token=token, user_id=user.user_id, expires_at=utcnow() + timedelta(hours=1)))
    db.flush()
    return {"Authorization": f"Bearer {token}"}


def _at(d: date) -> datetime:
    """12:00 UTC - ליד באמצע היום בכל אזור זמן, כדי ש-business_date_of יחזיר את
    אותו יום קלנדרי ולא את היום שלפניו (אותה סוגיה כמו ח1/ח2)."""
    return datetime(d.year, d.month, d.day, 12, 0, tzinfo=timezone.utc)


def _ledger_row(db, aggregate_type, aggregate_id, company_id, event_type, effective_date, seq=1):
    """אירוע ledger + שורת בעלות - הזוג שדוח התנועה מסתמך עליו (הבעלות היא מה
    שמכניס את האירוע להיקף החברה; ledger_events עצמה חסרת company_id)."""
    db.add(LedgerOwnership(aggregate_id=aggregate_id, aggregate_type=aggregate_type,
                           company_id=company_id))
    db.add(LedgerEvent(event_type=event_type, aggregate_type=aggregate_type,
                       aggregate_id=aggregate_id, payload="{}",
                       effective_date=effective_date, recorded_at=_at(effective_date),
                       sequence_no=seq, schema_version=1, source=LEDGER_SOURCE_LIVE))


@pytest.fixture
def world(db_session):
    """שלוש חברות:

    A - החברה הנבדקת. שני מענקים, שני נאמנים (אחד בלי מענקים), שתי בקשות
        מימוש, שלוש שורות מחיר מניה, אירוע ledger, ורשומת ביקורת על Shareholder.
    B - דאטה *חופפת* (אותם תאריכים, אותם סטטוסים) במספרים גדולים בסדר גודל -
        כל דליפה תזוהה מיד במספרים, לא רק במזהים.
    C - חברה עם מענק אבל **בלי אף שורת מחיר** - המקרה הנקי של NO_PRICE_DATA.
    """
    db = db_session
    db.add_all([
        Company(company_id="COMP-RPT-A", name="Alpha Ltd", country_code="IL"),
        Company(company_id="COMP-RPT-B", name="Beta Ltd", country_code="IL"),
        Company(company_id="COMP-RPT-C", name="Gamma Ltd", country_code="IL"),
    ])
    db.add_all([
        OptionPool(pool_id="POOL-RPT-A", company_id="COMP-RPT-A", total_shares=100_000.0,
                   allocated_shares=6_000.0, unallocated_shares=94_000.0),
        OptionPool(pool_id="POOL-RPT-B", company_id="COMP-RPT-B", total_shares=500_000.0,
                   allocated_shares=9_000.0, unallocated_shares=491_000.0),
        OptionPool(pool_id="POOL-RPT-C", company_id="COMP-RPT-C", total_shares=1_000.0,
                   allocated_shares=300.0, unallocated_shares=700.0),
    ])
    db.add_all([
        Trustee(trustee_id="TRU-RPT-A1", company_id="COMP-RPT-A",
                name="Trustee Alpha", registration_number="A-111"),
        # נאמן בלי אף מענק - חייב להופיע בדוח כשורה עם אפסים, לא להישמט.
        Trustee(trustee_id="TRU-RPT-A2", company_id="COMP-RPT-A",
                name="Trustee Alpha Idle", registration_number="A-222"),
        Trustee(trustee_id="TRU-RPT-B1", company_id="COMP-RPT-B",
                name="Trustee Beta", registration_number="B-111"),
    ])
    db.add_all([
        Employee(employee_id="EMP-RPT-A1", company_id="COMP-RPT-A", first_name="Dana",
                 last_name="Bar", email="a1@alpha.example", country_code="IL",
                 status=EmployeeStatus.ACTIVE, hire_date=date(2019, 1, 1)),
        Employee(employee_id="EMP-RPT-A2", company_id="COMP-RPT-A", first_name="Noa",
                 last_name="Gal", email="a2@alpha.example", country_code="IL",
                 status=EmployeeStatus.ACTIVE, hire_date=date(2018, 1, 1)),
        Employee(employee_id="EMP-RPT-B1", company_id="COMP-RPT-B", first_name="Rivka",
                 last_name="Levi", email="b1@beta.example", country_code="IL",
                 status=EmployeeStatus.ACTIVE, hire_date=date(2019, 1, 1)),
        Employee(employee_id="EMP-RPT-C1", company_id="COMP-RPT-C", first_name="Gil",
                 last_name="Zur", email="c1@gamma.example", country_code="IL",
                 status=EmployeeStatus.ACTIVE, hire_date=date(2019, 1, 1)),
    ])
    db.flush()

    # --- מענקים ---------------------------------------------------------
    # G-A1: 4,800 אופציות, הענקה 15/06/2021, מימוש 2.00 USD. הבשלה 24 חודש בלי
    #       cliff מוקדם => הבשיל במלואו ב-15/06/2023, כלומר vested=4800 קבוע
    #       לכל "היום" שאחרי התאריך הזה (דטרמיניסטי, לא תלוי שעון).
    # G-A2: 1,200 אופציות, הענקה 01/01/2019 - *לפני* שורת המחיר הראשונה של
    #       החברה (01/06/2019), כלומר המקרה של NO_PRECEDING_PRICE.
    db.add_all([
        Grant(grant_id="GRANT-RPT-A1", employee_id="EMP-RPT-A1", pool_id="POOL-RPT-A",
              trustee_id="TRU-RPT-A1", grant_date=date(2021, 6, 15),
              grant_type=GrantType.IL_102_CAPITAL_GAINS, total_options=4_800.0,
              exercise_price=2.0, currency="USD", post_termination_window_days=90),
        Grant(grant_id="GRANT-RPT-A2", employee_id="EMP-RPT-A2", pool_id="POOL-RPT-A",
              trustee_id="TRU-RPT-A1", grant_date=date(2019, 1, 1),
              grant_type=GrantType.IL_102_CAPITAL_GAINS, total_options=1_200.0,
              exercise_price=1.0, currency="USD", post_termination_window_days=90),
        Grant(grant_id="GRANT-RPT-B1", employee_id="EMP-RPT-B1", pool_id="POOL-RPT-B",
              trustee_id="TRU-RPT-B1", grant_date=date(2021, 6, 15),
              grant_type=GrantType.US_NSO, total_options=9_000.0,
              exercise_price=5.0, currency="USD", post_termination_window_days=90),
        Grant(grant_id="GRANT-RPT-C1", employee_id="EMP-RPT-C1", pool_id="POOL-RPT-C",
              grant_date=date(2022, 1, 1), grant_type=GrantType.US_ISO,
              total_options=300.0, exercise_price=1.0, currency="USD",
              post_termination_window_days=90),
    ])
    db.add_all([
        VestingSchedule(schedule_id="SCH-RPT-A1", grant_id="GRANT-RPT-A1",
                        start_date=date(2021, 6, 15), cliff_months=12,
                        total_months=24, paused_days_total=0),
        VestingSchedule(schedule_id="SCH-RPT-A2", grant_id="GRANT-RPT-A2",
                        start_date=date(2019, 1, 1), cliff_months=0,
                        total_months=12, paused_days_total=0),
        VestingSchedule(schedule_id="SCH-RPT-B1", grant_id="GRANT-RPT-B1",
                        start_date=date(2021, 6, 15), cliff_months=12,
                        total_months=24, paused_days_total=0),
        VestingSchedule(schedule_id="SCH-RPT-C1", grant_id="GRANT-RPT-C1",
                        start_date=date(2022, 1, 1), cliff_months=0,
                        total_months=12, paused_days_total=0),
    ])
    db.flush()

    # --- בקשות מימוש: אותם תאריכים בדיוק ב-A וב-B (דאטה חופפת) -----------
    db.add_all([
        ExerciseRequest(request_id="REQ-RPT-A1", grant_id="GRANT-RPT-A1",
                        employee_id="EMP-RPT-A1", options_requested=800.0,
                        status=ExerciseRequestStatus.APPROVED,
                        requested_at=_at(date(2024, 3, 10)), reviewed_at=_at(date(2024, 3, 20))),
        # PENDING - *לא* נספרת כ"מומשה" בדוח חשיפת הנאמן (רק APPROVED נספרת).
        ExerciseRequest(request_id="REQ-RPT-A2", grant_id="GRANT-RPT-A2",
                        employee_id="EMP-RPT-A2", options_requested=100.0,
                        status=ExerciseRequestStatus.PENDING,
                        requested_at=_at(date(2025, 5, 5))),
        ExerciseRequest(request_id="REQ-RPT-B1", grant_id="GRANT-RPT-B1",
                        employee_id="EMP-RPT-B1", options_requested=4_000.0,
                        status=ExerciseRequestStatus.APPROVED,
                        requested_at=_at(date(2024, 3, 10)), reviewed_at=_at(date(2024, 3, 20))),
    ])

    # --- היסטוריית מחירי מניה -------------------------------------------
    # A: שלוש שורות. 01/01/2021 היא הקודמת-הקרובה ל-15/06/2021; 01/01/2023 היא
    # מאוחרת יותר ואסור שתשמש (look-ahead bias) - זה גרעין הבדיקה.
    db.add_all([
        StockPricesHistory(price_id="PRC-RPT-A-2019", company_id="COMP-RPT-A",
                           price_date=date(2019, 6, 1), fmv_price=3.0, currency="USD"),
        StockPricesHistory(price_id="PRC-RPT-A-2021", company_id="COMP-RPT-A",
                           price_date=date(2021, 1, 1), fmv_price=10.0, currency="USD"),
        StockPricesHistory(price_id="PRC-RPT-A-2023", company_id="COMP-RPT-A",
                           price_date=date(2023, 1, 1), fmv_price=25.0, currency="USD"),
        # B: מחיר אחד, גבוה בהרבה - דליפה תזוהה בסכום, לא רק במזהה.
        StockPricesHistory(price_id="PRC-RPT-B-2021", company_id="COMP-RPT-B",
                           price_date=date(2021, 1, 1), fmv_price=500.0, currency="USD"),
        # C: אין אף שורה בכלל (NO_PRICE_DATA).
    ])

    # --- שני מקורות דוח התנועה: ledger + audit-only ----------------------
    _ledger_row(db, "Grant", "GRANT-RPT-A1", "COMP-RPT-A", "GRANT_CREATED", date(2024, 2, 1))
    _ledger_row(db, "Grant", "GRANT-RPT-B1", "COMP-RPT-B", "GRANT_CREATED", date(2024, 2, 1))
    db.add_all([
        ShareClass(share_class_id="SC-RPT-A", company_id="COMP-RPT-A", name="Common",
                   class_type="COMMON", seniority_order=10),
        ShareClass(share_class_id="SC-RPT-B", company_id="COMP-RPT-B", name="Common",
                   class_type="COMMON", seniority_order=10),
        Shareholder(shareholder_id="SH-RPT-A1", company_id="COMP-RPT-A",
                    name="Alpha Founder", shareholder_type="FOUNDER"),
        Shareholder(shareholder_id="SH-RPT-B1", company_id="COMP-RPT-B",
                    name="Beta Founder", shareholder_type="FOUNDER"),
    ])
    db.flush()
    db.add_all([
        AuditLog(entity_type="Shareholder", entity_id="SH-RPT-A1", action="CREATE",
                 occurred_at=_at(date(2024, 2, 5))),
        AuditLog(entity_type="Shareholder", entity_id="SH-RPT-B1", action="CREATE",
                 occurred_at=_at(date(2024, 2, 5))),
    ])
    db.flush()

    admin_a = _user(db, "U-RPT-ADMIN-A", UserRole.COMPANY_ADMIN, company_id="COMP-RPT-A")
    # אדמין שני *באותה חברה* - היום אין לו מקבילה בזריעה (חשבון admin משותף
    # אחד לחברה), אבל כלל הנראות של דוחות שמורים נכתב עבורו ולכן נבדק עליו.
    admin_a2 = _user(db, "U-RPT-ADMIN-A2", UserRole.COMPANY_ADMIN, company_id="COMP-RPT-A")
    admin_b = _user(db, "U-RPT-ADMIN-B", UserRole.COMPANY_ADMIN, company_id="COMP-RPT-B")
    admin_c = _user(db, "U-RPT-ADMIN-C", UserRole.COMPANY_ADMIN, company_id="COMP-RPT-C")
    emp_a = _user(db, "U-RPT-EMP-A1", UserRole.EMPLOYEE, employee_id="EMP-RPT-A1")
    trustee_a = _user(db, "U-RPT-TRU-A1", UserRole.TRUSTEE, trustee_id="TRU-RPT-A1")

    return SimpleNamespace(
        db=db,
        admin_a=_token(db, admin_a), admin_a2=_token(db, admin_a2),
        admin_b=_token(db, admin_b), admin_c=_token(db, admin_c),
        emp_a=_token(db, emp_a), trustee_a=_token(db, trustee_a),
        admin_a_id="U-RPT-ADMIN-A", admin_a2_id="U-RPT-ADMIN-A2",
    )


def _get(client, endpoint, headers, **params):
    return client.get(f"{REPORTS}/{endpoint}", headers=headers, params=params)


def _rows(client, endpoint, headers, **params):
    resp = _get(client, endpoint, headers, **params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _row_by(rows, key, value):
    matching = [r for r in rows if r[key] == value]
    assert len(matching) == 1, f"ציפינו לשורה אחת עם {key}={value}, קיבלנו {matching}"
    return matching[0]


# ===================================================================
# QA-110-01..02 - בידוד חוצה-חברות על כל שמונת דוחות ה-GET.
# זו התקדים שתפס IDOR אמיתי ב-create_shareholder (v1.0.0): אף אחת מהטבלאות
# שהדוחות קוראים (Grant/VestingSchedule/ExerciseRequest/AuditLog/LedgerEvent)
# אינה נושאת company_id, ולכן דוח שמסנן "בעצמו" יחזיר את כל החברות.
# ===================================================================

@pytest.mark.parametrize("endpoint", sorted(REPORT_ENDPOINTS))
def test_no_company_b_identifier_leaks_into_company_as_report(client, world, endpoint):
    body = _rows(client, endpoint, world.admin_a, **REPORT_ENDPOINTS[endpoint])
    blob = json.dumps(body, default=str)

    leaked = [t for t in B_TOKENS if t in blob]
    assert not leaked, f"מזהי חברה B דלפו לדוח {endpoint} של חברה A: {leaked}\n{blob}"


@pytest.mark.parametrize("endpoint", sorted(REPORT_ENDPOINTS))
def test_no_company_a_identifier_leaks_into_company_bs_report(client, world, endpoint):
    """הכיוון ההפוך במפורש: דוח שמסנן "החברה הראשונה שנמצאה" היה עובר את
    הבדיקה למעלה ונכשל רק כאן."""
    body = _rows(client, endpoint, world.admin_b, **REPORT_ENDPOINTS[endpoint])
    blob = json.dumps(body, default=str)

    leaked = [t for t in A_TOKENS if t in blob]
    assert not leaked, f"מזהי חברה A דלפו לדוח {endpoint} של חברה B: {leaked}\n{blob}"


@pytest.mark.parametrize("endpoint", sorted(set(REPORT_ENDPOINTS) - {"dashboard"}))
def test_every_report_actually_returns_company_a_data_so_isolation_is_not_vacuous(
        client, world, endpoint):
    """שמירה מפני מעבר-סרק: דוח שמחזיר ריק תמיד היה "עובר" את שתי בדיקות
    הבידוד למעלה. כאן נדרש במפורש שלפחות מזהה אחד של A מופיע בפועל."""
    body = _rows(client, endpoint, world.admin_a, **REPORT_ENDPOINTS[endpoint])
    blob = json.dumps(body, default=str)

    assert any(t in blob for t in A_TOKENS), (
        f"דוח {endpoint} לא החזיר שום מזהה של חברה A - בדיקת הבידוד עליו "
        f"הייתה עוברת מסיבה הלא-נכונה: {blob}"
    )


def test_dashboard_counts_are_scoped_per_company(client, world):
    """הדשבורד אינו מחזיר מזהים (רק ספירות/אחוזים), ולכן בדיקת מזהים לבדה
    הייתה ריקה עליו - הספירות עצמן הן ההוכחה."""
    a = _rows(client, "dashboard", world.admin_a)
    b = _rows(client, "dashboard", world.admin_b)

    assert a["total_grants_in_scope"] == 2, a          # GRANT-RPT-A1 + A2
    assert b["total_grants_in_scope"] == 1, b          # GRANT-RPT-B1 בלבד
    assert [t["grant_type"] for t in a["tax_track_breakdown"]] == ["IL_102_CAPITAL_GAINS"]
    assert a["tax_track_breakdown"][0]["count"] == 2
    assert a["tax_track_breakdown"][0]["pct_of_total"] == 100.0
    # US_NSO קיים רק ב-B; הופעתו אצל A הייתה דליפה שספירה כוללת לא חושפת.
    assert [t["grant_type"] for t in b["tax_track_breakdown"]] == ["US_NSO"]


def test_saved_report_of_another_company_is_invisible_and_undeletable(client, world):
    created = client.post(f"{REPORTS}/saved", headers=world.admin_a, json={
        "name": "A private view", "report_type": "POOL_STATUS", "filter_params": {},
    })
    assert created.status_code == 200, created.text
    report_id = created.json()["report_id"]

    listed_b = client.get(f"{REPORTS}/saved", headers=world.admin_b)
    assert listed_b.status_code == 200
    assert [r["report_id"] for r in listed_b.json()] == []

    # 404 ולא 403 - קיומו של דוח שמור בחברה אחרת לא דולף (אותו דפוס כמו
    # download_export/assert_document_access).
    assert client.get(f"{REPORTS}/saved/{report_id}", headers=world.admin_b).status_code == 404
    assert client.delete(f"{REPORTS}/saved/{report_id}", headers=world.admin_b).status_code == 404


# ===================================================================
# QA-110-03..06 - הרשאות: כל 12 ה-endpoints admin-only.
# ===================================================================

@pytest.mark.parametrize("endpoint", sorted(REPORT_ENDPOINTS))
@pytest.mark.parametrize("role", ["emp_a", "trustee_a"])
def test_report_endpoints_reject_non_admin_roles(client, world, endpoint, role):
    resp = _get(client, endpoint, getattr(world, role), **REPORT_ENDPOINTS[endpoint])
    assert resp.status_code == 403, f"{endpoint} החזיר {resp.status_code} ל-{role}"


@pytest.mark.parametrize("role", ["emp_a", "trustee_a"])
def test_saved_report_crud_rejects_non_admin_roles(client, world, role):
    headers = getattr(world, role)
    assert client.get(f"{REPORTS}/saved", headers=headers).status_code == 403
    assert client.post(f"{REPORTS}/saved", headers=headers, json={
        "name": "x", "report_type": "POOL_STATUS"}).status_code == 403
    assert client.get(f"{REPORTS}/saved/anything", headers=headers).status_code == 403
    assert client.delete(f"{REPORTS}/saved/anything", headers=headers).status_code == 403


# ===================================================================
# QA-110-07..08 - דוח מצב פולים (הדוח הפשוט; אימות מספרי מלא).
# ===================================================================

def test_pool_status_reports_only_own_pools_with_exact_balances(client, world):
    body = _rows(client, "pool-status", world.admin_a)

    assert [r["pool_id"] for r in body["rows"]] == ["POOL-RPT-A"]
    row = body["rows"][0]
    assert (row["total_shares"], row["allocated_shares"], row["unallocated_shares"]) \
        == (100_000.0, 6_000.0, 94_000.0)
    assert body["summary"] == {
        "pool_count": 1, "total_shares": 100_000.0,
        "total_allocated": 6_000.0, "total_unallocated": 94_000.0,
    }


# ===================================================================
# QA-110-09..12 - חשיפה לפי נאמן: **דוגמה מחושבת ביד**.
#
# נאמן TRU-RPT-A1 מחזיק שני מענקים:
#   GRANT-RPT-A1  4,800 אופציות, הבשלה 15/06/2021 + 24 חודשים => 15/06/2023
#                 (עבר) => vested = 4,800
#   GRANT-RPT-A2  1,200 אופציות, הבשלה 01/01/2019 + 12 חודשים => 01/01/2020
#                 (עבר) => vested = 1,200
#
#   employee_count             = |{EMP-RPT-A1, EMP-RPT-A2}|      = 2
#   grant_count                                                  = 2
#   total_options              = 4,800 + 1,200                    = 6,000
#   vested_options             = 4,800 + 1,200                    = 6,000
#   unvested_options           = max(0, 6,000 - 6,000)            = 0
#   exercised_options          = 800 (REQ-RPT-A1, APPROVED בלבד;
#                                REQ-RPT-A2 היא PENDING ואינה נספרת)  = 800
#   unexercised_vested_options = max(0, 6,000 - 800)              = 5,200
#   estimated_value            = None (שדה שמור ל-v1.4.0)
#
# נאמן TRU-RPT-A2 (בלי מענקים) חייב להופיע כשורה עם אפסים - נאמן שנשמט
# מהדוח קורא כ"אין חשיפה" בדיוק כמו נאמן עם 0, ואלה שני מצבים שונים.
# ===================================================================

def test_trustee_exposure_worked_example_counts_and_vesting(client, world):
    body = _rows(client, "trustee-exposure", world.admin_a)
    row = _row_by(body["rows"], "trustee_id", "TRU-RPT-A1")

    assert row["name"] == "Trustee Alpha"
    assert row["registration_number"] == "A-111"
    assert row["employee_count"] == 2
    assert row["grant_count"] == 2
    assert row["total_options"] == 6_000.0
    assert row["vested_options"] == 6_000.0
    assert row["unvested_options"] == 0.0
    assert row["exercised_options"] == 800.0, "רק בקשה מאושרת נספרת כמומשה"
    assert row["unexercised_vested_options"] == 5_200.0
    assert body["summary"]["trustee_count"] == 2
    assert body["summary"]["degraded_grant_ids"] == []


def test_trustee_with_no_grants_is_a_zero_row_not_an_omission(client, world):
    body = _rows(client, "trustee-exposure", world.admin_a)
    row = _row_by(body["rows"], "trustee_id", "TRU-RPT-A2")

    assert row["employee_count"] == 0
    assert row["grant_count"] == 0
    assert row["total_options"] == 0.0
    assert row["unexercised_vested_options"] == 0.0


def test_trustee_exposure_estimated_value_is_always_none_in_this_version(client, world):
    """v1.4.0 (הערכות שווי) טרם נבנתה. השדה קיים כדי שהוספת מדד שקלי לא תדרוש
    רה-ארגון - אבל ערך כספי שיתחיל להיפלט כאן בלי מקור שווי מאושר הוא בדיוק
    "מספר בלי שרשור מקורות" שהפרויקט אוסר. נעול, כולל ההצהרה שמסבירה למה."""
    body = _rows(client, "trustee-exposure", world.admin_a)

    assert "estimated_value" in body["rows"][0]
    assert all(r["estimated_value"] is None for r in body["rows"]), body["rows"]
    assert any("estimated_value" in d for d in body["disclosures"]), body["disclosures"]


def test_trustee_exposure_marks_a_grant_without_a_vesting_schedule_as_degraded(client, world):
    """מענק בלי לוח הבשלה: "לא ידוע" ולא "0 שהבשיל" (דפוס P4). הוא נספר
    ב-total_options אבל *לא* ב-vested_options, ומזההו מופיע ב-degraded."""
    world.db.add(Grant(grant_id="GRANT-RPT-A3", employee_id="EMP-RPT-A1",
                       pool_id="POOL-RPT-A", trustee_id="TRU-RPT-A1",
                       grant_date=date(2023, 1, 1), grant_type=GrantType.IL_102_WORK_INCOME,
                       total_options=500.0, exercise_price=1.0, currency="USD",
                       post_termination_window_days=90))
    world.db.flush()

    body = _rows(client, "trustee-exposure", world.admin_a)
    row = _row_by(body["rows"], "trustee_id", "TRU-RPT-A1")

    assert body["summary"]["degraded_grant_ids"] == ["GRANT-RPT-A3"]
    assert row["total_options"] == 6_500.0
    assert row["vested_options"] == 6_000.0, "מענק בלי לוח הבשלה לא נספר כהבשיל"


# ===================================================================
# QA-110-13..19 - הוצאת תגמול הוני (אומדן, non-GAAP): **דוגמה מחושבת ביד**.
#
# היסטוריית המחירים של COMP-RPT-A:
#   01/06/2019 -> 3.00 USD
#   01/01/2021 -> 10.00 USD    <-- הקודמת הקרובה ביותר ל-15/06/2021
#   01/01/2023 -> 25.00 USD    <-- מאוחרת מההענקה; **אסור** לשימוש
#
# GRANT-RPT-A1: grant_date=15/06/2021, exercise_price=2.00, total_options=4,800
#   fmv_at_grant_date = 10.00 (01/01/2021)
#   contribution      = max(0, 10.00 - 2.00) * 4,800 = 8.00 * 4,800 = 38,400.00
#   (עם המחיר המאוחר היה יוצא (25-2)*4,800 = 110,400 - טעות של פי ~2.9,
#    וזה בדיוק ה-look-ahead bias שהתכנון אוסר.)
#
# GRANT-RPT-A2: grant_date=01/01/2019 - כל שורות המחיר מאוחרות ממנו =>
#   exclusion_reason = NO_PRECEDING_PRICE, contribution = None (לא 0.0!)
#
#   total_contribution = 38,400.00 + (מוחרג) = 38,400.00
# ===================================================================

def test_compensation_expense_uses_the_nearest_preceding_price_never_a_later_one(client, world):
    body = _rows(client, "compensation-expense", world.admin_a)
    row = _row_by(body["rows"], "grant_id", "GRANT-RPT-A1")

    assert row["matched_price_date"] == "2021-01-01", (
        "ה-FMV נלקח משורת מחיר שאינה הקודמת-הקרובה-ביותר להענקה"
    )
    assert row["fmv_at_grant_date"] == 10.0
    assert row["contribution"] == 38_400.0
    assert row["exclusion_reason"] is None
    # (25.00 - 2.00) * 4,800 - הערך שהיה מתקבל מהמחיר המאוחר. אם הוא מופיע,
    # הכלל התהפך בשקט.
    assert row["contribution"] != 110_400.0
    assert body["summary"]["total_contribution"] == 38_400.0
    assert body["summary"]["by_pool"] == {"POOL-RPT-A": 38_400.0}
    assert body["summary"]["by_tax_track"] == {"IL_102_CAPITAL_GAINS": 38_400.0}


def test_compensation_expense_excluded_grant_contributes_none_not_zero(client, world):
    """0.0 ומענק-חסר-נתון הם שני דברים שונים: 0.0 הוא "מחוץ לכסף באמת".
    נפילה חזרה ל-exercise_price (או ל-0) הייתה מייצרת מספר תקין-למראה
    ובלתי-ניתן להבחנה - דפוס P4 בצורתו הכספית."""
    body = _rows(client, "compensation-expense", world.admin_a)
    row = _row_by(body["rows"], "grant_id", "GRANT-RPT-A2")

    assert row["exclusion_reason"] == "NO_PRECEDING_PRICE"
    assert row["contribution"] is None, "מענק מוחרג חייב לתרום None, לא 0.0"
    assert row["fmv_at_grant_date"] is None
    assert row["matched_price_date"] is None


def test_compensation_expense_no_price_data_is_distinct_from_no_preceding_price(client, world):
    """COMP-RPT-C אין לה אף שורת מחיר => NO_PRICE_DATA. שתי הסיבות חייבות
    להישאר מובחנות: "לא הזנתם מחירים בכלל" ו"הזנתם, אבל לא לפני ההענקה" הן
    שתי פעולות תיקון שונות למשתמש."""
    body = _rows(client, "compensation-expense", world.admin_c)
    row = _row_by(body["rows"], "grant_id", "GRANT-RPT-C1")

    assert row["exclusion_reason"] == "NO_PRICE_DATA"
    assert row["contribution"] is None
    assert body["summary"]["exclusion_counts"] == {
        "NO_PRICE_DATA": 1, "NO_PRECEDING_PRICE": 0, "CURRENCY_MISMATCH": 0,
    }
    assert body["summary"]["total_contribution"] == 0.0
    assert body["summary"]["included_grant_count"] == 0
    assert body["summary"]["excluded_grant_count"] == 1


def test_compensation_expense_currency_mismatch_is_its_own_exclusion_reason(client, world):
    """Grant.currency ו-StockPricesHistory.currency הן עמודות נפרדות שאינן
    כפויות-שוות. חיסור ישיר בין ₪ ל-$ מייצר מספר אמין-למראה ושגוי."""
    world.db.add(Grant(grant_id="GRANT-RPT-A4", employee_id="EMP-RPT-A1",
                       pool_id="POOL-RPT-A", grant_date=date(2023, 6, 1),
                       grant_type=GrantType.IL_102_CAPITAL_GAINS, total_options=1_000.0,
                       exercise_price=4.0, currency="ILS",
                       post_termination_window_days=90))
    world.db.flush()

    body = _rows(client, "compensation-expense", world.admin_a)
    row = _row_by(body["rows"], "grant_id", "GRANT-RPT-A4")

    assert row["currency"] == "ILS"
    assert row["exclusion_reason"] == "CURRENCY_MISMATCH"
    assert row["contribution"] is None, "אסור לחסר בין שני מטבעות שונים"
    # הסכום הכולל לא מושפע מהמענק המוחרג - (25-4)*1000 = 21,000 לא נוסף.
    assert body["summary"]["total_contribution"] == 38_400.0
    assert body["summary"]["exclusion_counts"] == {
        "NO_PRICE_DATA": 0, "NO_PRECEDING_PRICE": 1, "CURRENCY_MISMATCH": 1,
    }


def test_compensation_expense_three_exclusion_reasons_stay_distinguishable(client, world):
    """שלוש הסיבות יחד בדוח אחד: לא "N/A" גנרי אחד. אם מישהו יאחד אותן
    ל-value אחד, הבדיקה הזו נופלת."""
    world.db.add(Grant(grant_id="GRANT-RPT-A4", employee_id="EMP-RPT-A1",
                       pool_id="POOL-RPT-A", grant_date=date(2023, 6, 1),
                       grant_type=GrantType.IL_102_CAPITAL_GAINS, total_options=1_000.0,
                       exercise_price=4.0, currency="ILS",
                       post_termination_window_days=90))
    world.db.flush()

    reasons_a = {r["exclusion_reason"] for r in
                 _rows(client, "compensation-expense", world.admin_a)["rows"]}
    reasons_c = {r["exclusion_reason"] for r in
                 _rows(client, "compensation-expense", world.admin_c)["rows"]}

    assert reasons_a == {None, "NO_PRECEDING_PRICE", "CURRENCY_MISMATCH"}
    assert reasons_c == {"NO_PRICE_DATA"}


def test_compensation_expense_is_labelled_an_estimate_in_every_row_and_the_summary(client, world):
    """התיוג חייב לשרוד ייצוא - עמודה בדאטה, לא CSS שנעלם בהורדה."""
    body = _rows(client, "compensation-expense", world.admin_a)

    assert all(r["is_estimate"] is True for r in body["rows"])
    assert all("Not GAAP" in r["basis"] for r in body["rows"])
    assert body["summary"]["is_estimate"] is True
    assert "Not GAAP" in body["summary"]["basis"]
    assert any("Not GAAP" in d for d in body["disclosures"])


# ===================================================================
# QA-110-20..23 - קצוות טווח תאריכים. **movement ו-exercise-activity אינם
# סימטריים בכוונה** (movement דורש שני התאריכים, exercise-activity לא) -
# הבדיקות מתעדות את ההתנהגות בפועל, לא הנחת סימטריה.
# ===================================================================

def test_movement_requires_both_dates_while_exercise_activity_does_not(client, world):
    missing_both = client.get(f"{REPORTS}/movement", headers=world.admin_a)
    assert missing_both.status_code == 422, missing_both.text
    missing_one = client.get(f"{REPORTS}/movement", headers=world.admin_a,
                             params={"date_from": "2024-01-01"})
    assert missing_one.status_code == 422, missing_one.text

    # exercise-activity: שני התאריכים אופציונליים; בלעדיהם כל בקשה עם
    # requested_at נכללת.
    open_ended = client.get(f"{REPORTS}/exercise-activity", headers=world.admin_a)
    assert open_ended.status_code == 200, open_ended.text
    assert {r["request_id"] for r in open_ended.json()["rows"]} == {"REQ-RPT-A1", "REQ-RPT-A2"}


@pytest.mark.parametrize("endpoint", ["movement", "exercise-activity"])
def test_date_from_after_date_to_is_a_clean_400(client, world, endpoint):
    resp = _get(client, endpoint, world.admin_a, date_from="2025-01-01", date_to="2024-01-01")
    assert resp.status_code == 400, resp.text
    assert "date_from" in resp.json()["detail"]


@pytest.mark.parametrize("endpoint", ["movement", "exercise-activity"])
def test_range_entirely_before_the_company_existed_is_empty_not_an_error(client, world, endpoint):
    body = _rows(client, endpoint, world.admin_a,
                 date_from="1990-01-01", date_to="1990-12-31")
    assert body["rows"] == []


def test_range_with_no_results_still_returns_the_full_envelope(client, world):
    """טווח ריק אינו שגיאה ואינו תשובה חלקית: columns/summary/disclosures
    נשארים, כדי שה-UI לא יבדיל בין "אין תנועה" ל"הדוח נשבר"."""
    body = _rows(client, "movement", world.admin_a,
                 date_from="2010-01-01", date_to="2010-12-31")

    assert body["rows"] == []
    assert body["summary"]["ledger_event_count"] == 0
    assert body["summary"]["audit_only_event_count"] == 0
    assert body["summary"]["trustees"]["tracked"] is False


# ===================================================================
# QA-110-24..26 - דוח תנועה: שני מקורות נפרדים + הצהרת "נאמנים לא במעקב".
# ===================================================================

def test_movement_reports_both_sources_separately_within_the_range(client, world):
    body = _rows(client, "movement", world.admin_a,
                 date_from="2024-01-01", date_to="2024-12-31")

    by_source = {}
    for row in body["rows"]:
        by_source.setdefault(row["source"], []).append(row["id"])
    assert by_source["LEDGER"] == ["GRANT-RPT-A1"]
    assert by_source["AUDIT_LOG"] == ["SH-RPT-A1"]
    assert body["summary"]["ledger_event_count"] == 1
    assert body["summary"]["audit_only_event_count"] == 1
    assert "Shareholder" in body["summary"]["audit_only_types"]
    assert "Grant" in body["summary"]["ledger_covered_types"]


def test_movement_trustee_section_is_an_explicit_not_tracked_disclosure(client, world):
    """הכי חשוב בדוח הזה. ל-Trustee אין כיסוי LedgerEvent ואין כותב
    AuditLog(entity_type="Trustee") בכלל - כלומר "אין תנועה" בלתי-ניתן
    להבחנה מ"לא במעקב" אלא אם ההצהרה מפורשת. שורה ריקה או השמטה שקטה כאן
    היא בדיוק ה-0-השקרי שהפרויקט אוסר; זו מגבלת מוצר מתועדת, לא באג."""
    body = _rows(client, "movement", world.admin_a,
                 date_from="2024-01-01", date_to="2024-12-31")

    trustees = body["summary"]["trustees"]
    assert trustees["tracked"] is False, "הסתרת ההצהרה שקולה ל'אין תנועה' שקרי"
    assert trustees["message"], "ההצהרה חייבת להיות טקסט מוצג, לא דגל בלבד"
    assert "trustee" in trustees["message"].lower()
    assert trustees["message"] in body["disclosures"], (
        "ההצהרה חייבת להגיע גם ל-disclosures - זה מה שמופיע ב-PDF/CSV, "
        "לא רק ב-summary של ה-JSON"
    )
    # ולא בשקט דרך שורות: אין שורת תנועה שמתחזה לנאמן.
    assert not [r for r in body["rows"] if r["aggregate_or_entity_type"] == "Trustee"]


def test_movement_trustee_disclosure_is_present_even_for_an_empty_range(client, world):
    """"לא במעקב" אינו תלוי בטווח - טווח ריק לא אמור להשמיט את ההצהרה."""
    body = _rows(client, "movement", world.admin_a,
                 date_from="1990-01-01", date_to="1990-12-31")
    assert body["summary"]["trustees"] == {
        "tracked": False, "message": body["summary"]["trustees"]["message"],
    }
    assert body["summary"]["trustees"]["message"] in body["disclosures"]


# ===================================================================
# QA-110-27 - ASC 718: checklist בלבד, **בלי אף מספר כספי**.
# ===================================================================

def test_asc718_readiness_contains_no_money_field_at_all(client, world):
    """אילוץ ציות, לא סגנון: אין בקודבייס חישוב שווי-הוגן-ביום-המענק, שיטת
    הפחתה או אומדן forfeiture - ולכן מספר דולרי בדוח שמוגש לרואה-חשבון היה
    מתחזה לסמכות שאינה קיימת. נעול מבנית: כל ערך בכל שורה הוא bool/str, אף
    פעם int/float, ו-summary מכיל ספירות בלבד."""
    expected_columns = ["grant_id", "pool_id", "has_vesting_schedule",
                        "has_preceding_stock_price", "has_exercise_price_recorded"]
    body = _rows(client, "asc718-readiness", world.admin_a)

    # ה-envelope של ה-JSON אינו כולל ``columns`` (rows/summary/disclosures בלבד,
    # ראו api/reports.py::_respond) - לכן סדר העמודות נעול דרך כותרת ה-CSV,
    # ותוכן השדות נעול דרך מפתחות השורות עצמן. שתי הבדיקות יחד, לא אחת מהן:
    # שדה כספי יכול להתווסף לשורות בלי לגעת בכותרת ולהיפך.
    csv_resp = client.get(f"{REPORTS}/asc718-readiness", headers=world.admin_a,
                          params={"format": "csv"})
    assert csv_resp.status_code == 200, csv_resp.text
    header = next(csv.reader(io.StringIO(csv_resp.content.decode("utf-8"))))
    assert header == expected_columns, header

    for row in body["rows"]:
        assert set(row) == set(expected_columns), row
        for key, value in row.items():
            assert isinstance(value, (bool, str)), (
                f"ASC 718 החזיר ערך לא-בוליאני/לא-טקסטואלי בשדה {key}: {value!r} - "
                f"כל מספר כאן הוא רגרסיית ציות"
            )
    assert set(body["summary"]) == {"grant_count", "fully_ready_count"}
    assert body["summary"]["grant_count"] == 2
    assert any("no dollar figure" in d.lower() for d in body["disclosures"]), body["disclosures"]


def test_asc718_flags_reflect_the_actual_grant_state(client, world):
    """הדגלים אינם "תמיד True": GRANT-RPT-A2 הוענק לפני שורת המחיר הראשונה
    ולכן has_preceding_stock_price=False - אחרת הדוח היה מאשר מוכנות שאין."""
    body = _rows(client, "asc718-readiness", world.admin_a)

    a1 = _row_by(body["rows"], "grant_id", "GRANT-RPT-A1")
    a2 = _row_by(body["rows"], "grant_id", "GRANT-RPT-A2")
    assert a1["has_preceding_stock_price"] is True
    assert a2["has_preceding_stock_price"] is False
    assert a1["has_vesting_schedule"] is True
    assert body["summary"]["fully_ready_count"] == 1


# ===================================================================
# QA-110-28 - הגנת CSV formula-injection על הייצוא החדש.
# ===================================================================

@pytest.mark.parametrize("payload", ["=cmd|' /C calc'!A0", "+1+1", "-2-2", "@SUM(A1:A9)"])
def test_csv_export_neutralises_formula_prefixes_in_a_free_text_field(client, world, payload):
    """שם נאמן הוא שדה טקסט חופשי שמגיע מהמשתמש ונכתב ישירות ל-CSV. בלי
    התו ' המוביל, Excel מריץ את התא בפתיחה. הדוח מייבא את
    export.py::_escape_formula_cells - הבדיקה מוכיחה שההגנה *נורית בפועל*
    על הנתיב החדש, לא רק שהפונקציה קיימת."""
    world.db.add(Trustee(trustee_id="TRU-RPT-A9", company_id="COMP-RPT-A",
                         name=payload, registration_number="A-999"))
    world.db.flush()

    resp = client.get(f"{REPORTS}/trustee-exposure", headers=world.admin_a,
                      params={"format": "csv"})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")

    rows = list(csv.DictReader(io.StringIO(resp.content.decode("utf-8"))))
    row = _row_by(rows, "trustee_id", "TRU-RPT-A9")
    assert row["name"] == "'" + payload, f"התא לא נוטרל: {row['name']!r}"

    # ובכל התאים של כל השורות: אין ולו תא אחד שאקסל יפרש כנוסחה.
    live_formulas = [(r["trustee_id"], k, v) for r in rows for k, v in r.items()
                     if isinstance(v, str) and v.startswith(("=", "+", "-", "@"))]
    assert not live_formulas, f"תאים שיפורשו כנוסחה: {live_formulas}"


def test_csv_export_keeps_the_reserved_estimated_value_column_empty(client, world):
    """estimated_value הוא None; ב-CSV הוא חייב להיות תא ריק ולא "0" -
    אותה הבחנה בדיוק כמו ב-JSON, גם אחרי ההמרה לטקסט."""
    resp = client.get(f"{REPORTS}/trustee-exposure", headers=world.admin_a,
                      params={"format": "csv"})
    rows = list(csv.DictReader(io.StringIO(resp.content.decode("utf-8"))))
    assert rows, resp.content
    assert {r["estimated_value"] for r in rows} == {""}


def test_pdf_export_returns_a_pdf_document(client, world):
    resp = client.get(f"{REPORTS}/compensation-expense", headers=world.admin_a,
                      params={"format": "pdf"})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")


def test_an_unknown_format_is_a_clean_400(client, world):
    resp = client.get(f"{REPORTS}/pool-status", headers=world.admin_a,
                      params={"format": "xlsx"})
    assert resp.status_code == 400, resp.text


# ===================================================================
# QA-110-29..30 - רישום ביקורת על נתיב ה-bulk-egress החדש.
# ===================================================================

def _report_audit_rows(db):
    return db.query(AuditLog).filter(AuditLog.entity_type == "Report").all()


@pytest.mark.parametrize("endpoint,report_type", [
    ("pool-status", "POOL_STATUS"),
    ("trustee-exposure", "TRUSTEE_EXPOSURE"),
    ("deadline-risk", "DEADLINE_RISK"),
    ("exercise-activity", "EXERCISE_ACTIVITY"),
    ("compensation-expense", "COMPENSATION_EXPENSE"),
    ("movement", "MOVEMENT"),
    ("asc718-readiness", "ASC718_READINESS"),
    ("dashboard", "DASHBOARD"),
])
def test_every_report_request_writes_an_audit_row(client, world, endpoint, report_type):
    assert _report_audit_rows(world.db) == []

    _rows(client, endpoint, world.admin_a, **REPORT_ENDPOINTS[endpoint])

    rows = _report_audit_rows(world.db)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row.entity_id == report_type
    assert row.action == "generated", "format=json => generated"
    assert row.actor_user_id == world.admin_a_id
    assert f"report_type={report_type}" in row.notes
    assert "format=json" in row.notes


def test_csv_and_pdf_downloads_are_audited_as_downloaded_not_generated(client, world):
    client.get(f"{REPORTS}/pool-status", headers=world.admin_a, params={"format": "csv"})
    client.get(f"{REPORTS}/pool-status", headers=world.admin_a, params={"format": "pdf"})

    rows = _report_audit_rows(world.db)
    assert len(rows) == 2, rows
    assert {r.action for r in rows} == {"downloaded"}, [r.action for r in rows]
    formats = {r.notes.split("format=")[1].split()[0] for r in rows}
    assert formats == {"csv", "pdf"}, formats


def test_audit_notes_record_the_date_range_for_ranged_reports(client, world):
    _rows(client, "movement", world.admin_a, date_from="2024-01-01", date_to="2024-12-31")

    row = _report_audit_rows(world.db)[0]
    assert "date_from=2024-01-01" in row.notes
    assert "date_to=2024-12-31" in row.notes


@pytest.mark.parametrize("endpoint", sorted(REPORT_ENDPOINTS))
def test_audit_notes_never_contain_raw_pii(client, world, endpoint):
    """יומן הביקורת נגיש לצרכנים אחרים מהדוח עצמו. סוג דוח/פורמט/טווח - כן;
    שם עובד, אימייל, שם נאמן - לעולם לא."""
    _rows(client, endpoint, world.admin_a, **REPORT_ENDPOINTS[endpoint])

    pii = ("Dana", "Bar", "Noa", "Gal", "a1@alpha.example", "a2@alpha.example",
           "Trustee Alpha", "Alpha Founder")
    for row in _report_audit_rows(world.db):
        found = [p for p in pii if p in (row.notes or "")]
        assert not found, f"PII גולמי ב-notes של {endpoint}: {found} -> {row.notes!r}"


def test_every_report_endpoint_audits_a_report_type_from_the_closed_vocabulary(client, world):
    """שבעת סוגי הדוח שנשמרים (SAVED_REPORT_TYPES) חייבים להיות בדיוק אלה
    שה-endpoints מייצרים. סוג שקיים ב-endpoint ולא באוצר המילים אינו ניתן
    לשמירה; סוג ששמור ואין לו endpoint הוא דוח שמור שאי-אפשר להריץ."""
    for endpoint, params in REPORT_ENDPOINTS.items():
        _rows(client, endpoint, world.admin_a, **params)

    audited = {r.entity_id for r in _report_audit_rows(world.db)}
    # הדשבורד אינו סוג-דוח שמור (JSON בלבד, אין לו CSV/PDF ואין לו פילטרים).
    assert audited - {"DASHBOARD"} == SAVED_REPORT_TYPES, audited


# ===================================================================
# QA-110-31..33 - דוחות שמורים: CRUD + כללי נראות.
# ===================================================================

def _save(client, headers, name, report_type="POOL_STATUS", is_private=True, filters=None):
    resp = client.post(f"{REPORTS}/saved", headers=headers, json={
        "name": name, "report_type": report_type,
        "filter_params": filters if filters is not None else {},
        "is_private": is_private,
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_saved_report_defaults_to_private_and_round_trips_its_filters(client, world):
    filters = {"date_from": "2024-01-01", "date_to": "2024-12-31", "format": "csv"}
    created = client.post(f"{REPORTS}/saved", headers=world.admin_a, json={
        "name": "Q1 movement", "report_type": "MOVEMENT", "filter_params": filters,
    })
    assert created.status_code == 200, created.text
    body = created.json()

    assert body["is_private"] is True, "ברירת המחדל היא פרטי (החלטה מוצרית 4)"
    assert body["owner_user_id"] == world.admin_a_id
    assert body["company_id"] == "COMP-RPT-A"
    # dict ולא מחרוזת JSON - filter_params מאוחסן כטקסט אבל חוזר מפוענח.
    assert body["filter_params"] == filters


def test_owner_sees_their_own_private_saved_report(client, world):
    saved = _save(client, world.admin_a, "mine", is_private=True)

    listed = client.get(f"{REPORTS}/saved", headers=world.admin_a)
    assert [r["report_id"] for r in listed.json()] == [saved["report_id"]]
    assert client.get(f"{REPORTS}/saved/{saved['report_id']}",
                      headers=world.admin_a).status_code == 200


def test_another_admin_in_the_same_company_cannot_see_a_private_saved_report(client, world):
    """היום יש חשבון admin משותף אחד לחברה, ולכן "פרטי"="משותף" בפועל -
    אבל הכלל נאכף בקוד כבר עכשיו, וזו הבדיקה ששומרת עליו עד ש-v1.5.0 (RBAC)
    יהפוך אותו לנראה למשתמש."""
    saved = _save(client, world.admin_a, "mine only", is_private=True)

    listed = client.get(f"{REPORTS}/saved", headers=world.admin_a2)
    assert [r["report_id"] for r in listed.json()] == []
    assert client.get(f"{REPORTS}/saved/{saved['report_id']}",
                      headers=world.admin_a2).status_code == 404


def test_a_shared_saved_report_is_visible_to_every_admin_of_the_same_company(client, world):
    shared = _save(client, world.admin_a, "team view", is_private=False)

    listed = client.get(f"{REPORTS}/saved", headers=world.admin_a2)
    assert [r["report_id"] for r in listed.json()] == [shared["report_id"]]
    fetched = client.get(f"{REPORTS}/saved/{shared['report_id']}", headers=world.admin_a2)
    assert fetched.status_code == 200
    assert fetched.json()["is_private"] is False
    # ...ולא לחברה אחרת, גם כשהוא משותף.
    assert client.get(f"{REPORTS}/saved/{shared['report_id']}",
                      headers=world.admin_b).status_code == 404


def test_delete_is_owner_only_even_for_a_shared_report(client, world):
    """ברירת מחדל בטוחה: "משותף" הוא הרשאת *קריאה*, לא הרשאת מחיקה. 403
    ולא 404 כאן - הקיום מותר לדעת בתוך אותה חברה, הפעולה אסורה."""
    shared = _save(client, world.admin_a, "team view", is_private=False)

    forbidden = client.delete(f"{REPORTS}/saved/{shared['report_id']}", headers=world.admin_a2)
    assert forbidden.status_code == 403, forbidden.text
    assert world.db.get(SavedReport, shared["report_id"]) is not None

    deleted = client.delete(f"{REPORTS}/saved/{shared['report_id']}", headers=world.admin_a)
    assert deleted.status_code == 200, deleted.text
    assert world.db.get(SavedReport, shared["report_id"]) is None


def test_saved_report_rejects_a_report_type_outside_the_closed_vocabulary(client, world):
    resp = client.post(f"{REPORTS}/saved", headers=world.admin_a, json={
        "name": "bogus", "report_type": "NOT_A_REPORT", "filter_params": {},
    })
    assert resp.status_code == 400, resp.text
    assert "report_type" in resp.json()["detail"]


def test_saved_report_get_by_unknown_id_is_404(client, world):
    assert client.get(f"{REPORTS}/saved/does-not-exist",
                      headers=world.admin_a).status_code == 404
    assert client.delete(f"{REPORTS}/saved/does-not-exist",
                         headers=world.admin_a).status_code == 404


# ===================================================================
# QA-110-42 - חוזה ה-columns במעטפת ה-JSON.
#
# שלושת הצרכנים של דוח (מסך, CSV, PDF) חייבים להסכים על אותה רשימת עמודות.
# המסך צרך פעם ``Object.keys(rows[0])`` בעוד CSV/PDF צרכו את ``columns``, כך
# ששני מקורות היו חייבים להישאר מסונכרנים ידנית - ושחיקה שלהם מיישרת עמודת
# כסף מתחת לכותרת של עמודה אחרת, שגיאה שנראית נכונה. עכשיו השרת שולח את
# ``columns`` והמסך צורך אותו; הבדיקה נועלת את זה.
#
# שוויון-קבוצות ולא רק הכלה, בגלל ``extrasaction="ignore"`` ב-DictWriter
# (services/reports.py::rows_to_csv_bytes): מפתח שקיים בשורה ואינו ב-columns
# **נשמט בשקט מה-CSV**. זו אבידת דאטה בייצוא, לא אי-נוחות תצוגה.
# הדשבורד מוחרג במפורש - הוא מחזיר dict חופשי ולא ReportResult (JSON בלבד).
# ===================================================================

COLUMNED_REPORT_ENDPOINTS = {k: v for k, v in REPORT_ENDPOINTS.items() if k != "dashboard"}


@pytest.mark.parametrize("endpoint", sorted(COLUMNED_REPORT_ENDPOINTS))
def test_json_envelope_exposes_the_same_columns_the_csv_and_pdf_use(client, world, endpoint):
    body = _rows(client, endpoint, world.admin_a, **COLUMNED_REPORT_ENDPOINTS[endpoint])

    assert "columns" in body, (
        f"{endpoint}: המעטפת חייבת לשלוח columns - בלעדיו המסך נאלץ לגזור כותרות "
        "מסדר המפתחות של השורה, וזה צימוד שאף בדיקה לא שומרת"
    )
    assert body["columns"], f"{endpoint}: columns ריק"
    assert isinstance(body["columns"], list)


@pytest.mark.parametrize("endpoint", sorted(COLUMNED_REPORT_ENDPOINTS))
def test_every_row_key_is_declared_as_a_column_so_csv_drops_nothing(client, world, endpoint):
    body = _rows(client, endpoint, world.admin_a, **COLUMNED_REPORT_ENDPOINTS[endpoint])
    columns = set(body["columns"])

    for row in body["rows"]:
        assert set(row) == columns, (
            f"{endpoint}: מפתחות השורה אינם תואמים ל-columns. "
            f"חסר ב-columns (יישמט מה-CSV בשקט): {sorted(set(row) - columns)} · "
            f"מוצהר בלי דאטה (עמודה ריקה במסך): {sorted(columns - set(row))}"
        )
