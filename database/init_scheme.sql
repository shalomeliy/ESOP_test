-- SQL Schema Reference - משקף במדויק את backend/app/models.py (8 טבלאות, לא 3).
-- ה-DB בפועל נבנה על ידי SQLAlchemy דרך Base.metadata.create_all() ולא על ידי הקובץ
-- הזה - זהו קובץ תיעוד/reference בלבד, אבל הוא חייב להיות מסונכרן עם המודלים.
--
-- חשוב: ב-SQLite אכיפת Foreign Key כבויה כברירת מחדל בכל connection.
-- backend/app/database.py מפעיל את זה עכשיו אוטומטית (PRAGMA foreign_keys=ON),
-- אבל אם מתחברים ל-DB הזה בכלי אחר צריך להפעיל את זה ידנית:
--   PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS companies (
    company_id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    country_code VARCHAR NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS option_pools (
    pool_id VARCHAR PRIMARY KEY,
    company_id VARCHAR NOT NULL REFERENCES companies(company_id),
    total_shares FLOAT NOT NULL,
    allocated_shares FLOAT NOT NULL DEFAULT 0.0,
    unallocated_shares FLOAT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- מונע דריפט שקט בין allocated_shares ל-unallocated_shares (שני שדות שמתעדכנים
    -- ידנית בקוד האפליקציה) - הם חייבים תמיד לסכם בדיוק לגודל הפול.
    CHECK (allocated_shares + unallocated_shares = total_shares)
);
CREATE INDEX IF NOT EXISTS ix_option_pools_company_id ON option_pools(company_id);

CREATE TABLE IF NOT EXISTS trustees (
    trustee_id VARCHAR PRIMARY KEY,
    company_id VARCHAR NOT NULL REFERENCES companies(company_id),
    name VARCHAR NOT NULL,
    registration_number VARCHAR NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_trustees_company_id ON trustees(company_id);

CREATE TABLE IF NOT EXISTS employees (
    employee_id VARCHAR PRIMARY KEY,
    -- company_id יכול להיות NULL: עובד ששרד את סגירת/פירוק החברה המעסיקה,
    -- בשונה מ-companies.is_active=False שמתאר חברה שעדיין קיימת אך לא פעילה.
    company_id VARCHAR REFERENCES companies(company_id),
    first_name VARCHAR NOT NULL,
    last_name VARCHAR NOT NULL,
    email VARCHAR UNIQUE NOT NULL,
    country_code VARCHAR NOT NULL,
    status VARCHAR DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'TERMINATED', 'ON_LEAVE', 'DECEASED')),
    hire_date DATE NOT NULL,
    termination_date DATE,
    birth_date DATE
);
CREATE INDEX IF NOT EXISTS ix_employees_company_id ON employees(company_id);

CREATE TABLE IF NOT EXISTS grants (
    grant_id VARCHAR PRIMARY KEY,
    employee_id VARCHAR NOT NULL REFERENCES employees(employee_id),
    pool_id VARCHAR NOT NULL REFERENCES option_pools(pool_id),
    trustee_id VARCHAR REFERENCES trustees(trustee_id),
    grant_date DATE NOT NULL,
    grant_type VARCHAR NOT NULL CHECK (grant_type IN
        ('IL_102_CAPITAL_GAINS', 'IL_102_WORK_INCOME', 'US_ISO', 'US_NSO')),
    total_options FLOAT NOT NULL,
    exercise_price FLOAT NOT NULL,
    currency VARCHAR DEFAULT 'USD',
    trustee_deposit_date DATE
);
CREATE INDEX IF NOT EXISTS ix_grants_employee_id ON grants(employee_id);
CREATE INDEX IF NOT EXISTS ix_grants_pool_id ON grants(pool_id);
CREATE INDEX IF NOT EXISTS ix_grants_trustee_id ON grants(trustee_id);

CREATE TABLE IF NOT EXISTS vesting_schedules (
    schedule_id VARCHAR PRIMARY KEY,
    -- UNIQUE הופך את זה ל-1:1 אמיתי ברמת ה-DB (בעבר זה היה רק הנחה ב-ORM, לא אכוף)
    grant_id VARCHAR NOT NULL UNIQUE REFERENCES grants(grant_id),
    start_date DATE NOT NULL,
    cliff_months INTEGER DEFAULT 12,
    total_months INTEGER DEFAULT 48,
    paused_days_total INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tax_rates_history (
    tax_rule_id VARCHAR PRIMARY KEY,
    country_code VARCHAR NOT NULL,
    grant_type VARCHAR NOT NULL,
    effective_start_date DATE NOT NULL,
    capital_gains_rate FLOAT NOT NULL,
    official_source_url VARCHAR NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tax_rates_lookup ON tax_rates_history(country_code, grant_type, effective_start_date);

CREATE TABLE IF NOT EXISTS stock_prices_history (
    price_id VARCHAR PRIMARY KEY,
    company_id VARCHAR NOT NULL REFERENCES companies(company_id),
    price_date DATE NOT NULL,
    fmv_price FLOAT NOT NULL,
    currency VARCHAR DEFAULT 'USD'
);
CREATE INDEX IF NOT EXISTS ix_stock_prices_company_date ON stock_prices_history(company_id, price_date);

-- ===================================================================
-- Auth ו-workflow בקשות מימוש (3 הפורטלים: admin/trustee/employee)
-- ===================================================================

CREATE TABLE IF NOT EXISTS users (
    user_id VARCHAR PRIMARY KEY,
    username VARCHAR NOT NULL UNIQUE,
    password_hash VARCHAR NOT NULL,
    password_salt VARCHAR NOT NULL,
    role VARCHAR NOT NULL CHECK (role IN ('COMPANY_ADMIN', 'TRUSTEE', 'EMPLOYEE')),
    -- בדיוק אחד מהשלושה הבאים אמור להיות מאוכלס, בהתאם ל-role - לא נאכף ב-DB.
    company_id VARCHAR REFERENCES companies(company_id),
    trustee_id VARCHAR REFERENCES trustees(trustee_id),
    employee_id VARCHAR REFERENCES employees(employee_id),
    is_active BOOLEAN NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_users_username ON users(username);

CREATE TABLE IF NOT EXISTS user_sessions (
    token VARCHAR PRIMARY KEY,
    user_id VARCHAR NOT NULL REFERENCES users(user_id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_user_sessions_user_id ON user_sessions(user_id);

CREATE TABLE IF NOT EXISTS exercise_requests (
    request_id VARCHAR PRIMARY KEY,
    grant_id VARCHAR NOT NULL REFERENCES grants(grant_id),
    employee_id VARCHAR NOT NULL REFERENCES employees(employee_id),
    options_requested FLOAT NOT NULL,
    requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED')),
    reviewed_by_user_id VARCHAR REFERENCES users(user_id),
    reviewed_at TIMESTAMP,
    review_notes VARCHAR
);
CREATE INDEX IF NOT EXISTS ix_exercise_requests_grant_id ON exercise_requests(grant_id);
CREATE INDEX IF NOT EXISTS ix_exercise_requests_employee_id ON exercise_requests(employee_id);
