-- SQL Schema Reference - 21 טבלאות, משקף את backend/app/models.py בגרסה 0.9.1.
--
-- זהו קובץ תיעוד/reference בלבד. ה-DB בפועל נבנה על ידי Alembic
-- (python -m alembic upgrade head) מאז גרסה 0.4.0 - לא על ידי הקובץ הזה
-- ולא יותר על ידי Base.metadata.create_all(). כל שינוי סכמה חייב מיגרציה;
-- הקובץ הזה מסונכרן ידנית אחריה.
--
-- *** הקובץ הזה שיקר פעמיים ***
-- (1) הוא הכריז על CHECK constraints לערכי enum
-- (status/role/grant_type) שמעולם לא היו קיימים ב-DB האמיתי, ופספס שתי טבלאות
-- ועמודה. הגרסה הזו נגזרה מ-sqlite_master של DB שנבנה ב-alembic upgrade head,
-- ולכן היא תואמת בדיוק. אל תוסיפו כאן אילוץ שלא קיים במודל - אילוץ מתועד שלא
-- נאכף מסוכן יותר מהיעדר תיעוד.
-- (2) ב-09/08/2026 התגלה שהוא עצר בגרסה 0.5.0 וחסרו בו *ארבע* טבלאות שלמות -
--     tax_rule_packs (v0.7.0), ledger_events + ledger_ownership (v0.6.0),
--     documents (v0.9.0) - וכל ארבעת הטריגרים. CLAUDE.md שורה 17 מפנה לכאן
--     כמקור לאימות לוגיקת דומיין, כך שקובץ מיושן כאן אינו ליקוי תיעוד אלא
--     מקור מטעה. מאז נאכף ב-tests/test_project_invariants.py ולא בזיכרון.
-- (3) ותיקון (2) עצמו היה חלקי: רק *הטבלאות החדשות* נוספו, והטבלאות שקדמו
--     ל-0.5.0 לא נבנו מחדש - כך שנשארו חסרות שש עמודות, שני מפתחות זרים
--     ושלושה אילוצי UNIQUE. הבדיקה שנכתבה כדי למנוע את זה השוותה שמות טבלאות
--     בלבד ולכן אישרה את הדריפט. מאז ההשוואה היא עמודה-עמודה, כולל טיפוס,
--     NOT NULL, מפתחות זרים ואילוצי UNIQUE.
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
    birth_date DATE,
    -- ת.ז./SSN. nullable בכוונה: נוסף ב-v0.9.0 על עובדים קיימים, ואין דרך
    -- להשלים אותו רטרואקטיבית. משמש למסמכי 102 שדורשים זיהוי הנישום.
    national_id VARCHAR
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
    official_source_url VARCHAR NOT NULL,
    -- היחס ל-tax_rule_packs הוא 1:1, ולכן UNIQUE ולא רק FK: בלי האילוץ שתי
    -- שורות עם אותו pack_id היו מתחרות על ה-.first() ב-_calculate_flat בלי
    -- סדר מובטח. שימו לב שב-income_tax_brackets זה בכוונה *לא* ייחודי -
    -- שם כמה מדרגות חולקות pack_id אחד.
    pack_id VARCHAR REFERENCES tax_rule_packs(pack_id),
    CONSTRAINT uq_tax_rates_history_country_type_date
        UNIQUE (country_code, grant_type, effective_start_date),
    CONSTRAINT uq_tax_rates_history_pack_id UNIQUE (pack_id)
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
    official_source_url VARCHAR NOT NULL,
    pack_id VARCHAR REFERENCES tax_rule_packs(pack_id),
    -- הייחוד כולל את bracket_order ולכן אינו חוסם כמה מדרגות באותה גרסה;
    -- הוא חוסם רק "אותה מדרגה פעמיים באותה גרסה".
    CONSTRAINT uq_income_tax_brackets_country_type_date_order
        UNIQUE (country_code, grant_type, effective_start_date, bracket_order)
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
    created_at DATETIME,
    -- שלוש עמודות נעילת החשבון (v0.5.0). ה-DEFAULT כאן הוא היחיד בקובץ שמגיע
    -- מה-DB ולא מ-Python: המיגרציה חייבת server_default כדי להוסיף עמודת
    -- NOT NULL על טבלה מאוכלסת, וברירת המחדל משאירה כל משתמש קיים לא-נעול
    -- ובלי חובת החלפת סיסמה - נעילה גורפת של כל המשתמשים היא מה שהיה קורה אחרת.
    must_change_password BOOLEAN NOT NULL DEFAULT 0,
    failed_login_attempts INTEGER NOT NULL DEFAULT '0',
    locked_until DATETIME
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


-- ===================================================================
-- v0.6.0 - v0.9.0: הטבלאות שנוספו אחרי 0.5.0
-- ===================================================================

