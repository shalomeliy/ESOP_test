from pydantic import BaseModel, ConfigDict
from datetime import date, datetime
from typing import Any, Dict, Optional, List
from backend.app.models import EmployeeStatus, GrantType

class EmployeeStatusUpdate(BaseModel):
    status: EmployeeStatus
    effective_date: date
    return_unvested_to_pool: bool = True

class ExerciseSimulationRequest(BaseModel):
    grant_id: str
    exercise_date: date
    options_to_exercise: float

class ExerciseSimulationResponse(BaseModel):
    grant_id: str
    is_trustee_holding_period_met: bool
    holding_period_end_date: Optional[date] = None
    current_stock_price: float
    total_exercise_cost: float
    estimated_tax_amount: float
    applied_tax_rate: float
    tax_rule_source: str
    tax_calculation_method: str
    tax_table_effective_date: Optional[date] = None
    # v0.7.0: מזהה ה-TaxRulePack שהופעל בפועל - מאפשר לאתר בדיוק לפי איזו
    # גרסת כלל חושב סכום נתון, בלי לשחזר את זה מ-3 שדות בנפרד.
    tax_rule_pack_id: str
    is_within_post_termination_window: bool
    post_termination_exercise_deadline: Optional[date] = None

class CreateGrantRequest(BaseModel):
    employee_id: str
    pool_id: str
    grant_type: GrantType
    total_options: float
    exercise_price: float
    grant_date: date
    trustee_id: Optional[str] = None
    currency: str = "ILS"
    cliff_months: int = 12
    total_months: int = 48
    post_termination_window_days: int = 90

class CreateGrantResponse(BaseModel):
    grant_id: str
    employee_id: str
    pool_id: str
    total_options: float
    vesting_schedule_id: str
    pool_allocated_shares: float
    pool_unallocated_shares: float


# ===================================================================
# Auth
# ===================================================================

class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    token: str
    role: str
    display_name: str
    company_id: Optional[str] = None
    trustee_id: Optional[str] = None
    employee_id: Optional[str] = None
    # True כשהמשתמש עדיין על הסיסמה החד-פעמית שהוקצתה לו. הקליינט אמור לחסום
    # ניווט לכל מקום מלבד מסך "שנה סיסמה" עד שזה יורד ל-False.
    must_change_password: bool = False


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


# ===================================================================
# Company (admin) - Employee CRUD
# ===================================================================

class EmployeeCreateRequest(BaseModel):
    first_name: str
    last_name: str
    email: str
    country_code: str
    hire_date: date
    birth_date: Optional[date] = None

class EmployeeUpdateRequest(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    country_code: Optional[str] = None
    birth_date: Optional[date] = None

class EmployeeOut(BaseModel):
    employee_id: str
    company_id: Optional[str] = None
    first_name: str
    last_name: str
    email: str
    country_code: str
    status: EmployeeStatus
    hire_date: date
    termination_date: Optional[date] = None
    birth_date: Optional[date] = None

    model_config = ConfigDict(from_attributes=True)


class EmployeeCreateResponse(EmployeeOut):
    """זהה ל-EmployeeOut, בתוספת הסיסמה החד-פעמית - מוחזרת פעם אחת בלבד,
    מתגובת היצירה. אף endpoint אחר לא חושף אותה (רק ה-hash נשמר)."""
    temporary_password: str


# ===================================================================
# Company profile
# ===================================================================

class CompanyUpdateRequest(BaseModel):
    name: Optional[str] = None
    country_code: Optional[str] = None
    # v1.0.0 שלב א: Optional ולא ברירת מחדל - None משמעו "לא נגיעה בערך הקיים",
    # אותה מוסכמה כמו שאר השדות ב-request הזה, לא "אפס מניות מאושרות".
    total_authorized_shares: Optional[float] = None
    # v1.0.1: אותה מוסכמה בדיוק - None = לא נגיעה בערך הקיים, לא "אפס ימים".
    acknowledgment_window_days: Optional[int] = None

class CompanyOut(BaseModel):
    company_id: str
    name: str
    country_code: str
    is_active: bool
    # None = לא הוזן עדיין (חברות קיימות שנזרעו לפני v1.0.0) - ראו הערת models.py.Company.
    total_authorized_shares: Optional[float] = None
    # None = אין override, המסמכים משתמשים ב-ACKNOWLEDGMENT_WINDOW_DAYS הגלובלי.
    acknowledgment_window_days: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)

