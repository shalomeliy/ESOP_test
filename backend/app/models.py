import uuid
from enum import Enum
from sqlalchemy import Column, String, Float, Integer, Date, Boolean, ForeignKey, Enum as SQLEnum, CheckConstraint, UniqueConstraint, Index
from sqlalchemy.orm import relationship
from backend.app.database import Base
from backend.app.types import UtcDateTime, utcnow

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
    created_at = Column(UtcDateTime, default=utcnow)
    # nullable=True: אי אפשר להמציא ערך לחברות קיימות שנזרעו לפני v1.0.0 - שלב ב
    # (חישוב דילול) חייב להתייחס במפורש ל-None כ"לא זמין" ולעולם לא להציג 0%
    # דלילות בלי הערך הזה. זה בדיוק דפוס הכשל P4 המתועד ב-QA_TESTBOOK.md.
    total_authorized_shares = Column(Float, nullable=True)
    # nullable=True: אותו דפוס בדיוק כמו total_authorized_shares - None אומר
    # "אין override, השתמש בקבוע הגלובלי" (ACKNOWLEDGMENT_WINDOW_DAYS), ולא 0.
    # ה-CHECK דוחה 0/שלילי: "פוקע מיד בשליחה" אינו צורך שהוצהר, וטעות הקלדה
    # כאן (למשל שכחת ה-30) הייתה הופכת כל מסמך חדש לפג-תוקף באותו רגע.
    acknowledgment_window_days = Column(Integer, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "acknowledgment_window_days IS NULL OR acknowledgment_window_days > 0",
            name="ck_companies_acknowledgment_window_days_positive",
        ),
    )

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
    created_at = Column(UtcDateTime, default=utcnow)
    # nullable=True: פולים קיימים/מזרוקעים (seed_data.py) אין להם עדיין שיוך
    # לסוג מניה - אותו דפוס בדיוק כמו Grant.trustee_id. אין השפעה על
    # ck_option_pools_shares_balance - האילוץ ההוא per-row וממשיך לחול בלי
    # שינוי, לא משנה כמה פולים/סוגי מניה קיימים.
    share_class_id = Column(String, ForeignKey("share_classes.share_class_id"), nullable=True, index=True)

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
    # v0.9.0: נדרש למסמכים (כתב הענקה/נספח 102) שמזהים את העובד באופן רשמי.
    # nullable + טקסט חופשי בכוונה - בלי ולידציית פורמט לפי מדינה (מחוץ להיקף
    # v0.9.0); עובדים קיימים שלא הוזן להם הערך פשוט לא יוצג להם ב-PDF.
    national_id = Column(String, nullable=True)

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

# סוגי שיטת חישוב אפשריים על TaxRulePack.calculation_method - String רגיל (לא
# SQLEnum), כמו LEDGER_EVENT_TYPES: אוצר מילים סגור שנבדק באפליקציה, לא באילוץ DB.
TAX_CALCULATION_METHODS = {"FLAT_RATE", "PROGRESSIVE_BRACKETS"}


class TaxRulePack(Base):
    # "כותרת" חדשה (v0.7.0) שמוציאה את שיטת החישוב (שטוח מול מדורג) מ-if קשיח
    # ב-tax_engine.py אל תוך דאטה - זה בדיוק הפער בין "יש דאטה עם תאריך תוקף"
    # (כבר היה קיים) לבין "אין אף כלל מקודד בקוד" (עדיין לא היה, עד עכשיו).
    # calculation_method נשאר בכל זאת עובדה מבנית לא-ניתנת לעריכה חופשית: הגרסה
    # הזו לא בונה מסך admin לניהול חבילות - השורות היחידות שנכתבות הן מגיבוי
    # חד-פעמי (backfill_tax_rule_packs.py) ומ-seed_data.py, בדיוק כמו הטבלאות
    # הקיימות. ראו אזהרת מומחה המס בתכנון: שיוך שגוי של שיטה למסלול (למשל
    # מדרגות על מסלול רווח הון) הוא סיכון תוכן-מס, לא רק סיכון טכני.
    __tablename__ = "tax_rule_packs"
    pack_id = Column(String, primary_key=True, default=generate_uuid)
    country_code = Column(String, nullable=False, index=True)
    grant_type = Column(String, nullable=False, index=True)
    effective_start_date = Column(Date, nullable=False)
    calculation_method = Column(String, nullable=False)
    official_source_url = Column(String, nullable=False)
    created_at = Column(UtcDateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("country_code", "grant_type", "effective_start_date",
                         name="uq_tax_rule_packs_country_type_date"),
    )


