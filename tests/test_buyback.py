"""רכישה עצמית ותיקון הנפקה (v1.2.0) - מפרט: docs/spec/v1.2.0.md.

הקובץ נפרד מ-test_cap_table.py (553 שורות לפני הגרסה הזו) בכוונה: שם יושבות
הבדיקות ששומרות על *טבלת ההון הקיימת*, וכאן אלה ששומרות על *היכולת לשנות
אותה*. ערבוב היה מטשטש איזו בדיקה נשברת כשמה.

מיפוי לקריטריוני הקבלה: ראו docs/qa/v1.2.0.md.
"""

import json
import re
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from backend.app.auth import hash_password
from backend.app.models import (
    Company, Employee, EmployeeStatus, OptionPool, User, UserRole, UserSession,
)
from backend.app.types import utcnow

API = "/api/v1"


# ===================================================================
# B3 - מכניקת הפרויקטור. בדיקת יחידה טהורה, בלי DB ובלי client:
# project_share_issuance מקבל רשימת אירועים ולא Session (ledger.py), ולכן
# הבדיקה המכנית של סדר הקיפול עולה אפס תשתית.
# ===================================================================

def _event(event_type: str, effective_date: str, **payload):
    """אירוע מזויף בצורת המינימום שהפרויקטור נוגע בו: event_type + payload
    כמחרוזת JSON, בדיוק כמו append_event שכותב json.dumps(default=str)."""
    return SimpleNamespace(
        event_type=event_type,
        effective_date=date.fromisoformat(effective_date),
        payload=json.dumps(payload, default=str),
    )


def _established(shares: float, issue_date: str = "2022-01-01"):
    return _event("SHARE_ISSUANCE_ESTABLISHED", issue_date, shares=shares,
                  shareholder_id="SH-1", share_class_id="SC-1", issue_date=issue_date)


def _adjusted(delta: float, effective_date: str, reason: str = "BUYBACK"):
    return _event("SHARE_ISSUANCE_ADJUSTED", effective_date,
                  delta_shares=delta, reason=reason)


def test_projector_returns_none_when_there_are_no_events_at_all():
    """None = "אין היסטוריית ledger כלל" ולעולם לא 0. cap_table קורא את זה
    כתקלת שלמות ומסמן partial - ואם None היה מייצג גם "בוטל", הדגל היה הופך
    לרעש בכל חברה שאי פעם ביטלה מניה."""
    from backend.app.services.ledger import project_share_issuance

    assert project_share_issuance([]) is None


def test_projector_folds_a_base_event_alone():
    from backend.app.services.ledger import project_share_issuance

    state = project_share_issuance([_established(1000.0)])

    assert state["shares"] == 1000.0
    assert state["issue_date"] == date(2022, 1, 1)


def test_projector_subtracts_a_signed_delta_dated_after_the_issuance():
    from backend.app.services.ledger import project_share_issuance

    state = project_share_issuance([_established(1000.0), _adjusted(-250.0, "2023-05-01")])

    assert state["shares"] == 750.0


def test_projector_accumulates_several_deltas():
    """דלתאות מתחברות תחת append-only - שתי רכישות חלקיות ותיקון כלפי מעלה."""
    from backend.app.services.ledger import project_share_issuance

    state = project_share_issuance([
        _established(1000.0),
        _adjusted(-250.0, "2023-05-01"),
        _adjusted(-100.0, "2023-09-01"),
        _adjusted(50.0, "2024-01-01", reason="CORRECTION"),
    ])

    assert state["shares"] == 700.0


def test_projector_projects_a_fully_bought_back_lot_to_zero_and_not_to_none():
    """קריטריון 5: מנה שנרכשה במלואה היא עובדה נכונה וידועה - 0.0, לא None.
    None היה מדליק partial ומסמן תקלת שלמות שלא קיימת."""
    from backend.app.services.ledger import project_share_issuance

    state = project_share_issuance([_established(400.0), _adjusted(-400.0, "2023-05-01")])

    assert state is not None
    assert state["shares"] == 0.0


