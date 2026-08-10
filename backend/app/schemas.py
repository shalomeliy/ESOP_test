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
    holding_period_end_date: date
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

class CompanyOut(BaseModel):
    company_id: str
    name: str
    country_code: str
    is_active: bool

    model_config = ConfigDict(from_attributes=True)

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

    model_config = ConfigDict(from_attributes=True)


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