class TaxRatesHistory(Base):
    __tablename__ = "tax_rates_history"
    tax_rule_id = Column(String, primary_key=True, default=generate_uuid)
    country_code = Column(String, nullable=False, index=True)
    grant_type = Column(String, nullable=False, index=True)
    effective_start_date = Column(Date, nullable=False)
    capital_gains_rate = Column(Float, nullable=False)
    official_source_url = Column(String, nullable=False)
    # nullable בכוונה: מתמלא ע"י backfill חד-פעמי על שורות קיימות, לא שדה
    # שנדרש בזמן יצירה - ראו backfill_tax_rule_packs.py.
    # unique=True: **נמצא בסקירת קוד עצמאית** - היחס בין pack ל-TaxRatesHistory
    # הוא 1:1 (בניגוד ל-IncomeTaxBracket, ששם כמה שורות *אמורות* לחלוק pack_id
    # אחד - מדרגות אותה גרסה). בלי האילוץ, שתי שורות עם אותו pack_id היו יכולות
    # להתקיים ו-tax_engine.py._calculate_flat (שמשתמש ב-.first()) היה בוחר
    # ביניהן בלי סדר מובטח - בדיוק אותה מחלקת באג ש-v0.7.0 כולו נועד לסגור.
    pack_id = Column(String, ForeignKey("tax_rule_packs.pack_id"), nullable=True, unique=True)

    __table_args__ = (
        # נמצא בתכנון v0.7.0: בלי האילוץ הזה, שתי שורות עם אותו תאריך תוקף
        # היו מתחרות על .order_by(...).first() בלי סדר מובטח - בחירה לא
        # דטרמיניסטית בחישוב מס בפועל.
        UniqueConstraint("country_code", "grant_type", "effective_start_date",
                         name="uq_tax_rates_history_country_type_date"),
    )


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
    pack_id = Column(String, ForeignKey("tax_rule_packs.pack_id"), nullable=True)

    __table_args__ = (
        # ייחוד על בכל המפתח כולל bracket_order (לא רק השלישייה): שורות רבות
        # שייכות בכוונה לאותה גרסה (מדרגה 1, 2, 3...) - האילוץ מונע רק "גרסה
        # כפולה" (אותה מדרגה פעמיים לאותה שלישייה), לא חוסם מדרגות לגיטימיות.
        UniqueConstraint("country_code", "grant_type", "effective_start_date", "bracket_order",
                         name="uq_income_tax_brackets_country_type_date_order"),
    )

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
    created_at = Column(UtcDateTime, default=utcnow)
    # נדלק על כל חשבון שסופק עם סיסמה חד-פעמית (ראו auth.generate_temporary_password) -
    # לא על סיסמה שנבחרה ע"י המשתמש. require_roles חוסם כל endpoint עסקי עד שהדגל יורד
    # דרך POST /auth/change-password. חשבונות QA שנזרעים ישירות (seed_data.py) לא מסמנים
    # את זה - הם לא עוברים דרך "פרובייז לעובד חדש".
    must_change_password = Column(Boolean, default=False, nullable=False)
    # נעילת חשבון (ראו auth.MAX_FAILED_LOGIN_ATTEMPTS). שני שדות ולא טבלה נפרדת: אין
    # צורך בהיסטוריה, רק במונה חי ובזמן שחרור.
    failed_login_attempts = Column(Integer, default=0, nullable=False)
    locked_until = Column(UtcDateTime, nullable=True)

class UserSession(Base):
    __tablename__ = "user_sessions"
    token = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.user_id"), nullable=False, index=True)
    created_at = Column(UtcDateTime, default=utcnow)
    expires_at = Column(UtcDateTime, nullable=False)

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
    occurred_at = Column(UtcDateTime, default=utcnow, nullable=False, index=True)
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
    requested_at = Column(UtcDateTime, default=utcnow)
    status = Column(SQLEnum(ExerciseRequestStatus), default=ExerciseRequestStatus.PENDING, nullable=False)
    reviewed_by_user_id = Column(String, ForeignKey("users.user_id"), nullable=True)
    reviewed_at = Column(UtcDateTime, nullable=True)
    review_notes = Column(String, nullable=True)