def test_a_delta_dated_before_the_base_event_is_not_swallowed_by_it():
    """*** הבדיקה שבגללה pending_delta קיים. ***

    הקיפול ממוין לפי (effective_date, sequence_no), ואירוע הבסיס של
    ShareIssuance מתועד ב-issue_date אמיתי ולא ב-LEDGER_EPOCH. לכן דלתא
    שתאריכה מוקדם יותר מגיעה *לפני* הבסיס במיון. בצורת ה"דלג" של שאר
    הפרויקטורים היא הייתה נבלעת פעמיים והפרויקציה הייתה מחזירה 1000 -
    הסכום המקורי המלא - בעוד העמודה המוטטת כבר הופחתה ל-800.
    """
    from backend.app.services.ledger import project_share_issuance

    events = [_adjusted(-200.0, "2021-06-01"), _established(1000.0, "2022-01-01")]

    state = project_share_issuance(events)

    assert state["shares"] == 800.0, (
        "אירוע הבסיס דרס דלתא שכבר קופלה - זו הסטייה הקבועה בין העמודה ל-ledger"
    )


def test_a_delta_without_any_base_event_leaves_the_state_none():
    """דלתא בלי בסיס אינה החזקה. היא נשארת צבורה ולא מומצאת ממנה שורה יש-מאין -
    אחרת ייבוא פגום היה מייצר בעל מניות רפאים עם החזקה שלילית."""
    from backend.app.services.ledger import project_share_issuance

    assert project_share_issuance([_adjusted(-200.0, "2021-06-01")]) is None


# ===================================================================
# B4-B9 - קריטריוני הקבלה מקצה לקצה, דרך ה-API.
# הפיקסטורות מקומיות ולא משותפות עם test_cap_table.py בכוונה: עולם הרכישה
# העצמית צריך פול *עם* היסטוריית ledger (אחרת כל תמונת מצב היסטורית מדליקה
# partial מצד הפול ומסתירה את מה שנבדק כאן), ושתי חברות להוכחת בידוד.
# ===================================================================

def _user(db, user_id, role, **ids):
    pw_hash, salt = hash_password("Demo1234!")
    u = User(user_id=user_id, username=f"{user_id.lower()}@test.example",
             password_hash=pw_hash, password_salt=salt, role=role, is_active=True, **ids)
    db.add(u)
    db.flush()
    return u


def _token(db, user):
    token = f"tok-{user.user_id}"
    db.add(UserSession(token=token, user_id=user.user_id, expires_at=utcnow() + timedelta(hours=1)))
    db.flush()
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def bb(client, db_session):
    from backend.app.services.ledger import LEDGER_EPOCH, append_event, record_ownership

    db = db_session
    db.add_all([
        Company(company_id="COMP-BB-A", name="Alpha", country_code="IL", total_authorized_shares=10000.0),
        Company(company_id="COMP-BB-B", name="Beta", country_code="IL"),
    ])
    db.add(Employee(employee_id="BB-EMP-1", company_id="COMP-BB-A", first_name="Dana",
                    last_name="Shani", email="bb1@alpha.example", country_code="IL",
                    status=EmployeeStatus.ACTIVE, hire_date=date(2020, 1, 1)))
    db.add(OptionPool(pool_id="BB-POOL-A", company_id="COMP-BB-A", total_shares=1000.0,
                      allocated_shares=0.0, unallocated_shares=1000.0))
    db.flush()

    # לפול יש היסטוריית ledger, כמו על דאטה אמיתית אחרי backfill_ledger.
    record_ownership(db, aggregate_id="BB-POOL-A", aggregate_type="OptionPool", company_id="COMP-BB-A")
    append_event(db, event_type="POOL_BALANCE_ESTABLISHED", aggregate_type="OptionPool",
                 aggregate_id="BB-POOL-A", effective_date=LEDGER_EPOCH,
                 payload={"allocated_shares": 0.0, "unallocated_shares": 1000.0, "total_shares": 1000.0})
    db.flush()

    admin_a = _token(db, _user(db, "U-BB-ADMIN-A", UserRole.COMPANY_ADMIN, company_id="COMP-BB-A"))
    admin_b = _token(db, _user(db, "U-BB-ADMIN-B", UserRole.COMPANY_ADMIN, company_id="COMP-BB-B"))
    emp = _token(db, _user(db, "U-BB-EMP", UserRole.EMPLOYEE, employee_id="BB-EMP-1"))

    sc = client.post(f"{API}/admin/share-classes", headers=admin_a,
                     json={"name": "Common", "class_type": "COMMON", "seniority_order": 10}).json()
    sh = client.post(f"{API}/admin/shareholders", headers=admin_a,
                     json={"name": "Founder One", "shareholder_type": "FOUNDER",
                           "employee_id": "BB-EMP-1"}).json()
    lot = client.post(f"{API}/admin/share-issuances", headers=admin_a, json={
        "shareholder_id": sh["shareholder_id"], "share_class_id": sc["share_class_id"],
        "shares": 1000.0, "issue_date": "2022-01-01"}).json()

    return SimpleNamespace(db=db, admin_a=admin_a, admin_b=admin_b, emp=emp,
                           share_class=sc, shareholder=sh, lot=lot,
                           lot_id=lot["share_issuance_id"])


