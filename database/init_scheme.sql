-- SQL Schema Reference - 15 טבלאות, משקף את backend/app/models.py בגרסה 0.5.0.
--
-- זהו קובץ תיעוד/reference בלבד. ה-DB בפועל נבנה על ידי Alembic
-- (python -m alembic upgrade head) מאז גרסה 0.4.0 - לא על ידי הקובץ הזה
-- ולא יותר על ידי Base.metadata.create_all(). כל שינוי סכמה חייב מיגרציה;
-- הקובץ הזה מסונכרן ידנית אחריה.
--
-- *** הקובץ הזה שיקר בעבר ***: הוא הכריז על CHECK constraints לערכי enum
-- (status/role/grant_type) שמעולם לא היו קיימים ב-DB האמיתי, ופספס שתי טבלאות
-- ועמודה. הגרסה הזו נגזרה מ-sqlite_master של DB שנבנה ב-alembic upgrade head,
-- ולכן היא תואמת בדיוק. אל תוסיפו כאן אילוץ שלא קיים במודל - אילוץ מתועד שלא
-- נאכף מסוכן יותר מהיעדר תיעוד.
--
-- שתי הערות על "מה לא רואים כאן":
-- 1. ב-SQLite אכיפת Foreign Key כבויה כברירת מחדל בכל connection.
--    backend/app/database.py ו-migrations/env.py מפעילים PRAGMA foreign_keys=ON
--    אוטומטית, אבל בכלי חיצוני צריך להריץ ידנית:  PRAGMA foreign_keys = ON;
-- 2. אין כאן כמעט DEFAULT clauses, כי SQLAlchemy מיישם את רוב ברירות המחדל
--    (uuid, created_at, is_active) בצד Python בזמן INSERT ולא ברמת ה-DB.
--    INSERT ידני ב-sqlite3 חייב לספק את הערכים האלה בעצמו.

CREATE TABLE IF NOT EXISTS companies (
    company_id VARCHAR NOT NULL PRIMARY KEY,
    name VARCHAR NOT NULL,
    country_code VARCHAR NOT NULL,
    is_active BOOLEAN NOT NULL,
    created_at DATETIME
);

CREATE TABLE IF NOT EXISTS option_pools (
    pool_id VARCHAR NOT NULL PRIMARY KEY,
    company_id VARCHAR NOT NULL REFERENCES companies(company_id),
    total_shares FLOAT NOT NULL,
    allocated_shares FLOAT NOT NULL,
    unallocated_shares FLOAT NOT NULL,
    created_at DATETIME,
    -- מונע דריפט שקט בין allocated_shares ל-unallocated_shares (שני שדות שמתעדכנים
    -- ידנית בקוד האפליקציה) - הם חייבים תמיד לסכם בדיוק לגודל הפול.
    CONSTRAINT ck_option_pools_shares_balance
        CHECK (allocated_shares + unallocated_shares = total_shares)
);
CREATE INDEX IF NOT EXISTS ix_option_pools_company_id ON option_pools(company_id);