class ExerciseTaxRecord(Base):
    """נקודת המס היחידה שנרשמת בפועל על מימוש **אמיתי** (לא בדיקה/סימולציה) -
    עד v0.9.1 שלב ב, TaxCalculationEngine נקרא רק מ-/simulate-exercise, ותוצאתו
    נשמרת כ-JSON חופשי בתוך AuditLog.after_value בלבד. אישור מימוש בפועל
    (_decide_exercise_request) לא חישב מס כלל - לא רשם אותו אחרת, לא חישב.

    country_code/grant_type/effective_start_date ולא pack_id: pack_id מתחדש
    בכל seed/backfill (generate_uuid() חדש בכל הרצה) ולכן לא שורד בין שני
    מופעי DB - בדיוק אותה סיבה שהייצוא (שלב ב) חייב להתאים חבילות מס לפי
    המפתח הטבעי, לא לפי pack_id מילולי. השלישייה הזו היא הזהות האמיתית של
    TaxRulePack, לפי uq_tax_rule_packs_country_type_date שלו-עצמו.

    gain נשמר בנוסף ל-tax_amount, לא רק התוצאה: בלי הקלט הגולמי, דוח ההתאמה
    (שלב ב) יכול להשוות מספר לעצמו בלבד ולא לשחזר את החישוב באמת.
    """
    __tablename__ = "exercise_tax_records"
    record_id = Column(String, primary_key=True, default=generate_uuid)
    # unique=True: רשומת מס אחת בדיוק לכל בקשת מימוש - מונע שתי רשומות
    # מתחרות שמתארות את אותו אישור (למשל אם הקוד שקורא לזה ירוץ פעמיים).
    request_id = Column(String, ForeignKey("exercise_requests.request_id"), nullable=False, unique=True)
    country_code = Column(String, nullable=False)
    grant_type = Column(String, nullable=False)
    effective_start_date = Column(Date, nullable=False)
    calculation_method = Column(String, nullable=False)
    gain = Column(Float, nullable=False)
    tax_amount = Column(Float, nullable=False)
    effective_rate = Column(Float, nullable=False)
    official_source_url = Column(String, nullable=False)
    computed_at = Column(UtcDateTime, default=utcnow, nullable=False)


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
    dismissed_at = Column(UtcDateTime, default=utcnow)


# ===================================================================
# Ledger מבוסס-אירועים (v0.6.0)
# ===================================================================
# מצב עסקי (יתרת פול, סטטוס עובד, תאריך הפקדת נאמן וכו') הופך מ"שדה שמישהו
# עורך" ל-*תוצר חישוב* מרצף אירועים append-only. העמודות המוטטות הקיימות
# (OptionPool.allocated_shares וכו') נשארות בשלב זה כ"פרויקציה מחושבת" -
# נכתבות באותה טרנזקציה שמוסיפה את האירוע - כדי ש-ck_option_pools_shares_balance
# ואילוצי ה-DB האחרים ימשיכו להיאכף בלי שינוי (SQLite לא יודע לאכוף שוויון
# מול ערך משוחזר מאירועים, רק מול עמודות בפועל).

# רשימת סוגי האירועים החוקיים. עמודה String רגילה ולא SQLEnum/CHECK בכוונה -
# אותה החלטה בדיוק כמו NotificationPreference.rule למעלה: הרשימה צפויה לגדול,
# ו-CHECK על ערכי טקסט ב-SQLite דורש בנייה מחדש של הטבלה בכל תוספת.
# "ESTABLISHED" = אירוע בסיס (snapshot) שממנו מתחיל ה-replay; כל שאר הסוגים הם
# דלתא שמצטברת מעליו. גם אירוע גיבוי (source=BACKFILL) וגם אירוע חי (source=LIVE)
# משתמשים באותם סוגי אירועים בדיוק - ה-source הוא מה שמבדיל ביניהם, לא סוג האירוע.
LEDGER_EVENT_TYPES = {
    "POOL_BALANCE_ESTABLISHED",     # בסיס: יתרת פול כפי שהיא ידועה כרגע
    "POOL_ALLOCATED",               # דלתא: הקצאה בעת יצירת מענק
    "POOL_UNVEST_RETURNED",         # דלתא: החזרה לפול בעת עזיבה
    "EMPLOYEE_STATE_ESTABLISHED",   # בסיס: סטטוס עובד כפי שהוא ידוע כרגע
    # דלתא: כל שינוי סטטוס (לא רק עזיבה - update_employee_status מקבל כל
    # EmployeeStatus) - שם אחד גנרי ולא EMPLOYEE_TERMINATED, כי הקוד עצמו
    # מבצע השמה גנרית ל-status ולא ענף ייעודי לעזיבה בלבד.
    "EMPLOYEE_STATUS_CHANGED",
    "GRANT_CREATED",                # בסיס וגם דלתא חיה: יצירת מענק
    "TRUSTEE_DEPOSIT_CONFIRMED",    # בסיס וגם דלתא חיה: אישור הפקדה אצל נאמן
    "VESTING_SCHEDULE_ESTABLISHED", # בסיס: לוח הבשלה כפי שהוא ידוע כרגע
    # דלתא (שלב 4): תקופת הקפאה שלמה שנרשמה - start_date+end_date+days בבת
    # אחת, לא שני אירועים נפרדים "התחל"/"סיים". שום מקום אחר במערכת לא עוקב
    # אחרי "הקפאה פתוחה שטרם נסגרה" (בניגוד ל-termination_date, שכן קיים כשדה
    # עצמאי) - מוסכמת ברזל: לא לבנות מצב-ביניים שאין לו צרכן. אדמין רושם את
    # התקופה אחרי שהיא כבר ידועה במלואה, בדיוק כמו trustee_deposit_date.
    "VESTING_PAUSE_RECORDED",
    "EXERCISE_REQUEST_SUBMITTED",   # בסיס וגם דלתא חיה: הגשת בקשת מימוש
    "EXERCISE_REQUEST_DECIDED",     # דלתא: אישור/דחייה
    # בסיס וגם דלתא חיה (v1.0.0 שלב א): הקצאת מניות ל-Shareholder. סוג אירוע
    # יחיד בלבד בשלב א - אין עדיין תיקון/ביטול/העברה של מניות שהוקצו (v1.4.0/
    # M&A, לא כאן); ראו ShareIssuance למטה.
    "SHARE_ISSUANCE_ESTABLISHED",
}