def _preview(client, bb, **overrides):
    body = {"share_issuance_id": bb.lot_id, "shares": 250.0,
            "effective_date": "2023-05-01", "reason": "BUYBACK"}
    body.update(overrides)
    return client.post(f"{API}/admin/cap-table/buyback/preview", headers=bb.admin_a, json=body)


def _execute(client, bb, **overrides):
    """מריץ תצוגה מקדימה ואז ביצוע, בדיוק כמו המסך - כדי ש-expected_sequence_no
    יהיה זה שהתצוגה החזירה ולא מספר קסם בתוך הבדיקה."""
    preview_keys = {"share_issuance_id", "shares", "effective_date", "reason"}
    preview = _preview(client, bb, **{k: v for k, v in overrides.items() if k in preview_keys})

    body = {"share_issuance_id": bb.lot_id, "shares": 250.0,
            "effective_date": "2023-05-01", "reason": "BUYBACK", "confirm_shares": 250.0,
            "expected_sequence_no": preview.json().get("expected_sequence_no", 0)}
    if "shares" in overrides and "confirm_shares" not in overrides:
        body["confirm_shares"] = overrides["shares"]
    body.update(overrides)
    return client.post(f"{API}/admin/cap-table/buyback", headers=bb.admin_a, json=body)


def test_buyback_reduces_the_lot_and_both_headline_numbers_by_exactly_n(client, bb):
    """קריטריון 1."""
    before = client.get(f"{API}/admin/cap-table/snapshot", headers=bb.admin_a,
                        params={"as_of": "2023-05-01"}).json()

    resp = _execute(client, bb)
    assert resp.status_code == 200, resp.text

    after = client.get(f"{API}/admin/cap-table/snapshot", headers=bb.admin_a,
                       params={"as_of": "2023-05-01"}).json()
    assert after["outstanding_shares"] == before["outstanding_shares"] - 250.0
    assert after["fully_diluted_shares"] == before["fully_diluted_shares"] - 250.0
    assert resp.json()["lot_after"] == 750.0


def test_retired_shares_free_the_authorized_cap_again(client, bb):
    """קריטריון 2 - ה1: המניות נמחקות, לא הופכות למניות אוצר."""
    filler = client.post(f"{API}/admin/share-issuances", headers=bb.admin_a, json={
        "shareholder_id": bb.shareholder["shareholder_id"],
        "share_class_id": bb.share_class["share_class_id"],
        "shares": 9000.0, "issue_date": "2022-06-01"})
    assert filler.status_code == 200  # 1000 + 9000 = 10000, בדיוק התקרה

    blocked = client.post(f"{API}/admin/share-issuances", headers=bb.admin_a, json={
        "shareholder_id": bb.shareholder["shareholder_id"],
        "share_class_id": bb.share_class["share_class_id"],
        "shares": 250.0, "issue_date": "2023-01-01"})
    assert blocked.status_code == 400

    assert _execute(client, bb).status_code == 200

    now_allowed = client.post(f"{API}/admin/share-issuances", headers=bb.admin_a, json={
        "shareholder_id": bb.shareholder["shareholder_id"],
        "share_class_id": bb.share_class["share_class_id"],
        "shares": 250.0, "issue_date": "2023-06-01"})
    assert now_allowed.status_code == 200, now_allowed.text


def test_a_snapshot_before_the_buyback_date_is_unchanged_by_it(client, bb):
    """קריטריון 3 - הוכחת הבי-טמפורליות בשורה אחת."""
    as_of = {"as_of": "2022-06-01"}
    before = client.get(f"{API}/admin/cap-table/snapshot", headers=bb.admin_a, params=as_of).json()

    assert _execute(client, bb).status_code == 200

    after = client.get(f"{API}/admin/cap-table/snapshot", headers=bb.admin_a, params=as_of).json()
    assert after == before, "רכישה עצמית שינתה תמונת מצב שקדמה לה"