class DocumentAcknowledgmentWindowOverrideOut(BaseModel):
    template_type: str
    # Optional - ה-PUT endpoint (upsert_acknowledgment_window_override) מחזיר
    # את אותו schema גם על מחיקה (window_days=None), כדי לא לדלוף override_id/
    # company_id (שדות פנימיים) דרך תגובת ORM גולמית בענפי create/update.
    window_days: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)

class DocumentAcknowledgmentWindowOverrideUpsertRequest(BaseModel):
    # None = מחיקת ה-override (חזרה לירושה מ-Company.acknowledgment_window_days) -
    # אותה מוסכמה בדיוק כמו שאר שדות ה-override בפרויקט הזה. ערך חיובי = קבע/עדכן.
    window_days: Optional[int] = None

class GrantOut(BaseModel):
    grant_id: str
    employee_id: str
    pool_id: str
    trustee_id: Optional[str] = None
    grant_date: date
    grant_type: GrantType
    total_options: float
    exercise_price: float
    currency: Optional[str] = None
    trustee_deposit_date: Optional[date] = None
    post_termination_window_days: int

    model_config = ConfigDict(from_attributes=True)

class PoolOut(BaseModel):
    pool_id: str
    company_id: str
    total_shares: float
    allocated_shares: float
    unallocated_shares: float
    # v1.0.0 שלב א: None לפולים קיימים/מזורעים שנוצרו לפני שיוך סוגי מניה - ראו
    # models.py.OptionPool.share_class_id.
    share_class_id: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class CreatePoolRequest(BaseModel):
    """v1.0.0 שלב א: יצירת פול אופציות נוסף - עד כה רק seed_data.py יצר פול,
    ואין endpoint אמיתי (ראו התכנון). established_date הוא קלט מפורש מהקורא,
    לא מהשעון - אותו דפוס בדיוק כמו Grant.grant_date/ShareIssuance.issue_date."""
    total_shares: float
    share_class_id: Optional[str] = None
    established_date: date


# ===================================================================
# Trustee portfolio
# ===================================================================

class TrusteePortfolioItem(BaseModel):
    grant_id: str
    employee_id: Optional[str] = None
    employee_name: Optional[str] = None
    company_id: Optional[str] = None
    company_name: Optional[str] = None
    total_options: float
    # None כשלמענק אין VestingSchedule - "לא ידוע" ולא 0. ראו MissingVestingScheduleError.
    vested_options: Optional[float] = None
    vesting_data_missing: bool = False
    trustee_deposit_date: Optional[date] = None
    holding_period_end_date: Optional[date] = None
    is_trustee_holding_period_met: bool


# ===================================================================
# Search
# ===================================================================

class SearchResultItem(BaseModel):
    entity_type: str
    entity_id: str
    title: str
    subtitle: str
    score: float


# ===================================================================
# Notifications
# ===================================================================

class NotificationItemOut(BaseModel):
    key: str
    rule: str
    entity_type: str
    entity_id: str
    title: str
    detail: str
    trigger_date: Optional[date] = None
    severity: str

class NotificationFeedOut(BaseModel):
    items: List[NotificationItemOut]
    # ישויות שהמנוע התרסק עליהן (הבאגים המכוונים של 29/2) - מדווחות במפורש
    # כדי שהפיד יחזיר 200 חלקי במקום 500, ושהמשתמש ידע שמשהו הושמט.
    degraded_entities: List[str]
    total: int

class NotificationCountOut(BaseModel):
    count: int