LEDGER_AGGREGATE_TYPES = {"OptionPool", "Employee", "Grant", "VestingSchedule", "ExerciseRequest", "ShareIssuance"}

# מקור האירוע - נדרש כעמודה בסכמה (לא הערה בקוד): אירוע גיבוי חייב להיות מובחן
# לצמיתות מאירוע אמיתי, כדי שמסך "מה חשבנו בתאריך X" לעולם לא יתחזה לידע
# שאין למערכת (ראו GOAL.md, "אין מספר בלי שרשור מקורות").
LEDGER_SOURCE_LIVE = "LIVE"
LEDGER_SOURCE_BACKFILL = "BACKFILL_v0.6.0"


class LedgerEvent(Base):
    __tablename__ = "ledger_events"
    __table_args__ = (
        # (aggregate_id, sequence_no) הוא סדר הקיפול (fold) הקנוני בתוך אותה
        # ישות, כששני אירועים חולקים את אותו effective_date - recorded_at לבדו
        # לא מספיק כי backfill מדביק את כולם לאותה שנייה בדיוק (ראו backfill_ledger.py).
        UniqueConstraint("aggregate_id", "sequence_no", name="uq_ledger_events_aggregate_seq"),
        Index("ix_ledger_events_aggregate", "aggregate_type", "aggregate_id"),
        Index("ix_ledger_events_effective_date", "effective_date"),
        Index("ix_ledger_events_recorded_at", "recorded_at"),
    )
    event_id = Column(String, primary_key=True, default=generate_uuid)
    event_type = Column(String, nullable=False, index=True)
    aggregate_type = Column(String, nullable=False)
    aggregate_id = Column(String, nullable=False)
    # עובדה טיפוסית כ-JSON טקסטואלי - אותה מוסכמה כמו AuditLog.before_value/after_value.
    payload = Column(String, nullable=False)
    # ציר זמן #1: מתי העובדה נכונה *בעולם*.
    effective_date = Column(Date, nullable=False)
    # ציר זמן #2: מתי המערכת למדה אותה. immutable לאחר הכתיבה - זו בדיוק הסיבה
    # שהטריגר במיגרציה חוסם UPDATE על הטבלה הזו כולה.
    recorded_at = Column(UtcDateTime, nullable=False)
    # nullable כדי לאפשר אירועי גיבוי/מערכת - לעולם לא משויכים למשתמש אמיתי
    # שלא ביצע את הפעולה בפועל (ראו backfill_ledger.py).
    actor_user_id = Column(String, ForeignKey("users.user_id"), nullable=True)
    sequence_no = Column(Integer, nullable=False)
    # מצביע על האירוע שהוא מתקן, ולעולם לא מוחק/עורך אותו. הסכמה מוכנה מהיום
    # הראשון; אין עדיין endpoint שיוצר תיקון - זה מתוכנן לגרסה הבאה, ראו
    # ההחלטה המפורשת ב-FEATURE_SPEC.md ("אין maker-checker לתיקונים בגרסה הזו").
    corrects_event_id = Column(String, ForeignKey("ledger_events.event_id"), nullable=True)
    schema_version = Column(Integer, nullable=False, default=1)
    # LEDGER_SOURCE_LIVE / LEDGER_SOURCE_BACKFILL - ראו הערה למעלה.
    source = Column(String, nullable=False)


class LedgerOwnership(Base):
    """אינדקס בעלות נפרד ולא-חוזר, לצורך הרשאות בלבד - לעולם לא נשען על דאטה
    משוחזר/מוקרן. נקבע פעם אחת ביצירת הישות ונחשב immutable בהיקף v0.6.0 (מענק
    שעובר בין חברות הוא v1.4.0/M&A, לא כאן). זו בדיוק ההגנה מפני חזרה של דפוס
    ה-IDOR שכבר תוקן פעמיים: מסכי v0.6.0 מאשרים גישה מול הטבלה הזו, לא מול
    אירועים ששוחזרו."""
    __tablename__ = "ledger_ownership"
    aggregate_id = Column(String, primary_key=True)
    aggregate_type = Column(String, nullable=False)
    company_id = Column(String, ForeignKey("companies.company_id"), nullable=True, index=True)
    trustee_id = Column(String, ForeignKey("trustees.trustee_id"), nullable=True, index=True)
    employee_id = Column(String, ForeignKey("employees.employee_id"), nullable=True, index=True)