def test_buying_back_more_than_the_lot_holds_is_rejected_before_anything_is_written(client, bb):
    """קריטריון 4."""
    from backend.app.models import ShareIssuance

    resp = _execute(client, bb, shares=1001.0)
    assert resp.status_code == 400
    assert "1000" in resp.json()["detail"]
    assert bb.db.get(ShareIssuance, bb.lot_id).shares == 1000.0


def test_a_fully_bought_back_lot_stays_visible_as_a_zero_row(client, bb):
    """קריטריון 5: השמטה הייתה הופכת (הוחזק ואיננו) ל(מעולם לא היה)."""
    resp = _execute(client, bb, shares=1000.0)
    assert resp.status_code == 200, resp.text

    snapshot = client.get(f"{API}/admin/cap-table/snapshot", headers=bb.admin_a,
                          params={"as_of": "2023-05-01"}).json()
    rows = [r for r in snapshot["by_shareholder_and_class"]
            if r["shareholder_id"] == bb.shareholder["shareholder_id"]]
    assert len(rows) == 1
    assert rows[0]["shares"] == 0.0
    assert snapshot["partial"] is False


def test_replaying_the_same_request_does_not_subtract_twice(client, bb):
    """קריטריון 6 - שידור חוזר. sequence_no זז אחרי הביצוע הראשון."""
    from backend.app.models import ShareIssuance

    first = _execute(client, bb)
    assert first.status_code == 200
    stale_sequence = 1

    replay = client.post(f"{API}/admin/cap-table/buyback", headers=bb.admin_a, json={
        "share_issuance_id": bb.lot_id, "shares": 250.0, "effective_date": "2023-05-01",
        "reason": "BUYBACK", "confirm_shares": 250.0, "expected_sequence_no": stale_sequence})
    assert replay.status_code == 409
    assert bb.db.get(ShareIssuance, bb.lot_id).shares == 750.0


def test_an_effective_date_before_the_issuance_is_rejected(client, bb):
    """קריטריון 7 - דרישה קשיחה, בלי חלופה מרוככת."""
    resp = _execute(client, bb, effective_date="2021-12-31")
    assert resp.status_code == 400
    assert "before the issuance date" in resp.json()["detail"]


def test_the_projection_and_the_mutable_column_agree_after_a_legal_sequence(client, bb):
    """קריטריון 7, הצד המכני: אחרי כל רצף אירועים חוקי, project() והעמודה
    חייבים להחזיר את אותו מספר. זו האינווריאנטה שכל ה-ledger קיים בשבילה."""
    from backend.app.models import ShareIssuance
    from backend.app.services.ledger import project

    for shares, eff in [(100.0, "2022-03-01"), (50.0, "2023-01-01"), (25.0, "2024-07-07")]:
        resp = _execute(client, bb, shares=shares, effective_date=eff)
        assert resp.status_code == 200, resp.text

    row = bb.db.get(ShareIssuance, bb.lot_id)
    state = project(bb.db, "ShareIssuance", bb.lot_id)
    assert state["shares"] == row.shares == 825.0


def test_the_employee_role_is_refused(client, bb):
    """קריטריון 8, צד התפקידים."""
    resp = client.post(f"{API}/admin/cap-table/buyback/preview", headers=bb.emp,
                       json={"share_issuance_id": bb.lot_id, "shares": 10.0,
                             "effective_date": "2023-05-01", "reason": "BUYBACK"})
    assert resp.status_code == 403


def test_another_companys_lot_is_404_and_indistinguishable_from_a_missing_one(client, bb):
    """קריטריון 8, צד הטננטים: 403 כאן היה אורקל קיום חוצה-טננטים."""
    foreign = client.post(f"{API}/admin/cap-table/buyback/preview", headers=bb.admin_b,
                          json={"share_issuance_id": bb.lot_id, "shares": 10.0,
                                "effective_date": "2023-05-01", "reason": "BUYBACK"})
    invented = client.post(f"{API}/admin/cap-table/buyback/preview", headers=bb.admin_b,
                           json={"share_issuance_id": "ISS-NOPE", "shares": 10.0,
                                 "effective_date": "2023-05-01", "reason": "BUYBACK"})
    assert foreign.status_code == 404
    assert invented.status_code == 404
    assert foreign.json() == invented.json()