class NotificationPreferenceItem(BaseModel):
    rule: str
    enabled: bool
    lead_days: int

class NotificationPreferencesOut(BaseModel):
    preferences: List[NotificationPreferenceItem]

class NotificationPreferencesUpdate(BaseModel):
    preferences: List[NotificationPreferenceItem]


# ===================================================================
# Audit log
# ===================================================================

class AuditLogOut(BaseModel):
    audit_id: str
    entity_type: str
    entity_id: str
    action: str
    actor_user_id: Optional[str] = None
    occurred_at: datetime
    before_value: Optional[str] = None
    after_value: Optional[str] = None
    notes: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


# ===================================================================
# Exercise request workflow
# ===================================================================

class ExerciseRequestCreate(BaseModel):
    grant_id: str
    options_to_exercise: float

class ExerciseRequestReview(BaseModel):
    approve: bool
    notes: Optional[str] = None

class ExerciseRequestOut(BaseModel):
    request_id: str
    grant_id: str
    employee_id: str
    options_requested: float
    status: str
    requested_at: datetime
    reviewed_at: Optional[datetime] = None
    review_notes: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


# ===================================================================
# Ledger (v0.6.0 שלב 3) - ציר זמן ושאילתה בי-טמפורלית
# ===================================================================

class LedgerEventOut(BaseModel):
    event_id: str
    event_type: str
    effective_date: date
    recorded_at: datetime
    source: str
    payload: Dict[str, Any]
    corrects_event_id: Optional[str] = None


class LedgerProjectionOut(BaseModel):
    aggregate_type: str
    aggregate_id: str
    as_of_effective_date: Optional[date] = None
    as_of_knowledge_date: Optional[datetime] = None
    # None משמעו "אין אירועים בכלל עד לחתך הזה" - לא 0/ריק, אלא היעדר ידיעה
    # אמיתי. ראו GOAL.md: אין מספר בלי שרשור מקורות.
    state: Optional[Dict[str, Any]] = None


# ===================================================================
# Vesting pause / leave-of-absence (v0.6.0 שלב 4)
# ===================================================================

class VestingPauseRequest(BaseModel):
    start_date: date
    end_date: date

class VestingPauseResponse(BaseModel):
    schedule_id: str
    days_added: int
    paused_days_total: int


# ===================================================================
# מסמכים ואישור קבלה פנימי (v0.9.0 שלב 1: כתב הענקה בלבד)
#
# *** לא חתימה - ראו models.py. שום שדה כאן לא נקרא signature/signed. ***
# ===================================================================

class GenerateDocumentRequest(BaseModel):
    grant_id: str
    template_type: str

class DocumentOut(BaseModel):
    document_id: str
    template_type: str
    grant_id: str
    status: str
    version: int
    is_latest: bool
    file_sha256: str
    generated_at: datetime
    sent_at: Optional[datetime] = None
    # None = טרם נשלח, או נשלח לפני v0.9.1 ולכן אין לו דדליין. המסך חייב
    # להבחין בין השניים לבין "פג" - ראו P4 ב-QA_TESTBOOK.md.
    expires_at: Optional[datetime] = None
    acknowledged_at: Optional[datetime] = None
    # שני השדות האחרונים אינם על שורת ה-Document אלא מגיעים מ-Employee/Grant,
    # ולכן מורכבים ב-_documents_out ולא דרך from_attributes. בלעדיהם רשימת
    # המסמכים בפורטל היא grant_id בלבד - שהוא UUID לכל מענק שנוצר דרך ה-API.
    # Optional ולא מחרוזת ריקה: נתון חסר חייב להישאר מזוהה כחסר עד ל-UI, אחרת
    # הוא מוצג כשם ריק לגיטימי (QA_TESTBOOK.md P4).
    employee_name: Optional[str] = None
    grant_date: Optional[date] = None

    model_config = ConfigDict(from_attributes=True)


