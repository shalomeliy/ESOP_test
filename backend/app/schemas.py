from pydantic import BaseModel
from datetime import date, datetime
from typing import Optional, List
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

    class Config:
        from_attributes = True


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

    class Config:
        from_attributes = True

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

    class Config:
        from_attributes = True

class PoolOut(BaseModel):
    pool_id: str
    company_id: str
    total_shares: float
    allocated_shares: float
    unallocated_shares: float

    class Config:
        from_attributes = True


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
    vested_options: float
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

    class Config:
        from_attributes = True


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

    class Config:
        from_attributes = True