def test_preview_writes_nothing_at_all(client, bb):
    """קריטריון 9 - תצוגה ואז נטישה."""
    from backend.app.models import LedgerEvent, ShareIssuance

    before_events = bb.db.query(LedgerEvent).count()

    resp = _preview(client, bb)
    assert resp.status_code == 200, resp.text
    assert resp.json()["lot_after"] == 750.0

    assert bb.db.query(LedgerEvent).count() == before_events
    assert bb.db.get(ShareIssuance, bb.lot_id).shares == 1000.0


def test_the_preview_shows_both_the_lot_and_the_total_holding(client, bb):
    """§6 - האדמין בוחר מנה, אבל התוצאה שהוא שופט היא ההחזקה הכוללת.
    שתי הרמות על אותו מסך, לא אחת מהן."""
    second = client.post(f"{API}/admin/share-issuances", headers=bb.admin_a, json={
        "shareholder_id": bb.shareholder["shareholder_id"],
        "share_class_id": bb.share_class["share_class_id"],
        "shares": 400.0, "issue_date": "2022-09-01"})
    assert second.status_code == 200

    body = _preview(client, bb).json()

    assert body["lot_before"] == 1000.0 and body["lot_after"] == 750.0
    assert body["holding_before"] == 1400.0 and body["holding_after"] == 1150.0
    assert body["shareholder"]["employee_id"] == "BB-EMP-1"


def test_execution_refuses_a_sequence_number_that_was_never_current(client, bb):
    """קריטריון 10 - הביצוע לא מקבל מספרים מהדפדפן. מספר *עתידי*: הצד הזול."""
    resp = client.post(f"{API}/admin/cap-table/buyback", headers=bb.admin_a, json={
        "share_issuance_id": bb.lot_id, "shares": 250.0, "effective_date": "2023-05-01",
        "reason": "BUYBACK", "confirm_shares": 250.0, "expected_sequence_no": 99})
    assert resp.status_code == 409


def test_execution_refuses_a_preview_invalidated_by_an_intervening_write(client, bb):
    """קריטריון 10, התרחיש האמיתי (QA-120-12) - פער אימות 3 בסקירה 12.

    הבדיקה שמעליה מעבירה 99, מספר שלא היה נוכחי לעולם, ולכן כל השוואת ``!=`` על
    כל ערך הייתה מספקת אותה. כאן הסימן *היה* נוכחי, נלקח מתצוגה מקדימה אמיתית,
    והתיישן מכתיבה מתערבת - וזה המצב שהאדמין באמת פוגש כששני מסכים פתוחים."""
    from backend.app.models import ShareIssuance

    stale = _preview(client, bb, shares=100.0).json()["expected_sequence_no"]

    intervening = _execute(client, bb, shares=50.0)
    assert intervening.status_code == 200, intervening.text

    resp = client.post(f"{API}/admin/cap-table/buyback", headers=bb.admin_a, json={
        "share_issuance_id": bb.lot_id, "shares": 100.0, "effective_date": "2023-05-01",
        "reason": "BUYBACK", "confirm_shares": 100.0, "expected_sequence_no": stale})

    assert resp.status_code == 409
    assert "re-run the preview" in resp.json()["detail"]
    # ההפחתה המתערבת בלבד. 100 לא הופחתו על סמך סימן מיושן.
    assert bb.db.get(ShareIssuance, bb.lot_id).shares == 950.0