class DataTransferRunOut(BaseModel):
    """v0.9.1 שלב ב - שורת היסטוריית ייצוא/ייבוא. file_path לא נחשף כאן בכוונה
    (אותה סיבה כמו DocumentOut): ההורדה עוברת רק דרך endpoint מאומת שבודק
    company_id, לא דרך נתיב שהלקוח מקבל ופותח בעצמו."""
    run_id: str
    direction: str
    source_company_id: Optional[str] = None
    target_company_id: Optional[str] = None
    export_schema_version: int
    rows_attempted: int
    rows_succeeded: int
    rows_failed: int
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ImportRowErrorOut(BaseModel):
    """שורה אחת מדוח הדריי-ראן שנכשלה. row_id הוא None כשלא ניתן היה לזהות
    מפתח ראשי בשורה עצמה (קלט פגום עוד יותר מ"מפתח לא נמצא")."""
    table: str
    index: int
    row_id: Optional[str] = None
    error: str


class ImportDryRunReportOut(BaseModel):
    run_id: str
    status: str
    rows_attempted: int
    rows_new: int
    rows_skipped_existing: int
    # שורה חדשה בטבלה עם FK חובה ל-users (notification_preferences/dismissals) -
    # users לעולם לא מיובאת, אז שורה כזו לעולם לא תיכתב ב-commit. לא NEW (היה
    # משקר) ולא rows_failed (לא חוסמת ייבוא של שאר החבילה) - ראו services/import_.py.
    rows_not_portable: int = 0
    rows_failed: int
    errors: List[ImportRowErrorOut]


class ImportCommitRequest(BaseModel):
    """PLAN.md §8 step 8 - שני שלבים: אין upload חוזר, רק הפניה לדריי-ראן
    שכבר נשמר (services/import_.py::commit קורא את החבילה מ-file_path שלו,
    ומריץ dry_run מחדש מולה - לא סומך על הדוח הישן)."""
    dry_run_id: str


class ImportCommitReportOut(BaseModel):
    run_id: str
    status: str
    rows_attempted: int
    rows_written: int
    rows_skipped_existing: int
    rows_not_portable: int = 0
    rows_failed: int


class ReconciliationMismatchOut(BaseModel):
    """שורת אי-התאמה אחת (PLAN.md §8 step 10). source_value/target_value הם
    Any בכוונה - הערך יכול להיות float (סכום/שיעור), date (table_effective_date)
    או str (method), תלוי איזה שדה סטה (services/reconciliation.py)."""
    entity_type: str
    entity_id: str
    field_name: str
    source_value: Optional[Any] = None
    target_value: Optional[Any] = None
    reason: str


class ReconciliationReportOut(BaseModel):
    run_id: str
    as_of: date
    grants_checked: int
    exercises_checked: int
    clean: bool
    mismatches: List[ReconciliationMismatchOut]
    known_limitations: List[str]


# ===================================================================
# טבלת הון (Cap Table) - סוגי מניות, בעלי מניות, הקצאות מניות (v1.0.0 שלב א)
# ===================================================================

class CreateShareClassRequest(BaseModel):
    name: str
    class_type: str
    seniority_order: int

class ShareClassOut(BaseModel):
    share_class_id: str
    company_id: str
    name: str
    class_type: str
    seniority_order: int

    model_config = ConfigDict(from_attributes=True)


class CreateShareholderRequest(BaseModel):
    name: str
    shareholder_type: str
    # None = משקיע חיצוני שאינו עובד קיים במערכת - ראו models.py.Shareholder.employee_id.
    employee_id: Optional[str] = None

class ShareholderOut(BaseModel):
    shareholder_id: str
    company_id: str
    name: str
    shareholder_type: str
    employee_id: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class CreateShareIssuanceRequest(BaseModel):
    shareholder_id: str
    share_class_id: str
    shares: float
    # קלט מפורש מהקורא, לעולם לא מהשעון - ראו models.py.ShareIssuance.issue_date.
    issue_date: date