CREATE TABLE IF NOT EXISTS tax_rule_packs (
	pack_id VARCHAR NOT NULL, 
	country_code VARCHAR NOT NULL, 
	grant_type VARCHAR NOT NULL, 
	effective_start_date DATE NOT NULL, 
	calculation_method VARCHAR NOT NULL, 
	official_source_url VARCHAR NOT NULL, 
	created_at DATETIME, 
	PRIMARY KEY (pack_id), 
	CONSTRAINT uq_tax_rule_packs_country_type_date UNIQUE (country_code, grant_type, effective_start_date)
);

CREATE INDEX IF NOT EXISTS ix_tax_rule_packs_country_code ON tax_rule_packs (country_code);
CREATE INDEX IF NOT EXISTS ix_tax_rule_packs_grant_type ON tax_rule_packs (grant_type);

CREATE TABLE IF NOT EXISTS ledger_events (
	event_id VARCHAR NOT NULL, 
	event_type VARCHAR NOT NULL, 
	aggregate_type VARCHAR NOT NULL, 
	aggregate_id VARCHAR NOT NULL, 
	payload VARCHAR NOT NULL, 
	effective_date DATE NOT NULL, 
	recorded_at DATETIME NOT NULL, 
	actor_user_id VARCHAR, 
	sequence_no INTEGER NOT NULL, 
	corrects_event_id VARCHAR, 
	schema_version INTEGER NOT NULL, 
	source VARCHAR NOT NULL, 
	PRIMARY KEY (event_id), 
	FOREIGN KEY(actor_user_id) REFERENCES users (user_id), 
	FOREIGN KEY(corrects_event_id) REFERENCES ledger_events (event_id), 
	CONSTRAINT uq_ledger_events_aggregate_seq UNIQUE (aggregate_id, sequence_no)
);

CREATE INDEX IF NOT EXISTS ix_ledger_events_aggregate ON ledger_events (aggregate_type, aggregate_id);
CREATE INDEX IF NOT EXISTS ix_ledger_events_effective_date ON ledger_events (effective_date);
CREATE INDEX IF NOT EXISTS ix_ledger_events_event_type ON ledger_events (event_type);
CREATE INDEX IF NOT EXISTS ix_ledger_events_recorded_at ON ledger_events (recorded_at);

CREATE TABLE IF NOT EXISTS ledger_ownership (
	aggregate_id VARCHAR NOT NULL, 
	aggregate_type VARCHAR NOT NULL, 
	company_id VARCHAR, 
	trustee_id VARCHAR, 
	employee_id VARCHAR, 
	PRIMARY KEY (aggregate_id), 
	FOREIGN KEY(company_id) REFERENCES companies (company_id), 
	FOREIGN KEY(employee_id) REFERENCES employees (employee_id), 
	FOREIGN KEY(trustee_id) REFERENCES trustees (trustee_id)
);

CREATE INDEX IF NOT EXISTS ix_ledger_ownership_company_id ON ledger_ownership (company_id);
CREATE INDEX IF NOT EXISTS ix_ledger_ownership_employee_id ON ledger_ownership (employee_id);
CREATE INDEX IF NOT EXISTS ix_ledger_ownership_trustee_id ON ledger_ownership (trustee_id);

CREATE TABLE IF NOT EXISTS documents (
	document_id VARCHAR NOT NULL, 
	template_type VARCHAR NOT NULL, 
	grant_id VARCHAR NOT NULL, 
	company_id VARCHAR NOT NULL, 
	employee_id VARCHAR NOT NULL, 
	trustee_id VARCHAR, 
	status VARCHAR(12) NOT NULL, 
	version INTEGER NOT NULL, 
	is_latest BOOLEAN NOT NULL, 
	file_path VARCHAR NOT NULL, 
	file_sha256 VARCHAR NOT NULL, 
	generated_at DATETIME NOT NULL, 
	sent_at DATETIME,
	-- מועד פקיעת בקשת האישור (sent_at + 30 יום), נקבע בשליחה. NULL = אין
	-- דדליין, ולא "פג" - כך מסמכים שנשלחו לפני v0.9.1 נשארים פתוחים.
	expires_at DATETIME,
	acknowledged_at DATETIME,
	acknowledged_by_user_id VARCHAR, 
	created_by_user_id VARCHAR, 
	PRIMARY KEY (document_id), 
	FOREIGN KEY(acknowledged_by_user_id) REFERENCES users (user_id), 
	FOREIGN KEY(company_id) REFERENCES companies (company_id), 
	FOREIGN KEY(created_by_user_id) REFERENCES users (user_id), 
	FOREIGN KEY(employee_id) REFERENCES employees (employee_id), 
	FOREIGN KEY(grant_id) REFERENCES grants (grant_id), 
	FOREIGN KEY(trustee_id) REFERENCES trustees (trustee_id)
);

CREATE INDEX IF NOT EXISTS ix_documents_company_id ON documents (company_id);
CREATE INDEX IF NOT EXISTS ix_documents_employee_id ON documents (employee_id);
CREATE INDEX IF NOT EXISTS ix_documents_grant_id ON documents (grant_id);
CREATE INDEX IF NOT EXISTS ix_documents_trustee_id ON documents (trustee_id);