def test_a_unique_constraint_collision_on_commit_is_409_and_not_an_unhandled_500(client, bb, monkeypatch):
    """אזהרה 5 בסקירה 12 - הכשל התחרותי.

    שני ביצועים *במקביל* קוראים את אותו max(sequence_no), שניהם עוברים את בדיקת
    ה-409, ושניהם מנסים להכניס N+1. ה-UniqueConstraint מונע את ההפחתה הכפולה,
    אבל בלי לכידה הוא חוזר כ-500 שהלקוח אינו יכול להבחין בינו לתקלת שרת - ולכן
    ינסה שוב. התנגשות אמיתית אינה ניתנת לתזמון בבדיקה סדרתית (כל דרך לייצר את
    השורה המתנגשת מקדימה גם את max ומפילה 409 מוקדם יותר), ולכן ה-commit הוא
    שמזריק את החריגה - הענף הנבדק הוא הטיפול, לא ה-race עצמו."""
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session

    from backend.app.models import ShareIssuance

    original_commit = Session.commit
    raised = {"once": False}

    def commit_that_collides_once(self):
        if not raised["once"]:
            raised["once"] = True
            raise IntegrityError(
                "INSERT INTO ledger_events ...", {},
                Exception("UNIQUE constraint failed: ledger_events.aggregate_id, "
                          "ledger_events.sequence_no"))
        return original_commit(self)

    monkeypatch.setattr(Session, "commit", commit_that_collides_once)

    resp = _execute(client, bb)

    assert raised["once"], "ה-commit לא נקרא בכלל - הבדיקה אינה בודקת את מה שהיא מתיימרת"
    assert resp.status_code == 409, resp.text
    assert "re-run the preview" in resp.json()["detail"]
    # *** למה אין כאן טענה על העמודה ***: db.rollback() בנתיב הכשל מגלגל את
    # הטרנזקציה של הבדיקה כולה (tests/conftest.py מריץ כל בדיקה בתוך טרנזקציה
    # אחת), כלומר גם את הפיקסטורה - וההיעדרות של השורה כאן היא תוצר של מבנה
    # הבדיקה ולא של קוד הייצור. אי-ההפחתה הכפולה נאכפת ב-UniqueConstraint
    # ומכוסה ב-test_replaying_the_same_request_does_not_subtract_twice.
    assert bb.db.get(ShareIssuance, bb.lot_id) is None


def test_the_amount_must_be_retyped_exactly(client, bb):
    """§6 - אי-ההפיכות נישאת באקט האישור, לא בצבע."""
    resp = _execute(client, bb, confirm_shares=249.0)
    assert resp.status_code == 400


# פער אימות 4 בסקירה 12: הטענה הייתה רשימת-שלילה בת חמש מילים על מפתחות ברמה
# העליונה בלבד, כלומר שדה מס מספרי בשם אחר - או מקונן בתוך company_after - היה
# עובר אותה. עכשיו הסריקה רקורסיבית ומבוססת דפוס-שם: מה שנאסר הוא *מספר* שנקרא
# כמו מס, בכל עומק. השדות הלגיטימיים (tax_treatment, tax_reason_code) הם מחרוזות
# ולכן אינם נתפסים - וזו בדיוק ההבחנה שהמפרט דורש: קוד, לא סכום.
_TAX_NUMERIC_HINT = re.compile(r"tax|withhold|gain|profit|rate|liab|deduct", re.IGNORECASE)


def _numeric_tax_fields(node, path="") -> list:
    if isinstance(node, dict):
        found = []
        for key, value in node.items():
            here = f"{path}.{key}"
            if (_TAX_NUMERIC_HINT.search(key) and isinstance(value, (int, float))
                    and not isinstance(value, bool)):
                found.append(f"{here}={value!r}")
            found += _numeric_tax_fields(value, here)
        return found
    if isinstance(node, list):
        return [f for i, item in enumerate(node) for f in _numeric_tax_fields(item, f"{path}[{i}]")]
    return []


@pytest.mark.parametrize("path", ["/admin/cap-table/buyback/preview", "/admin/cap-table/buyback"])
def test_no_route_returns_a_numeric_tax_field_and_never_zero(client, bb, path):
    """קריטריונים 11+12 - אפס מס בכל מסלול, בכל עומק."""
    from backend.app.models import ExerciseTaxRecord

    body = {"share_issuance_id": bb.lot_id, "shares": 250.0, "effective_date": "2023-05-01",
            "reason": "BUYBACK"}
    if not path.endswith("preview"):
        body.update({"confirm_shares": 250.0, "expected_sequence_no": 1})

    resp = client.post(f"{API}{path}", headers=bb.admin_a, json=body)
    assert resp.status_code == 200, resp.text
    out = resp.json()

    assert out["tax_treatment"] == "NOT_COMPUTED"
    assert out["tax_reason_code"]
    assert isinstance(out["tax_treatment"], str) and isinstance(out["tax_reason_code"], str)
    leaked = _numeric_tax_fields(out)
    assert not leaked, f"שדה מס מספרי דלף לתגובה: {leaked}"
    assert bb.db.query(ExerciseTaxRecord).count() == 0