CREATE TABLE IF NOT EXISTS trustees (
    trustee_id VARCHAR NOT NULL PRIMARY KEY,
    company_id VARCHAR NOT NULL REFERENCES companies(company_id),
    name VARCHAR NOT NULL,
    registration_number VARCHAR NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_trustees_company_id ON trustees(company_id);

CREATE TABLE IF NOT EXISTS employees (
    employee_id VARCHAR NOT NULL PRIMARY KEY,
    -- company_id יכול להיות NULL: עובד ששרד את סגירת/פירוק החברה המעסיקה,
    -- בשונה מ-companies.is_active=False שמתאר חברה שעדיין קיימת אך לא פעילה.
    company_id VARCHAR REFERENCES companies(company_id),
    first_name VARCHAR NOT NULL,
    last_name VARCHAR NOT NULL,
    email VARCHAR NOT NULL UNIQUE,
    country_code VARCHAR NOT NULL,
    -- ACTIVE / TERMINATED / ON_LEAVE / DECEASED. האורך נגזר מהערך הארוך ביותר
    -- ב-Enum של SQLAlchemy; אין CHECK - האכיפה היא בשכבת ה-ORM בלבד.
    status VARCHAR(10),
    hire_date DATE NOT NULL,
    termination_date DATE,
    birth_date DATE
);
CREATE INDEX IF NOT EXISTS ix_employees_company_id ON employees(company_id);

CREATE TABLE IF NOT EXISTS grants (
    grant_id VARCHAR NOT NULL PRIMARY KEY,
    employee_id VARCHAR NOT NULL REFERENCES employees(employee_id),
    pool_id VARCHAR NOT NULL REFERENCES option_pools(pool_id),
    trustee_id VARCHAR REFERENCES trustees(trustee_id),
    grant_date DATE NOT NULL,
    -- IL_102_CAPITAL_GAINS / IL_102_WORK_INCOME / US_ISO / US_NSO. אין CHECK ב-DB.
    grant_type VARCHAR(20) NOT NULL,
    total_options FLOAT NOT NULL,
    exercise_price FLOAT NOT NULL,
    currency VARCHAR,
    trustee_deposit_date DATE,
    -- תנאי תוכנית (plan term) ולא הוראת מיסוי סטטוטורית - כמה ימים אחרי עזיבה
    -- עדיין מותר להגיש בקשת מימוש. לכן ניתן להגדרה לפי מענק ולא קבוע בחוק.
    post_termination_window_days INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_grants_employee_id ON grants(employee_id);
CREATE INDEX IF NOT EXISTS ix_grants_pool_id ON grants(pool_id);
CREATE INDEX IF NOT EXISTS ix_grants_trustee_id ON grants(trustee_id);

CREATE TABLE IF NOT EXISTS vesting_schedules (
    schedule_id VARCHAR NOT NULL PRIMARY KEY,
    -- UNIQUE הופך את זה ל-1:1 אמיתי ברמת ה-DB (בעבר זו הייתה רק הנחה ב-ORM, לא אכוף)
    grant_id VARCHAR NOT NULL UNIQUE REFERENCES grants(grant_id),
    start_date DATE NOT NULL,
    cliff_months INTEGER,
    total_months INTEGER,
    paused_days_total INTEGER
);

CREATE TABLE IF NOT EXISTS tax_rates_history (
    tax_rule_id VARCHAR NOT NULL PRIMARY KEY,
    country_code VARCHAR NOT NULL,
    grant_type VARCHAR NOT NULL,
    effective_start_date DATE NOT NULL,
    capital_gains_rate FLOAT NOT NULL,
    official_source_url VARCHAR NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tax_rates_history_country_code ON tax_rates_history(country_code);
CREATE INDEX IF NOT EXISTS ix_tax_rates_history_grant_type ON tax_rates_history(grant_type);

CREATE TABLE IF NOT EXISTS income_tax_brackets (
    -- מדרגות מס פרוגרסיביות, versioned לפי (country_code, grant_type,
    -- effective_start_date) - כל השורות עם אותה שלישייה הן אותה "גרסה" של הטבלה.
    -- *** נתוני דמו לתרגול QA בלבד - לא חוק מס אמיתי ***
    bracket_id VARCHAR NOT NULL PRIMARY KEY,
    country_code VARCHAR NOT NULL,
    grant_type VARCHAR NOT NULL,
    effective_start_date DATE NOT NULL,
    bracket_order INTEGER NOT NULL,
    min_amount FLOAT NOT NULL,
    -- max_amount NULL = המדרגה העליונה הפתוחה (בלי תקרה)
    max_amount FLOAT,
    rate FLOAT NOT NULL,
    official_source_url VARCHAR NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_income_tax_brackets_country_code ON income_tax_brackets(country_code);
CREATE INDEX IF NOT EXISTS ix_income_tax_brackets_grant_type ON income_tax_brackets(grant_type);
CREATE INDEX IF NOT EXISTS ix_income_tax_brackets_effective_start_date ON income_tax_brackets(effective_start_date);

CREATE TABLE IF NOT EXISTS stock_prices_history (
    price_id VARCHAR NOT NULL PRIMARY KEY,
    company_id VARCHAR NOT NULL REFERENCES companies(company_id),
    price_date DATE NOT NULL,
    fmv_price FLOAT NOT NULL,
    currency VARCHAR
);
CREATE INDEX IF NOT EXISTS ix_stock_prices_history_company_id ON stock_prices_history(company_id);

-- ===================================================================
-- Auth ו-workflow בקשות מימוש (3 הפורטלים: admin/trustee/employee)
-- ===================================================================

CREATE TABLE IF NOT EXISTS users (
    user_id VARCHAR NOT NULL PRIMARY KEY,
    username VARCHAR NOT NULL,
    password_hash VARCHAR NOT NULL,
    password_salt VARCHAR NOT NULL,
    -- COMPANY_ADMIN / TRUSTEE / EMPLOYEE. אין CHECK ב-DB.
    role VARCHAR(13) NOT NULL,
    -- בדיוק אחד מהשלושה הבאים אמור להיות מאוכלס, בהתאם ל-role - לא נאכף ב-DB.
    company_id VARCHAR REFERENCES companies(company_id),
    trustee_id VARCHAR REFERENCES trustees(trustee_id),
    employee_id VARCHAR REFERENCES employees(employee_id),
    is_active BOOLEAN NOT NULL,
    created_at DATETIME
);
-- ייחודיות ה-username נאכפת דרך האינדקס הייחודי הזה ולא דרך UNIQUE בהגדרת הטבלה.
CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users(username);

CREATE TABLE IF NOT EXISTS user_sessions (
    token VARCHAR NOT NULL PRIMARY KEY,
    user_id VARCHAR NOT NULL REFERENCES users(user_id),
    created_at DATETIME,
    expires_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_user_sessions_user_id ON user_sessions(user_id);

CREATE TABLE IF NOT EXISTS exercise_requests (
    request_id VARCHAR NOT NULL PRIMARY KEY,
    grant_id VARCHAR NOT NULL REFERENCES grants(grant_id),
    employee_id VARCHAR NOT NULL REFERENCES employees(employee_id),
    options_requested FLOAT NOT NULL,
    requested_at DATETIME,
    -- PENDING / APPROVED / REJECTED. אין CHECK ב-DB.
    status VARCHAR(8) NOT NULL,
    reviewed_by_user_id VARCHAR REFERENCES users(user_id),
    reviewed_at DATETIME,
    review_notes VARCHAR
);
CREATE INDEX IF NOT EXISTS ix_exercise_requests_grant_id ON exercise_requests(grant_id);
CREATE INDEX IF NOT EXISTS ix_exercise_requests_employee_id ON exercise_requests(employee_id);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id VARCHAR NOT NULL PRIMARY KEY,
    entity_type VARCHAR NOT NULL,
    entity_id VARCHAR NOT NULL,
    action VARCHAR NOT NULL,
    actor_user_id VARCHAR REFERENCES users(user_id),
    occurred_at DATETIME NOT NULL,
    -- snapshot לפני/אחרי כ-JSON טקסטואלי - מספיק לתרגול QA, בלי JSON column ייעודי
    before_value VARCHAR,
    after_value VARCHAR,
    notes VARCHAR
);
CREATE INDEX IF NOT EXISTS ix_audit_log_entity_type ON audit_log(entity_type);
CREATE INDEX IF NOT EXISTS ix_audit_log_entity_id ON audit_log(entity_id);
CREATE INDEX IF NOT EXISTS ix_audit_log_occurred_at ON audit_log(occurred_at);

-- ===================================================================
-- Notification Center (v0.5.0)
-- ===================================================================
-- אין כאן טבלת notifications בכוונה: ההתראות מחושבות בכל קריאה מ-
-- DeterministicESOPEngine מתוך הנתונים הקיימים. התראה מאוחסנת מתיישנת ברגע
-- שהמענק משתנה, וה-feed היה מציג מצב שקרי. נשמר רק מצב המשתמש.

CREATE TABLE IF NOT EXISTS notification_preferences (
    preference_id VARCHAR NOT NULL PRIMARY KEY,
    user_id VARCHAR NOT NULL REFERENCES users(user_id),
    -- VESTING_EVENT_NEAR / TRUSTEE_HOLDING_ENDING / PTEW_CLOSING /
    -- REQUEST_PENDING_TOO_LONG / FULLY_VESTED_UNEXERCISED.
    -- אין CHECK בכוונה: רשימת הכללים עוד צפויה לגדול, ותוספת ערך ל-CHECK ב-SQLite
    -- מחייבת בנייה מחדש של הטבלה. מקור האמת הוא
    -- backend/app/models.py :: NOTIFICATION_DEFAULT_LEAD_DAYS
    rule VARCHAR NOT NULL,
    enabled BOOLEAN NOT NULL,
    -- כמה ימים מראש הכלל יורה. ברירות המחדל (14/30/30/7/90) הן החלטת מוצר
    -- ומיושמות בצד Python, לכן אין כאן DEFAULT ברמת ה-DB.
    lead_days INTEGER NOT NULL,
    -- שורה אחת בדיוק לכל (משתמש, כלל). בלי זה upsert שנכשל באמצע או שתי לשוניות
    -- במקביל יוצרים שתי שורות סותרות לאותו כלל, וה-feed מתחיל להיות תלוי בסדר
    -- השורות שחזר מה-DB - כלומר התראות שנעלמות וחוזרות בלי סיבה נראית לעין.
    CONSTRAINT uq_notification_preferences_user_rule UNIQUE (user_id, rule)
);
CREATE INDEX IF NOT EXISTS ix_notification_preferences_user_id ON notification_preferences(user_id);

CREATE TABLE IF NOT EXISTS notification_dismissals (
    dismissal_id VARCHAR NOT NULL PRIMARY KEY,
    user_id VARCHAR NOT NULL REFERENCES users(user_id),
    -- מפתח דטרמיניסטי "rule|entity_id|trigger_date". הוא חייב להיות דטרמיניסטי
    -- כי ההתראה עצמה לא נשמרת: זה הדבר היחיד שמקשר סגירה שנעשתה אתמול להתראה
    -- שהמנוע מייצר מחדש היום.
    notification_key VARCHAR NOT NULL,
    dismissed_at DATETIME
);
CREATE INDEX IF NOT EXISTS ix_notification_dismissals_user_id ON notification_dismissals(user_id);
-- אינדקס ייחודי אחד לשני תפקידים:
-- (1) אכיפה - סגירה חוזרת של אותה התראה היא idempotent ברמת ה-DB, כך שה-endpoint
--     יכול להישען על IntegrityError במקום בדיקה-ואז-הכנסה (race בלחיצה כפולה).
-- (2) ביצועים - זה ה-lookup החם: כל בקשת feed בודקת כל התראה מועמדת מול הצמד הזה.
--     UNIQUE רגיל היה יוצר autoindex זהה, ולכן אינדקס נוסף נפרד היה כפילות מיותרת.
CREATE UNIQUE INDEX IF NOT EXISTS ix_notification_dismissals_user_key
    ON notification_dismissals(user_id, notification_key);