# ===================================================================
# מסמכים ו"אישור קבלה" פנימי (v0.9.0)
#
# *** במכוון לא "חתימה"/signature בשום מקום - לא בקוד, לא ב-API, לא ב-UI ***.
# למערכת הזו אין אימות זהות, הצפנה או גורם שלישי מאשר - "חתימה דיגיטלית" היה
# מתחזה לתוקף משפטי שאין לו. זו אותה הפרת "לא ממציאים סמכות" כמו כלל מס בדוי
# (GOAL.md חוק ברזל 1), רק על מסמך משפטי במקום שיעור מס. ראו DocumentStatus.
# ===================================================================

# String חופשי (לא SQLEnum) - אותה מוסכמה כמו TAX_CALCULATION_METHODS/
# LEDGER_EVENT_TYPES: אוצר מילים סגור שנבדק באפליקציה, לא באילוץ DB, כי
# תבניות חדשות (v0.9.0 שלב 2) יתווספו בלי מיגרציה.
DOCUMENT_TEMPLATE_TYPES = {"GRANT_LETTER", "SECTION_102_APPENDIX", "TRUSTEE_DEPOSIT_CONFIRMATION"}


class DocumentStatus(str, Enum):
    DRAFT = "DRAFT"
    SENT = "SENT"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    DECLINED = "DECLINED"
    EXPIRED = "EXPIRED"


class Document(Base):
    # company_id/employee_id/trustee_id מוכפלים כאן (לא רק דרך grant_id), אותה
    # סיבה בדיוק כמו LedgerOwnership - בדיקת הרשאה חייבת להיות השוואת עמודה
    # ישירה וזולה על השורה עצמה, לא join שסומך על grant_id בלי לאמת אותו בנפרד.
    __tablename__ = "documents"
    document_id = Column(String, primary_key=True, default=generate_uuid)
    template_type = Column(String, nullable=False)
    grant_id = Column(String, ForeignKey("grants.grant_id"), nullable=False, index=True)
    company_id = Column(String, ForeignKey("companies.company_id"), nullable=False, index=True)
    employee_id = Column(String, ForeignKey("employees.employee_id"), nullable=False, index=True)
    trustee_id = Column(String, ForeignKey("trustees.trustee_id"), nullable=True, index=True)
    status = Column(SQLEnum(DocumentStatus), default=DocumentStatus.DRAFT, nullable=False)
    # גרסה: שינוי בנתוני המענק אחרי שהמסמך נוצר מייצר גרסה חדשה, לא דריסה -
    # ההחלטה המפורשת בתכנון v0.9.0 (מקביל לעיקרון הבי-טמפורלי מ-v0.6.0: אסור
    # לאבד את מה שאושר בעבר, גם אם המקור השתנה אחר כך).
    version = Column(Integer, nullable=False, default=1)
    is_latest = Column(Boolean, nullable=False, default=True)
    # נתיב יחסי בתוך document_store/ (לא מוחלט - כמו sqlite:///./esop_database.db,
    # כדי שהתיקייה תישאר ניידת בין מחשבים). לעולם לא מוגש כקובץ סטטי ישירות -
    # רק דרך endpoint מאומת שמפעיל את בדיקת הבעלות.
    file_path = Column(String, nullable=False)
    file_sha256 = Column(String, nullable=False)
    generated_at = Column(UtcDateTime, default=utcnow, nullable=False)
    sent_at = Column(UtcDateTime, nullable=True)
    # מועד פקיעת בקשת האישור, נקבע בשליחה. חותמת זמן ולא תאריך בכוונה: רגע
    # פיזי מדויק אינו נזקק להכרעה בין UTC לשעון העסקי, וגבול שנמדד בימים
    # קלנדריים היה מחזיר את שאלת ח1/ח2 דרך הדלת האחורית.
    # NULL = אין דדליין (מסמכים שנשלחו לפני v0.9.1), ולא "פג".
    expires_at = Column(UtcDateTime, nullable=True)
    acknowledged_at = Column(UtcDateTime, nullable=True)
    acknowledged_by_user_id = Column(String, ForeignKey("users.user_id"), nullable=True)
    created_by_user_id = Column(String, ForeignKey("users.user_id"), nullable=True)