def test_an_upward_correction_beyond_the_authorized_cap_is_rejected(client, bb):
    """קריטריון 15 - הכיוון שאיש לא בדק. בדיקת התקרה קיימת בקוד בנקודה אחת
    בלבד (create_share_issuance), ובלי החזרה עליה כאן תיקון כלפי מעלה היה
    עוקף אילוץ עסקי שכל מסלול אחר מקיים."""
    filler = client.post(f"{API}/admin/share-issuances", headers=bb.admin_a, json={
        "shareholder_id": bb.shareholder["shareholder_id"],
        "share_class_id": bb.share_class["share_class_id"],
        "shares": 9000.0, "issue_date": "2022-06-01"})
    assert filler.status_code == 200

    resp = _execute(client, bb, shares=-1.0, reason="CORRECTION")
    assert resp.status_code == 400
    assert "total_authorized_shares" in resp.json()["detail"]


def test_an_upward_correction_is_capped_even_when_the_filler_is_dated_after_it(client, bb):
    """*** רגרסיית חוסם 1 (סקירה 12). ***

    הבדיקה שמעליה מתארכת את ההנפקה הממלאת ל-2022-06-01, כלומר *לפני* ה-
    effective_date - ולכן החלון שבו הבאג חי לא נפתח בה, והיא הייתה ירוקה גם כשה-
    תקרה נפרצה. כאן הממלא מתוארך *אחרי* ה-effective_date: הנוסחה הישנה השוותה
    מול snapshot חתוך ב-effective_date, שאינו כולל אותו, וקיבלה תיקון כלפי מעלה
    שמעביר את סכום העמודה מעל התקרה. 10,000 מונפקות מתוך תקרה 10,000, ובכל זאת
    +500 התקבלו והעמודה הגיעה ל-10,500.
    """
    from backend.app.models import ShareIssuance

    filler = client.post(f"{API}/admin/share-issuances", headers=bb.admin_a, json={
        "shareholder_id": bb.shareholder["shareholder_id"],
        "share_class_id": bb.share_class["share_class_id"],
        "shares": 9000.0, "issue_date": "2024-01-01"})
    assert filler.status_code == 200  # 1000 + 9000 = 10000, בדיוק התקרה

    # effective_date חוקי (>= issue_date של המנה) ומוקדם מההנפקה הממלאת - בדיוק
    # מה שקריטריון 7 מתיר, וזה מה שהפך את זה לניצול ולא לתרחיש תיאורטי.
    resp = _execute(client, bb, shares=-500.0, effective_date="2022-06-01", reason="CORRECTION")

    assert resp.status_code == 400, resp.text
    assert "total_authorized_shares" in resp.json()["detail"]
    assert bb.db.get(ShareIssuance, bb.lot_id).shares == 1000.0
    snapshot = client.get(f"{API}/admin/cap-table/snapshot", headers=bb.admin_a).json()
    assert snapshot["outstanding_pct_of_authorized"] == 1.0, "סכום העמודה חרג מהתקרה"


def test_the_receipt_headline_numbers_are_the_companys_numbers_now(client, bb):
    """*** רגרסיית חוסם 2 (סקירה 12), והכרעת המשתתף 17/08/2026: שעון אחד. ***

    ההנפקה הנוספת מתוארכת *אחרי* ה-effective_date, וזה מה שמפריד בין שני
    השעונים: הנוסחה הישנה חישבה את מספרי הכותרת מ-snapshot חתוך ב-effective_date,
    שאינו כולל אותה, והקבלה הצהירה על מונפק שאינו המונפק בפועל - על מסך אישור
    בלתי-הפיך. ההשוואה היא מול GET snapshot *טרי* (בלי as_of), כלומר מול המקור
    שהמסך עצמו מציג באותו רגע.
    """
    from backend.app.types import business_today

    later = client.post(f"{API}/admin/share-issuances", headers=bb.admin_a, json={
        "shareholder_id": bb.shareholder["shareholder_id"],
        "share_class_id": bb.share_class["share_class_id"],
        "shares": 500.0, "issue_date": "2024-01-01"})
    assert later.status_code == 200

    receipt = _execute(client, bb, effective_date="2023-05-01")
    assert receipt.status_code == 200, receipt.text
    body = receipt.json()

    fresh = client.get(f"{API}/admin/cap-table/snapshot", headers=bb.admin_a).json()
    assert body["company_after"]["outstanding_shares"] == fresh["outstanding_shares"]
    assert body["company_after"]["fully_diluted_shares"] == fresh["fully_diluted_shares"]
    assert body["company_as_of"] == business_today().isoformat()
    # ולא רק "שווים": 1000 + 500 - 250. הנוסחה הישנה החזירה 750 בשני השדות.
    assert body["company_after"]["outstanding_shares"] == 1250.0


