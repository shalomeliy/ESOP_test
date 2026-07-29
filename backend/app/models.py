import uuid
from datetime import datetime, date
from enum import Enum
from sqlalchemy import Column, String, Float, Integer, Date, DateTime, Boolean, ForeignKey, Enum as SQLEnum, CheckConstraint, UniqueConstraint, Index
from sqlalchemy.orm import relationship
from backend.app.database import Base

def generate_uuid():
    return str(uuid.uuid4())

class EmployeeStatus(str, Enum):
    ACTIVE = "ACTIVE"
    TERMINATED = "TERMINATED"
    ON_LEAVE = "ON_LEAVE"
    DECEASED = "DECEASED"

class GrantType(str, Enum):
    IL_102_CAPITAL_GAINS = "IL_102_CAPITAL_GAINS"
    IL_102_WORK_INCOME = "IL_102_WORK_INCOME"
    US_ISO = "US_ISO"
    US_NSO = "US_NSO"

class Company(Base):
    __tablename__ = "companies"
    company_id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String, nullable=False)
    country_code = Column(String, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    pools = relationship("OptionPool", back_populates="company")
    employees = relationship("Employee", back_populates="company")

class OptionPool(Base):
    __tablename__ = "option_pools"
    __table_args__ = (
        # allocated + unallocated חייבים תמיד לסכם בדיוק לגודל הפול - מונע דריפט שקט
        # בין שני השדות (למשל אם מישהו יעדכן רק אחד מהם בקוד עתידי).
        CheckConstraint(
            "allocated_shares + unallocated_shares = total_shares",
            name="ck_option_pools_shares_balance",
        ),
    )
    pool_id = Column(String, primary_key=True, default=generate_uuid)
    company_id = Column(String, ForeignKey("companies.company_id"), nullable=False, index=True)
    total_shares = Column(Float, nullable=False)
    allocated_shares = Column(Float, default=0.0, nullable=False)
    unallocated_shares = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    company = relationship("Company", back_populates="pools")

class Employee(Base):
    __tablename__ = "employees"
    employee_id = Column(String, primary_key=True, default=generate_uuid)
    # nullable=True בכוונה: עובד שהחברה המעסיקה שלו נסגרה/פורקה יכול להישאר
    # בלי שיוך לחברה בכלל (בשונה מ-is_active=False על חברה שעדיין קיימת).
    company_id = Column(String, ForeignKey("companies.company_id"), nullable=True, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String, nullable=False, unique=True)
    country_code = Column(String, nullable=False)
    status = Column(SQLEnum(EmployeeStatus), default=EmployeeStatus.ACTIVE)
    hire_date = Column(Date, nullable=False)
    termination_date = Column(Date, nullable=True)
    birth_date = Column(Date, nullable=True)

    company = relationship("Company", back_populates="employees")
    grants = relationship("Grant", back_populates="employee")

class Trustee(Base):
    __tablename__ = "trustees"
    trustee_id = Column(String, primary_key=True, default=generate_uuid)
    company_id = Column(String, ForeignKey("companies.company_id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    registration_number = Column(String, nullable=False)

class Grant(Base):
    __tablename__ = "grants"
    grant_id = Column(String, primary_key=True, default=generate_uuid)
    employee_id = Column(String, ForeignKey("employees.employee_id"), nullable=False, index=True)
    pool_id = Column(String, ForeignKey("option_pools.pool_id"), nullable=False, index=True)
    trustee_id = Column(String, ForeignKey("trustees.trustee_id"), nullable=True, index=True)
    grant_date = Column(Date, nullable=False)
    grant_type = Column(SQLEnum(GrantType), nullable=False)
    total_options = Column(Float, nullable=False)
    exercise_price = Column(Float, nullable=False)
    currency = Column(String, default="USD")
    trustee_deposit_date = Column(Date, nullable=True)
    # תנאי תוכנית (plan term) - כמה ימים אחרי עזיבה מותר עדיין להגיש בקשת מימוש על
    # אופציות שכבר הבשילו. זה נוהג מקובל בתוכניות אופציות, לא הוראת מיסוי סטטוטורית
    # (בשונה מ-102 חסימת הנאמנות) - לכן ניתן להגדרה לפי מענק, לא קבוע קשיח בחוק.
    post_termination_window_days = Column(Integer, default=90, nullable=False)

    employee = relationship("Employee", back_populates="grants")
    vesting_schedule = relationship("VestingSchedule", back_populates="grant", uselist=False)

class VestingSchedule(Base):
    __tablename__ = "vesting_schedules"
    schedule_id = Column(String, primary_key=True, default=generate_uuid)
    # unique=True הופך את זה ל-1:1 אמיתי ברמת ה-DB, לא רק הנחה ב-ORM (uselist=False).
    grant_id = Column(String, ForeignKey("grants.grant_id"), nullable=False, unique=True)
    start_date = Column(Date, nullable=False)
    cliff_months = Column(Integer, default=12)
    total_months = Column(Integer, default=48)
    paused_days_total = Column(Integer, default=0)

    grant = relationship("Grant", back_populates="vesting_schedule")

class TaxRatesHistory(Base):
    __tablename__ = "tax_rates_history"
    tax_rule_id = Column(String, primary_key=True, default=generate_uuid)
    country_code = Column(String, nullable=False, index=True)
    grant_type = Column(String, nullable=False, index=True)
    effective_start_date = Column(Date, nullable=False)
    capital_gains_rate = Column(Float, nullable=False)
    official_source_url = Column(String, nullable=False)

class IncomeTaxBracket(Base):
    # מדרגות מס פרוגרסיביות, versioned לפי (country_code, grant_type,
    # effective_start_date) - כל שורות עם אותה שלישייה שייכות לאותה "גרסה" של
    # טבלת המדרגות. רלוונטי בעיקר ל-IL_102_WORK_INCOME (מוסה כהכנסת עבודה,
    # לא capital gains שטוח). *** נתוני דמו לתרגול QA בלבד - לא חוק מס אמיתי ***
    __tablename__ = "income_tax_brackets"
    bracket_id = Column(String, primary_key=True, default=generate_uuid)
    country_code = Column(String, nullable=False, index=True)
    grant_type = Column(String, nullable=False, index=True)
    effective_start_date = Column(Date, nullable=False, index=True)
    bracket_order = Column(Integer, nullable=False)
    min_amount = Column(Float, nullable=False)
    # max_amount=None = המדרגה העליונה הפתוחה (בלי תקרה)
    max_amount = Column(Float, nullable=True)
    rate = Column(Float, nullable=False)
    official_source_url = Column(String, nullable=False)

class StockPricesHistory(Base):
    __tablename__ = "stock_prices_history"
    price_id = Column(String, primary_key=True, default=generate_uuid)
    company_id = Column(String, ForeignKey("companies.company_id"), nullable=False, index=True)
    price_date = Column(Date, nullable=False)
    fmv_price = Column(Float, nullable=False)
    currency = Column(String, default="USD")


# ===================================================================
# מודלים ל-Auth ו-workflow בקשות מימוש (פורטלי admin/trustee/employee)
# ===================================================================

class UserRole(str, Enum):
    COMPANY_ADMIN = "COMPANY_ADMIN"
    TRUSTEE = "TRUSTEE"
    EMPLOYEE = "EMPLOYEE"

class User(Base):
    __tablename__ = "users"
    user_id = Column(String, primary_key=True, default=generate_uuid)
    username = Column(String, nullable=False, unique=True, index=True)
    password_hash = Column(String, nullable=False)
    password_salt = Column(String, nullable=False)
    role = Column(SQLEnum(UserRole), nullable=False)
    # בדיוק אחד מהשלושה הבאים אמור להיות מאוכלס, בהתאם ל-role - לא נאכף ברמת ה-DB.
    company_id = Column(String, ForeignKey("companies.company_id"), nullable=True)
    trustee_id = Column(String, ForeignKey("trustees.trustee_id"), nullable=True)
    employee_id = Column(String, ForeignKey("employees.employee_id"), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class UserSession(Base):
    __tablename__ = "user_sessions"
    token = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.user_id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)

class ExerciseRequestStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

class AuditLog(Base):
    __tablename__ = "audit_log"
    audit_id = Column(String, primary_key=True, default=generate_uuid)
    entity_type = Column(String, nullable=False, index=True)
    entity_id = Column(String, nullable=False, index=True)
    action = Column(String, nullable=False)
    actor_user_id = Column(String, ForeignKey("users.user_id"), nullable=True)
    occurred_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    # snapshot לפני/אחרי כ-JSON טקסטואלי - מספיק לצורכי תרגול QA, לא צריך JSON column ייעודי ב-SQLite
    before_value = Column(String, nullable=True)
    after_value = Column(String, nullable=True)
    notes = Column(String, nullable=True)


class ExerciseRequest(Base):
    __tablename__ = "exercise_requests"
    request_id = Column(String, primary_key=True, default=generate_uuid)
    grant_id = Column(String, ForeignKey("grants.grant_id"), nullable=False, index=True)
    employee_id = Column(String, ForeignKey("employees.employee_id"), nullable=False, index=True)
    options_requested = Column(Float, nullable=False)
    requested_at = Column(DateTime, default=datetime.utcnow)
    status = Column(SQLEnum(ExerciseRequestStatus), default=ExerciseRequestStatus.PENDING, nullable=False)
    reviewed_by_user_id = Column(String, ForeignKey("users.user_id"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    review_notes = Column(String, nullable=True)


# ===================================================================
# Notification Center
# ===================================================================
# ההתראות עצמן *לא* נשמרות ב-DB - הן מחושבות בכל קריאה מ-DeterministicESOPEngine
# מתוך הנתונים הקיימים (vesting, נאמנות, PTEW, בקשות ממתינות). לכן אין כאן טבלת
# notifications: התראה מאוחסנת היא עותק שמתיישן ברגע שהמענק משתנה, ואז ה-feed
# מציג מצב שקרי. מה שכן חייב להישמר זה רק מצב המשתמש - מה הוא ביקש לקבל ומה
# הוא כבר סגר.

# ברירות המחדל של מספר ימי ההתראה מראש לכל כלל. אלה החלטות מוצר (כמה מוקדם
# להטריד את המשתמש) ולא כללי מס, ולכן מותר לשנות אותן בלי אימות מול חוק.
# הן יושבות כאן ולא ב-service כדי שיהיה מקור אמת אחד: המפתחות של המילון הם
# גם רשימת הכללים החוקיים היחידה עבור העמודה rule.
NOTIFICATION_DEFAULT_LEAD_DAYS = {
    "VESTING_EVENT_NEAR": 14,
    "TRUSTEE_HOLDING_ENDING": 30,
    "PTEW_CLOSING": 30,
    "REQUEST_PENDING_TOO_LONG": 7,
    "FULLY_VESTED_UNEXERCISED": 90,
}


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"
    __table_args__ = (
        # שורה אחת בדיוק לכל (משתמש, כלל). בלי האילוץ הזה upsert שנכשל באמצע או
        # שתי לשוניות פתוחות במקביל יכולים ליצור שתי שורות סותרות לאותו כלל -
        # אחת enabled=1/lead_days=14 והשנייה enabled=0 - ואז ה-feed תלוי בסדר
        # השורות שחזר מה-DB, כלומר ההתראות "נעלמות וחוזרות" בלי סיבה נראית לעין.
        UniqueConstraint("user_id", "rule", name="uq_notification_preferences_user_rule"),
    )
    preference_id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.user_id"), nullable=False, index=True)
    # אחד מהמפתחות של NOTIFICATION_DEFAULT_LEAD_DAYS. לא נאכף כ-CHECK ברמת ה-DB
    # בכוונה: רשימת הכללים צפויה עוד לגדול בגרסה הזו, ו-CHECK על ערכי טקסט ב-SQLite
    # דורש בנייה מחדש של הטבלה בכל תוספת כלל.
    rule = Column(String, nullable=False)
    enabled = Column(Boolean, default=True, nullable=False)
    # nullable=False בלי ברירת מחדל ב-DB: הערך נקבע ב-backend מ-
    # NOTIFICATION_DEFAULT_LEAD_DAYS, כדי ששינוי ברירת מחדל לא יחייב מיגרציה.
    lead_days = Column(Integer, nullable=False)


class NotificationDismissal(Base):
    __tablename__ = "notification_dismissals"
    __table_args__ = (
        # אינדקס ייחודי אחד שממלא שני תפקידים בבת אחת:
        # (1) אכיפה - סגירה חוזרת של אותה התראה היא idempotent ברמת ה-DB, כך
        #     שה-endpoint יכול להישען על IntegrityError במקום לבדוק-ואז-להכניס
        #     (בדיקה-ואז-הכנסה היא race שמייצרת כפילויות בדיוק בלחיצה כפולה).
        # (2) ביצועים - זה ה-lookup החם: כל בקשת feed בודקת כל התראה מועמדת מול
        #     (user_id, notification_key). UNIQUE רגיל היה יוצר כאן autoindex זהה,
        #     ולכן אינדקס נפרד נוסף על אותן שתי עמודות היה כפילות שמייקרת כתיבות בלבד.
        Index(
            "ix_notification_dismissals_user_key",
            "user_id",
            "notification_key",
            unique=True,
        ),
    )
    dismissal_id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.user_id"), nullable=False, index=True)
    # מפתח דטרמיניסטי "rule|entity_id|trigger_date" - הוא חייב להיות דטרמיניסטי
    # כי ההתראה עצמה לא נשמרת: זה הדבר היחיד שמקשר סגירה שנעשתה אתמול להתראה
    # שתיווצר מחדש מהמנוע היום.
    notification_key = Column(String, nullable=False)
    dismissed_at = Column(DateTime, default=datetime.utcnow)