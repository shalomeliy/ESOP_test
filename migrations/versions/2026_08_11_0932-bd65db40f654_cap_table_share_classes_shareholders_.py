"""cap table share classes shareholders issuances

Revision ID: bd65db40f654
Revises: d9e4f1a2b3c6
Create Date: 2026-08-11 09:32:20.283108

v1.0.0 שלב א (טבלת הון - שלב הסכמה בלבד, ראו FEATURE_SPEC.md): שלוש טבלאות
חדשות + שתי עמודות nullable על טבלאות קיימות. אין UPDATE/מחיקה על שורות
קיימות בכל המיגרציה הזו - שינוי אדיטיבי טהור, כדרישת CLAUDE.md.

``share_classes``/``shareholders`` הן CREATE TABLE טרי בלי FK נכנס אליהן
מטבלה קיימת עדיין. ``companies.total_authorized_shares`` הוא ADD COLUMN
נאלבילי טהור, אותו דפוס בדיוק כמו national_id - SQLite מטפל בו בלי recreate.

**תוקן 11/08/2026, אחרי כשל בפועל מול esop_database.db החי:**
``option_pools.share_class_id`` **נראה** כמו ADD COLUMN נאלבילי תמים, אבל
ה-``create_foreign_key`` הנוסף באותו batch_alter_table מחייב SQLite לבצע
recreate מלא (טבלה חדשה + copy + DROP הישנה + rename) - וה-DROP נכשל
ב-FOREIGN KEY constraint failed כל עוד grants.pool_id מפנה אל שורות קיימות.
ההנחה המקורית כאן (‏"זה רק ADD COLUMN נאלבילי, כמו national_id"‏) פספסה שזו
לא הסיבה ל-recreate - ה-FK החדש הוא. תוקן ב-PRAGMA foreign_keys=OFF/ON סביב
הבלוק, אותו דפוס בדיוק שכבר קיים ב-56baedac6e53. **הבדיקה המקורית (שלב א)
לא תפסה את זה כי היא רצה מול סכימה ריקה** (סוויטת pytest ו-sandbox בלי
grants מזרוע) - הבאג הופיע רק מול DB עם שורות grants אמיתיות.

שים לב: esop_database.db החי נמצא בפועל שני revisions מאחורי ההד (עומד על
b7c4d1e9f2a3, לא d9e4f1a2b3c6) - drift קיים שהתגלה בזמן כתיבת המיגרציה הזו
ותועד ב-HANDOFF.md.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bd65db40f654'
down_revision: Union[str, Sequence[str], None] = 'd9e4f1a2b3c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'share_classes',
        sa.Column('share_class_id', sa.String(), nullable=False),
        sa.Column('company_id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('class_type', sa.String(), nullable=False),
        sa.Column('seniority_order', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['companies.company_id']),
        sa.PrimaryKeyConstraint('share_class_id'),
    )
    with op.batch_alter_table('share_classes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_share_classes_company_id'), ['company_id'], unique=False)

    op.create_table(
        'shareholders',
        sa.Column('shareholder_id', sa.String(), nullable=False),
        sa.Column('company_id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('shareholder_type', sa.String(), nullable=False),
        sa.Column('employee_id', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['companies.company_id']),
        sa.ForeignKeyConstraint(['employee_id'], ['employees.employee_id']),
        sa.PrimaryKeyConstraint('shareholder_id'),
    )
    with op.batch_alter_table('shareholders', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_shareholders_company_id'), ['company_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_shareholders_employee_id'), ['employee_id'], unique=False)

    op.create_table(
        'share_issuances',
        sa.Column('share_issuance_id', sa.String(), nullable=False),
        sa.Column('company_id', sa.String(), nullable=False),
        sa.Column('shareholder_id', sa.String(), nullable=False),
        sa.Column('share_class_id', sa.String(), nullable=False),
        sa.Column('shares', sa.Float(), nullable=False),
        sa.Column('issue_date', sa.Date(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['companies.company_id']),
        sa.ForeignKeyConstraint(['share_class_id'], ['share_classes.share_class_id']),
        sa.ForeignKeyConstraint(['shareholder_id'], ['shareholders.shareholder_id']),
        sa.PrimaryKeyConstraint('share_issuance_id'),
    )
    with op.batch_alter_table('share_issuances', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_share_issuances_company_id'), ['company_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_share_issuances_share_class_id'), ['share_class_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_share_issuances_shareholder_id'), ['shareholder_id'], unique=False)

    with op.batch_alter_table('companies', schema=None) as batch_op:
        batch_op.add_column(sa.Column('total_authorized_shares', sa.Float(), nullable=True))

    # create_foreign_key (לא רק add_column) מחייב SQLite לבצע recreate מלא
    # של option_pools (batch mode: טבלה חדשה + copy + DROP הישנה + rename) -
    # וה-DROP נכשל ב-FOREIGN KEY constraint failed כל עוד grants.pool_id
    # מפנה אל השורות הקיימות ו-PRAGMA foreign_keys=ON פעיל על כל connection
    # (database.py/env.py). אותו דפוס בדיוק שכבר נתפס ב-56baedac6e53/
    # employees.national_id - מכבים את האכיפה רק סביב הפעולה הזו.
    op.execute("PRAGMA foreign_keys=OFF")
    with op.batch_alter_table('option_pools', schema=None) as batch_op:
        batch_op.add_column(sa.Column('share_class_id', sa.String(), nullable=True))
        batch_op.create_index(batch_op.f('ix_option_pools_share_class_id'), ['share_class_id'], unique=False)
        batch_op.create_foreign_key(
            'fk_option_pools_share_class_id_share_classes', 'share_classes', ['share_class_id'], ['share_class_id']
        )
    op.execute("PRAGMA foreign_keys=ON")


def downgrade() -> None:
    """Downgrade schema."""
    # option_pools/companies יורדים ראשונים - אחרת ה-FK הנכנס מ-option_pools
    # אל share_classes חוסם את drop_table('share_classes') בהמשך. וכמו ב-
    # upgrade(): drop_constraint/drop_column על option_pools מחייב recreate
    # מלא, שנכשל ב-FOREIGN KEY constraint failed כל עוד grants.pool_id מפנה
    # אל השורות הקיימות - מכבים את האכיפה סביב הפעולה הזו גם כאן.
    op.execute("PRAGMA foreign_keys=OFF")
    with op.batch_alter_table('option_pools', schema=None) as batch_op:
        batch_op.drop_constraint('fk_option_pools_share_class_id_share_classes', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_option_pools_share_class_id'))
        batch_op.drop_column('share_class_id')
    op.execute("PRAGMA foreign_keys=ON")

    with op.batch_alter_table('companies', schema=None) as batch_op:
        batch_op.drop_column('total_authorized_shares')

    with op.batch_alter_table('share_issuances', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_share_issuances_shareholder_id'))
        batch_op.drop_index(batch_op.f('ix_share_issuances_share_class_id'))
        batch_op.drop_index(batch_op.f('ix_share_issuances_company_id'))

    op.drop_table('share_issuances')

    with op.batch_alter_table('shareholders', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_shareholders_employee_id'))
        batch_op.drop_index(batch_op.f('ix_shareholders_company_id'))

    op.drop_table('shareholders')

    with op.batch_alter_table('share_classes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_share_classes_company_id'))

    op.drop_table('share_classes')