class DocumentAcknowledgmentWindowOverride(Base):
    """חלון אישור קבלה פר-סוג-מסמך (v1.0.2, HANDOFF.md debt item 2) - שכבה
    שנייה, עדינה יותר, מעל Company.acknowledgment_window_days (שכבר קיים,
    v1.0.1). שורה חסרה ל-(company_id, template_type) = אין override לסוג
    הזה - נופל ל-acknowledgment_window_days של החברה, ואם גם הוא None -
    לקבוע הגלובלי (ACKNOWLEDGMENT_WINDOW_DAYS, document_status.py). אותה
    מוסכמה בדיוק כמו total_authorized_shares/acknowledgment_window_days:
    None/היעדר שורה הוא "לא הוגדר", לא "0 יום".
    template_type הוא String חופשי ולא FK - הוא לא נבדק מול טבלה, רק מול
    DOCUMENT_TEMPLATE_TYPES ברמת האפליקציה (אותה מוסכמה כמו Document.template_type
    עצמו), כדי שסוג מסמך חדש לא ידרוש מיגרציה כאן."""
    __tablename__ = "document_acknowledgment_window_overrides"
    override_id = Column(String, primary_key=True, default=generate_uuid)
    company_id = Column(String, ForeignKey("companies.company_id"), nullable=False, index=True)
    template_type = Column(String, nullable=False)
    window_days = Column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("company_id", "template_type",
                         name="uq_doc_ack_window_override_company_type"),
        CheckConstraint("window_days > 0",
                        name="ck_doc_ack_window_override_positive"),
    )


# ===================================================================
# ייצוא / ייבוא וניידות נתונים (v0.9.1 שלב ב)
# ===================================================================
# SQLEnum ולא String חופשי (בשונה מ-LEDGER_EVENT_TYPES/TAX_CALCULATION_METHODS):
# direction/status הן מכונת מצבים סגורה וקבועה כמו DocumentStatus, לא אוצר מילים
# שצפוי לגדול - אין כאן את בעיית "CHECK דורש בנייה מחדש של הטבלה" שהנחתה את
# הבחירה בצד השני.

class DataTransferDirection(str, Enum):
    EXPORT = "EXPORT"
    IMPORT_DRY_RUN = "IMPORT_DRY_RUN"
    IMPORT_COMMIT = "IMPORT_COMMIT"


