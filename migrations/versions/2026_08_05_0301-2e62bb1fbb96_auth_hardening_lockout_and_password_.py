"""auth hardening lockout and password reset

Revision ID: 2e62bb1fbb96
Revises: 11434b49810c
Create Date: 2026-08-05 03:01:50.297990

שלוש עמודות חדשות ב-users עבור v0.5.1 (patch אבטחה): must_change_password,
failed_login_attempts, locked_until - ראו auth.py.

*** תוקן ידנית פעמיים אחרי autogenerate, שני כשלים אמיתיים שנבדקו מול עותק
של esop_database.db (55 משתמשים) ולא רק מול DB ריק: ***

1. server_default נדרש על שתי העמודות NOT NULL - בלעדיו
   ``ALTER TABLE users ADD COLUMN ... NOT NULL`` נכשל מיידית
   (``Cannot add a NOT NULL column with default value NULL``). ה-``default=``
   ב-models.py הוא client-side בלבד (חל רק על INSERT חדש) ולא עוזר כאן.
   server_default='0' משאיר את כל המשתמשים הקיימים לא-נעולים וללא חובת החלפת
   סיסמה - החובה חלה רק על פרובייז חדש (routes.create_employee).

2. ``op.batch_alter_table`` (ברירת המחדל של autogenerate, וגם render_as_batch=True
   הגלובלי ב-env.py) בונה מחדש את כל טבלת users מאפס - ועם PRAGMA foreign_keys=ON
   זה מפיל ``DROP TABLE users`` על FOREIGN KEY constraint מכל טבלה שמצביעה אליה
   (user_sessions, notification_preferences/dismissals, audit_log, exercise_requests).
   הפתרון: ``op.add_column`` ישיר, *לא* עטוף ב-batch_alter_table. SQLite תומך
   ב-ADD COLUMN עם ברירת מחדל קבועה כפעולה אמיתית, בלי לשחזר את הטבלה -
   render_as_batch משפיע רק על מה ש-autogenerate *מייצר*, לא על op.add_column
   שנכתב ישירות. (DROP COLUMN/ALTER TYPE עדיין כן דורשים batch - זה לא רלוונטי כאן.)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2e62bb1fbb96'
down_revision: Union[str, Sequence[str], None] = '11434b49810c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('must_change_password', sa.Boolean(),
                                     nullable=False, server_default=sa.false()))
    op.add_column('users', sa.Column('failed_login_attempts', sa.Integer(),
                                     nullable=False, server_default='0'))
    op.add_column('users', sa.Column('locked_until', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # DROP COLUMN כן דורש batch mode אמיתי (SQLite לא תומך בזה ישירות) - בשונה
    # מ-upgrade(), כאן batch_alter_table הוא הכרחי. וה-recreate שה-batch עושה
    # מפיל DROP TABLE users על FOREIGN KEY מכל טבלה שמצביעה אליה (בדיוק כמו
    # שנבדק ב-upgrade לפני התיקון) - לכן מכבים את אכיפת ה-FK סביב הבלוק בלבד.
    op.execute("PRAGMA foreign_keys=OFF")
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('locked_until')
        batch_op.drop_column('failed_login_attempts')
        batch_op.drop_column('must_change_password')
    op.execute("PRAGMA foreign_keys=ON")