class ShareIssuanceOut(BaseModel):
    share_issuance_id: str
    company_id: str
    shareholder_id: str
    share_class_id: str
    shares: float
    issue_date: date

    model_config = ConfigDict(from_attributes=True)


# ===================================================================
# Cap Table snapshot (חישוב דילול) - v1.0.0 שלב ב. שלוש הסכמות האלה מיוצרות
# מ-dict רגיל שמוחזר מ-services/cap_table.py::compute_cap_table_snapshot,
# לא משורת ORM - from_attributes לא נדרש להן (בשונה מ-Out-ים למעלה שנבנים
# מ-SQLAlchemy rows), אבל נשארות ConfigDict-consistent עם השאר בכל זאת כדי
# שלא תהיה שונות מוסכמה בלי הצדקה.
# ===================================================================

class ShareholderClassBreakdownRow(BaseModel):
    shareholder_id: str
    share_class_id: str
    shares: float


class PoolSnapshotRow(BaseModel):
    pool_id: str
    # None = הפול לא משויך לסוג מניה ("unassigned") - ראו models.py.OptionPool.share_class_id.
    share_class_id: Optional[str] = None
    # None = הפול הוחרג מהחישוב (אין לו היסטוריית ledger לתאריך היסטורי
    # שהתבקש) - לעולם לא 0 שקרי, ראו compute_cap_table_snapshot.
    total_shares: Optional[float] = None


class CapTableSnapshotOut(BaseModel):
    as_of: date
    outstanding_shares: float
    fully_diluted_shares: float
    # None = Company.total_authorized_shares לא הוגדר - שני האחוזים למטה
    # נשארים None גם הם, לעולם לא 0% (דפוס הכשל P4, ראו models.py.Company).
    total_authorized_shares: Optional[float] = None
    outstanding_pct_of_authorized: Optional[float] = None
    fully_diluted_pct_of_authorized: Optional[float] = None
    partial: bool
    warnings: List[str]
    by_shareholder_and_class: List[ShareholderClassBreakdownRow]
    pools: List[PoolSnapshotRow]


# ===================================================================
# דוחות שמורים (Saved Reports) - v1.1.0, "דוחות, ייצוא ו-BI"
# ===================================================================
# filter_params: מהצד השני של ה-API הוא Dict רגיל, לא מחרוזת JSON - הקורא לא
# אמור לדעת ש-models.py.SavedReport.filter_params מאוחסן כטקסט; ה-service
# (json.dumps/json.loads) עושה את ההמרה, אותה מוסכמה בדיוק כמו LedgerEventOut.payload
# (api/ledger.py: json.loads(e.payload) לפני בניית ה-schema).

class SavedReportCreateRequest(BaseModel):
    name: str
    report_type: str
    filter_params: Dict[str, Any] = {}
    is_private: bool = True


class SavedReportOut(BaseModel):
    report_id: str
    company_id: str
    owner_user_id: str
    is_private: bool
    name: str
    report_type: str
    filter_params: Dict[str, Any]
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# ===================================================================
# דוחות (Reports/BI) - v1.1.0. rows/summary/disclosures הם ה-envelope
# הגנרי המשותף לכל שבעת הדוחות (ראו services/reports.py::ReportResult) -
# summary/disclosures הם dict/list חופשיים בכוונה (צורתם שונה מדוח לדוח,
# למשל by_pool/by_tax_track בדוח הוצאת השכר מול by_employee בדוח הדדליין),
# לא משועבטים ל-schema נוקשה אחד לכל שבעת הדוחות.
class ReportEnvelopeOut(BaseModel):
    report_type: str
    generated_at: datetime
    # *** columns נוסף ב-v1.1.1, והיעדרו כאן היה מלכודת. *** ה-schema הוגדר
    # ב-v1.1.0 אבל לא חובר לאף endpoint, ולכן אף אחד לא גילה שהוא חסר את השדה
    # שה-endpoint באמת מחזיר. חיבור שלו כ-response_model במצב הקודם היה מוחק את
    # columns מהתשובה בשקט - וזה השדה שכל שלושת הפורטלים גוזרים ממנו את כותרות
    # הטבלה (ראו ההערה ב-api/reports.py::_respond ואת הבדיקה
    # test_every_row_key_is_declared_as_a_column_so_csv_drops_nothing).
    columns: List[str]
    rows: List[Dict[str, Any]]
    summary: Dict[str, Any]
    disclosures: List[str] = []