class DataTransferStatus(str, Enum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    # דגל ל-IMPORT_DRY_RUN שכבר "נוצל" ע"י commit - זה מה ש-based_on_run_id
    # למטה מאפשר לבדוק: דריי-ראן שכבר יש לו commit לא ניתן להפעיל שוב.
    COMMITTED = "COMMITTED"


class DataTransferRun(Base):
    """רשומת היסטוריה אחת לכל ייצוא/ייבוא-דריי-ראן/ייבוא-commit - זה מה שמזין
    את מסך "היסטוריית ייצוא" ואת בדיקת ה-403 בהורדה (השוואה מול
    source_company_id/target_company_id, לא הנחה על מי שביקש).

    based_on_run_id הוא FK עצמי, באותו דפוס בדיוק כמו LedgerEvent.corrects_event_id:
    מצביע מ-IMPORT_COMMIT אל ה-IMPORT_DRY_RUN שעליו הוא מבוסס, כדי ש-409 על
    דריי-ראן ישן/מנוצל (שני השלבים - decision 6/8 בתכנון) יהיה דבר שאפשר
    לבדוק ב-DB ולא רק להניח בקוד.

    export_schema_version ולא schema_version: LedgerEvent.schema_version מתאר
    את צורת ה-payload של אירוע בודד - מושג שונה מגרסת חבילת הייצוא כולה. שם
    זהה היה יוצר קונפליקט שקט במיפוי עמודות בין ה-CSV/JSON של הייצוא לזה
    של יומן האירועים המיוצא בתוכו.
    """
    __tablename__ = "data_transfer_runs"
    run_id = Column(String, primary_key=True, default=generate_uuid)
    direction = Column(SQLEnum(DataTransferDirection), nullable=False)
    source_company_id = Column(String, ForeignKey("companies.company_id"), nullable=True, index=True)
    target_company_id = Column(String, ForeignKey("companies.company_id"), nullable=True, index=True)
    initiated_by_user_id = Column(String, ForeignKey("users.user_id"), nullable=False)
    export_schema_version = Column(Integer, nullable=False)
    based_on_run_id = Column(String, ForeignKey("data_transfer_runs.run_id"), nullable=True)
    rows_attempted = Column(Integer, default=0, nullable=False)
    rows_succeeded = Column(Integer, default=0, nullable=False)
    rows_failed = Column(Integer, default=0, nullable=False)
    status = Column(SQLEnum(DataTransferStatus), default=DataTransferStatus.PENDING, nullable=False)
    # נתיב יחסי בתוך export_store/, אותה מוסכמה בדיוק כמו Document.file_path -
    # לעולם לא מוגש כקובץ סטטי, רק דרך endpoint מאומת שמפעיל בדיקת company_id.
    file_path = Column(String, nullable=True)
    created_at = Column(UtcDateTime, default=utcnow, nullable=False)


# ===================================================================
# טבלת הון (Cap Table) - סוגי מניות, בעלי מניות, הקצאות מניות (v1.0.0 שלב א)
# ===================================================================
# שלב א בלבד: מודל דאטה + אינטגרציית ledger. אין כאן חישוב דילול ואין UI -
# אלה שלב ב, שמתוכנן בנפרד רק אחרי ששלב א יתייצב (ראו FEATURE_SPEC.md).
# ShareIssuance הוא ledger-native *מהיום הראשון* - לא "פרויקציה מחושבת" שנוספה
# בדיעבד כמו ב-OptionPool - כדי ש-snapshot היסטורי לפי תאריך (שלב ב) יהיה
# אפשרי מבנית מהתחלה. זו ההחלטה הארכיטקטונית שכל מומחי התכנון התכנסו עליה.

class ShareClass(Base):
    # סוג מניה (Common / Preferred A / ...). class_type הוא String חופשי ולא
    # SQLEnum - אותה מוסכמה בדיוק כמו LEDGER_EVENT_TYPES/TAX_CALCULATION_METHODS:
    # אוצר מילים סגור שנבדק באפליקציה, לא באילוץ DB, כי סוגי Preferred נוספים
    # (Preferred B/C...) צפויים להתווסף בלי מיגרציה.
    # seniority_order: מספר קטן יותר = משולם קודם ב-waterfall של פירוק (v1.4.0
    # יהיה הצרכן הראשון בפועל) - זו החלטה עסקית לכל חברה, לא אינווריאנט דאטה,
    # ולכן בלי UNIQUE ברמת ה-DB (אין מניעה משתי שורות לחלוק סדר בכוונה/בטעות -
    # זה לא תפקיד ה-DB לשפוט).
    __tablename__ = "share_classes"
    share_class_id = Column(String, primary_key=True, default=generate_uuid)
    company_id = Column(String, ForeignKey("companies.company_id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    class_type = Column(String, nullable=False)
    seniority_order = Column(Integer, nullable=False)
    created_at = Column(UtcDateTime, default=utcnow)


class Shareholder(Base):
    # company_id NOT NULL: חד-חברתי בכוונה, מראה את Trustee ולא ישות
    # חוצת-חברות - זו ההכרעה שפותרת ארכיטקטונית (לא רק בקוד scoping נוסף) את
    # חשש ה-IDOR שהועלה בסקירת אבטחה בתכנון: משקיע שמחזיק בשתי חברות-פורטפוליו
    # מקבל שתי שורות נפרדות, בדיוק כמו כל ישות אחרת בקוד הזה שהיא scoped לחברה.
    __tablename__ = "shareholders"
    shareholder_id = Column(String, primary_key=True, default=generate_uuid)
    company_id = Column(String, ForeignKey("companies.company_id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    # FOUNDER / INVESTOR / EMPLOYEE / ENTITY - אוצר מילים פתוח, אותה מוסכמה
    # כמו ShareClass.class_type למעלה.
    shareholder_type = Column(String, nullable=False)
    # nullable=True: מאוכלס רק כשבעל המניות הזה הוא *גם* עובד קיים במערכת
    # (למשל עובד שמימש אופציות לכדי מניות ממשיות) - כדי לקשר לזהות הקיימת
    # ולא לשכפל אותה. משקיע חיצוני (investor) פשוט משאיר את זה NULL.
    employee_id = Column(String, ForeignKey("employees.employee_id"), nullable=True, index=True)
    created_at = Column(UtcDateTime, default=utcnow)


class ShareIssuance(Base):
    # ledger-native מהיום הראשון (ראו הערת הסעיף למעלה). shares היא עמודת
    # פרויקציה מוטטת - מתעדכנת באותה טרנזקציה שמוסיפה את אירוע ה-
    # SHARE_ISSUANCE_ESTABLISHED, אותו דפוס בדיוק כמו OptionPool.allocated_shares.
    __tablename__ = "share_issuances"
    share_issuance_id = Column(String, primary_key=True, default=generate_uuid)
    # מוכפל ישירות על השורה ולא נגזר דרך shareholder_id - אותו דפוס הגנתי
    # "עמודה ישירה, לא join" כמו Document/LedgerOwnership: בדיקת הרשאה חייבת
    # להיות השוואת עמודה ישירה וזולה על השורה עצמה.
    company_id = Column(String, ForeignKey("companies.company_id"), nullable=False, index=True)
    shareholder_id = Column(String, ForeignKey("shareholders.shareholder_id"), nullable=False, index=True)
    share_class_id = Column(String, ForeignKey("share_classes.share_class_id"), nullable=False, index=True)
    shares = Column(Float, nullable=False)
    # קלט מפורש מהקורא, לעולם לא נגזר מהשעון - אותו דפוס כמו Grant.grant_date.
    # זה מה שהופך הזנת נתונים היסטוריים ו-snapshot-לפי-תאריך עתידי (שלב ב)
    # לנכונים, בדיוק אותו לקח שכבר נלמד בקודבייס הזה (ח1/ח2).
    issue_date = Column(Date, nullable=False)
    created_at = Column(UtcDateTime, default=utcnow)


# ===================================================================
# דוחות שמורים (Saved Reports) - v1.1.0, "דוחות, ייצוא ו-BI"
# ===================================================================
# הטבלה החדשה היחידה בגרסה הזו - כל שאר v1.1.0 קורא מדאטה קיים (ledger,
# audit log, cap table, כללי ההתראה) בזמן קריאה בלבד, בדיוק כמו
# compute_cap_table_snapshot/NotificationFeed. הטבלה כאן שומרת רק את
# *קונפיגורציית* הדוח (סוג + פילטרים) - לא את תוצאת הדוח עצמה.

# סוגי הדוחות הנתמכים - String חופשי ולא SQLEnum, אותה מוסכמה בדיוק כמו
# ShareClass.class_type/TAX_CALCULATION_METHODS/LEDGER_EVENT_TYPES: אוצר
# מילים סגור שנבדק באפליקציה (routes/reports.py), לא באילוץ DB - כי דוח
# חדש (למשל v1.4.0 שיוסיף ערך שקלי לחשיפה-לפי-נאמן) לא ידרוש מיגרציה כאן.
SAVED_REPORT_TYPES = {
    "POOL_STATUS",
    "TRUSTEE_EXPOSURE",
    "DEADLINE_RISK",
    "EXERCISE_ACTIVITY",
    "COMPENSATION_EXPENSE",
    "MOVEMENT",
    "ASC718_READINESS",
}


class SavedReport(Base):
    """קונפיגורציית דוח שמורה (סוג + פילטרים), לא הדוח עצמו - שממשיך להיות
    מחושב מחדש מהדאטה הקיים בכל קריאה, בדיוק כמו compute_cap_table_snapshot.

    company_scope (backend/app/services/company_scope.py): הטבלה הזו קרובה
    מבחינה מושגית ל"נוחות עבודה אישית" (saved filter) ולא לדאטה עסקי ליבתי
    כמו grants/cap table - ולכן רשומה ב-SPECIAL_CASED_TABLES ולא ב-
    TABLE_REGISTRY: היא לא אמורה לנדוד עם ייצוא/ייבוא מלא של חברה בין
    סביבות, בדיוק כמו NotificationPreference/NotificationDismissal (אף
    שלאלה, בשונה מכאן, אין בכלל עמודת company_id ישירה ולכן הן לא נדרשות
    ברישום הזה כלל - ראו tests/test_project_invariants.py::
    test_every_company_scoped_table_is_registered_or_explicitly_special_cased).

    owner_user_id: היוצר. היום כל חברה חולקת חשבון admin משותף אחד
    (admin@{company_id}.demo), כך ש"פרטי"="משותף" בפועל - אבל הסכמה כבר
    בנויה לזהות-מנהל אמיתית שתגיע עם v1.5.0 (RBAC), ראו PLAN v1.1.0 החלטה 4.
    """
    __tablename__ = "saved_reports"
    report_id = Column(String, primary_key=True, default=generate_uuid)
    company_id = Column(String, ForeignKey("companies.company_id"), nullable=False, index=True)
    owner_user_id = Column(String, ForeignKey("users.user_id"), nullable=False)
    # default=True: פרטי-כברירת-מחדל (החלטה מוצרית מפורשת, PLAN v1.1.0) - אבל
    # עמודת Boolean רגילה, לא קבוע-אמת-בלבד: False ("משותף לכל מנהלי החברה")
    # הוא ערך נתמך לגמרי ברמת הסכמה, לא רק תיאורטי.
    is_private = Column(Boolean, default=True, nullable=False)
    name = Column(String, nullable=False)
    # אוצר-מילים סגור - ראו SAVED_REPORT_TYPES למעלה.
    report_type = Column(String, nullable=False)
    # JSON טקסטואלי - אותה מוסכמה כמו AuditLog.before_value/LedgerEvent.payload.
    # nullable=False: דוח שמור בלי שום פילטר הוא עדיין "{}" תקין, לא None.
    filter_params = Column(String, nullable=False)
    created_at = Column(UtcDateTime, default=utcnow)