def test_the_preview_puts_the_lot_and_the_company_on_the_same_clock(client, bb):
    """אותו חוסם, בצד התצוגה המקדימה: lot_before מגיע מקיפול מלא בלי חתך as-of,
    ולכן מספרי החברה חייבים להיות על אותו שעון. שני שעונים בדיף אחד, תחת תוויות
    לא-מסויגות, הם ההפך מ"דיף של טבלת הון" שדורש §6."""
    later = client.post(f"{API}/admin/share-issuances", headers=bb.admin_a, json={
        "shareholder_id": bb.shareholder["shareholder_id"],
        "share_class_id": bb.share_class["share_class_id"],
        "shares": 500.0, "issue_date": "2024-01-01"})
    assert later.status_code == 200

    body = _preview(client, bb, effective_date="2023-05-01").json()

    # ההחזקה הכוללת והמנה נמדדות "עכשיו", ולכן גם סך החברה: 1000 + 500.
    assert body["lot_before"] == 1000.0
    assert body["holding_before"] == 1500.0
    assert body["company_before"]["outstanding_shares"] == 1500.0


def test_a_fractional_share_amount_is_rejected(client, bb):
    """אזהרה 7: זו הגרסה הראשונה שמפחיתה מניות, וכל שדה כספי הוא Float (חוב א').
    ה-CHECK של option_pools הוא שוויון צף מדויק שמחזיק רק כל עוד הכמויות שלמות."""
    from backend.app.models import ShareIssuance

    resp = _execute(client, bb, shares=0.5)
    assert resp.status_code == 400
    assert "whole number" in resp.json()["detail"]
    assert bb.db.get(ShareIssuance, bb.lot_id).shares == 1000.0


def test_the_execution_stamps_a_company_event_id_into_the_event_payload(client, bb):
    """מפרט §7: מפתח הקורלציה נטבע ב-payload. תחת append-only אי אפשר להוסיפו
    בדיעבד, ולכן הוא נכתב מהאירוע הראשון (הכרעת המשתתף, 17/08/2026)."""
    from backend.app.models import LedgerEvent

    body = _execute(client, bb).json()
    assert body["company_event_id"]

    event = bb.db.get(LedgerEvent, body["ledger_event_id"])
    assert json.loads(event.payload)["company_event_id"] == body["company_event_id"]


def test_a_lot_without_ledger_history_is_rejected_not_silently_marked(client, bb):
    """קריטריון 16 - אחרת אירוע הדלתא נוחת כאירוע ראשון, הפרויקטור לעולם לא
    יחיל אותו, והעמודה תופחת בלעדיו."""
    from backend.app.models import ShareIssuance

    bb.db.add(ShareIssuance(
        share_issuance_id="ISS-BB-ORPHAN", company_id="COMP-BB-A",
        shareholder_id=bb.shareholder["shareholder_id"],
        share_class_id=bb.share_class["share_class_id"],
        shares=500.0, issue_date=date(2022, 1, 1)))
    bb.db.flush()

    resp = client.post(f"{API}/admin/cap-table/buyback", headers=bb.admin_a, json={
        "share_issuance_id": "ISS-BB-ORPHAN", "shares": 100.0, "effective_date": "2023-05-01",
        "reason": "BUYBACK", "confirm_shares": 100.0, "expected_sequence_no": 0})
    assert resp.status_code == 400
    assert "no ledger history" in resp.json()["detail"]
    assert bb.db.get(ShareIssuance, "ISS-BB-ORPHAN").shares == 500.0


def test_the_audit_trail_records_before_and_after_and_the_actor(client, bb):
    """§7: פעולה הרסנית חייבת לרשום before, after והמשתמש המבצע - שלושת
    מסלולי טבלת ההון הקיימים העבירו after בלבד."""
    from backend.app.models import AuditLog

    assert _execute(client, bb).status_code == 200

    entry = (bb.db.query(AuditLog)
             .filter(AuditLog.entity_id == bb.lot_id, AuditLog.action == "UPDATE")
             .one())
    assert entry.actor_user_id == "U-BB-ADMIN-A"
    assert json.loads(entry.before_value)["shares"] == 1000.0
    assert json.loads(entry.after_value)["shares"] == 750.0