# ===================================================================
# v1.1.1 פריט ד2: מעטפות לתשובות שהיו dict לא-מתועד ב-/docs
#
# כל אחת מהן מתעדת צורה שה-endpoint *כבר* מחזיר - אף שדה לא נוסף ואף שדה לא
# הוסר. שני מקומות מחזירים שתי צורות שונות לפי מסלול (מחיקה קשה מול רכה),
# ושם ה-endpoint משתמש ב-response_model_exclude_unset=True: בלעדיו המסלול
# הקצר היה מתחיל להחזיר את השדה הנוסף כ-null, כלומר שינוי חוזה גלוי ללקוח
# בתוך patch.
# ===================================================================

class StatusOut(BaseModel):
    """{"status": "..."} - logout ו-change-password."""
    status: str


class ApiRootOut(BaseModel):
    message: str
    version: str


class VersionOut(BaseModel):
    version: str


class CurrentUserOut(BaseModel):
    user_id: str
    username: str
    role: str
    # שלושת המזהים תלויי-תפקיד: לאדמין יש company_id, לנאמן trustee_id,
    # לעובד employee_id - ולכן None בשניים מהם הוא המצב הרגיל, לא חוסר נתון.
    company_id: Optional[str] = None
    trustee_id: Optional[str] = None
    employee_id: Optional[str] = None


class CompanyDeleteOut(BaseModel):
    company_id: str
    deleted: str
    is_active: Optional[bool] = None


class EmployeeDeleteOut(BaseModel):
    employee_id: str
    deleted: str
    new_status: Optional[str] = None


class EmployeeStatusChangeOut(BaseModel):
    employee_id: str
    status: EmployeeStatus
    returned_options: float


class SavedReportDeleteOut(BaseModel):
    deleted: bool
    report_id: str


class TrusteeDepositConfirmOut(BaseModel):
    grant_id: str
    # str ולא date: ה-endpoint מחזיר str(deposit_date) כבר היום.
    deposit_date: str
    status: str


class EmployeeDashboardGrantOut(BaseModel):
    grant_id: str
    total_options: float
    # None כשאין לוח הבשלה - ומסומן במפורש ב-vesting_data_missing ולא מתחזה
    # ל-0.0. אותו כלל P4 שנאכף בכל הקודבייס: נתון חסר אינו אפס.
    vested_options: Optional[float] = None
    vesting_data_missing: bool
    exercise_price: float
    is_trustee_holding_period_met: bool
    holding_period_end_date: Optional[str] = None
    is_within_post_termination_window: bool
    post_termination_exercise_deadline: Optional[str] = None


class EmployeeDashboardOut(BaseModel):
    employee_name: str
    grants: List[EmployeeDashboardGrantOut]


class DashboardTaxTrackItemOut(BaseModel):
    grant_type: str
    count: int
    pct_of_total: float


class DashboardVestingPointOut(BaseModel):
    as_of: date
    cumulative_vested: float


class ReportsDashboardOut(BaseModel):
    as_of: date
    total_grants_in_scope: int
    tax_track_breakdown: List[DashboardTaxTrackItemOut]
    forward_vesting_curve: List[DashboardVestingPointOut]
    vesting_curve_horizon_months: int
    # מענקים בלי לוח הבשלה, שנספרו אך לא נכללו בעקומה. רשימה ריקה היא "אין
    # בעיה"; היעדר השדה היה "לא נמדד" - שתי משמעויות שאסור לאחד.
    degraded_grant_ids: List[str]