-- ===================================================================
-- ייצוא / ייבוא וניידות נתונים (v0.9.1 שלב ב)
-- ===================================================================

CREATE TABLE IF NOT EXISTS exercise_tax_records (
	record_id VARCHAR NOT NULL,
	request_id VARCHAR NOT NULL,
	country_code VARCHAR NOT NULL,
	grant_type VARCHAR NOT NULL,
	effective_start_date DATE NOT NULL,
	calculation_method VARCHAR NOT NULL,
	gain FLOAT NOT NULL,
	tax_amount FLOAT NOT NULL,
	effective_rate FLOAT NOT NULL,
	official_source_url VARCHAR NOT NULL,
	computed_at DATETIME NOT NULL,
	PRIMARY KEY (record_id),
	FOREIGN KEY(request_id) REFERENCES exercise_requests (request_id),
	CONSTRAINT uq_exercise_tax_records_request_id UNIQUE (request_id)
);

CREATE TABLE IF NOT EXISTS data_transfer_runs (
	run_id VARCHAR NOT NULL,
	direction VARCHAR(14) NOT NULL,
	source_company_id VARCHAR,
	target_company_id VARCHAR,
	initiated_by_user_id VARCHAR NOT NULL,
	export_schema_version INTEGER NOT NULL,
	based_on_run_id VARCHAR,
	rows_attempted INTEGER NOT NULL,
	rows_succeeded INTEGER NOT NULL,
	rows_failed INTEGER NOT NULL,
	status VARCHAR(9) NOT NULL,
	file_path VARCHAR,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (run_id),
	FOREIGN KEY(source_company_id) REFERENCES companies (company_id),
	FOREIGN KEY(target_company_id) REFERENCES companies (company_id),
	FOREIGN KEY(initiated_by_user_id) REFERENCES users (user_id),
	FOREIGN KEY(based_on_run_id) REFERENCES data_transfer_runs (run_id)
);

CREATE INDEX IF NOT EXISTS ix_data_transfer_runs_source_company_id ON data_transfer_runs (source_company_id);
CREATE INDEX IF NOT EXISTS ix_data_transfer_runs_target_company_id ON data_transfer_runs (target_company_id);


-- ===================================================================
-- טריגרים - כאן נאכפים האינווריאנטים שאין להם ביטוי בעמודה
-- ===================================================================
-- שני זוגות: יומן האירועים append-only (אין UPDATE ואין DELETE, לעולם), ומסמך
-- שאושר בקבלה מוקפא בתוכנו. הטריגרים קיימים *רק במיגרציות* - Base.metadata.
-- create_all() אינו מייצר אותם, ולכן סוויטת בדיקות שנבנית ב-create_all רצה בלי
-- אף אחד מהם. זה בדיוק מה שהסתיר באג 500 שלם עד v0.9.1; tests/conftest.py עבר
-- מאז ל-alembic upgrade head ומוודא שנוצר לפחות טריגר אחד.

CREATE TRIGGER trg_documents_no_delete_once_acknowledged
        BEFORE DELETE ON documents
        WHEN OLD.status = 'ACKNOWLEDGED'
        BEGIN SELECT RAISE(ABORT, 'documents: an ACKNOWLEDGED document is frozen; DELETE is rejected'); END;

CREATE TRIGGER trg_documents_no_update_once_acknowledged
        BEFORE UPDATE ON documents
        WHEN OLD.status = 'ACKNOWLEDGED' AND (NEW.status IS NOT OLD.status OR NEW.acknowledged_at IS NOT OLD.acknowledged_at OR NEW.acknowledged_by_user_id IS NOT OLD.acknowledged_by_user_id OR NEW.template_type IS NOT OLD.template_type OR NEW.grant_id IS NOT OLD.grant_id OR NEW.company_id IS NOT OLD.company_id OR NEW.employee_id IS NOT OLD.employee_id OR NEW.trustee_id IS NOT OLD.trustee_id OR NEW.version IS NOT OLD.version OR NEW.file_path IS NOT OLD.file_path OR NEW.file_sha256 IS NOT OLD.file_sha256 OR NEW.generated_at IS NOT OLD.generated_at OR NEW.sent_at IS NOT OLD.sent_at)
        BEGIN SELECT RAISE(ABORT, 'documents: an ACKNOWLEDGED document is frozen; only is_latest may change'); END;

CREATE TRIGGER trg_ledger_events_no_delete BEFORE DELETE ON ledger_events
        BEGIN SELECT RAISE(ABORT, 'ledger_events is append-only: DELETE is rejected'); END;

CREATE TRIGGER trg_ledger_events_no_update BEFORE UPDATE ON ledger_events
        BEGIN SELECT RAISE(ABORT, 'ledger_events is append-only: UPDATE is rejected'); END;
