"""exercise tax records and data transfer runs

Revision ID: d9e4f1a2b3c6
Revises: c8d5e2f0a1b4
Create Date: 2026-08-10 10:00:00.000000

v0.9.1 שלב ב (ייצוא/ייבוא) - שני יסודות סכמה בלבד, שני PLAN.md::§8 step 1.
שתיהן CREATE TABLE טרי - אין ALTER על טבלה קיימת, ולכן אין כאן את בעיית
ה-NOT NULL/server_default שנתקלנו בה ב-v0.5.1 (ראו 2e62bb1fbb96).

``exercise_tax_records`` סוגר פער שהתגלה בתכנון שלב ב ולא היה ידוע קודם:
_decide_exercise_request (exercise_requests.py) מעולם לא קרא ל-TaxCalculationEngine
בנתיב האישור האמיתי - רק /simulate-exercise עשה זאת, ותוצאתו נכתבת כ-JSON חופשי
בתוך AuditLog.after_value ולא כרשומה מבנית. בלי הטבלה הזו, דוח ההתאמה (v0.9.1
שלב ב) לא יכול לשחזר חישוב מס על מימוש אמיתי - רק על סימולציה. שדה ``gain``
נשמר בנוסף ל-``tax_amount`` בכוונה: בלעדיו הדוח משווה מספר לעצמו ולא משחזר
את החישוב. המפתח הטבעי (country_code, grant_type, effective_start_date) ולא
pack_id: pack_id מתחדש בכל seed/backfill ולא שורד בין שני מופעי DB.

``data_transfer_runs`` מזין את מסך היסטוריית ייצוא/ייבוא ואת שני שלבי
דריי-ראן -> commit (ראו based_on_run_id, FK עצמי באותו דפוס כמו
LedgerEvent.corrects_event_id).

שני היסודות נכתבים באותה מיגרציה כי שניהם תשתית טהורה בלי חיווט לקוד עדיין -
_decide_exercise_request מתחבר ל-exercise_tax_records בשלב הבא (§8 step 2),
לא כאן.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd9e4f1a2b3c6'
down_revision: Union[str, Sequence[str], None] = 'c8d5e2f0a1b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'exercise_tax_records',
        sa.Column('record_id', sa.String(), nullable=False),
        sa.Column('request_id', sa.String(), nullable=False),
        sa.Column('country_code', sa.String(), nullable=False),
        sa.Column('grant_type', sa.String(), nullable=False),
        sa.Column('effective_start_date', sa.Date(), nullable=False),
        sa.Column('calculation_method', sa.String(), nullable=False),
        sa.Column('gain', sa.Float(), nullable=False),
        sa.Column('tax_amount', sa.Float(), nullable=False),
        sa.Column('effective_rate', sa.Float(), nullable=False),
        sa.Column('official_source_url', sa.String(), nullable=False),
        sa.Column('computed_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['request_id'], ['exercise_requests.request_id']),
        sa.PrimaryKeyConstraint('record_id'),
        sa.UniqueConstraint('request_id', name='uq_exercise_tax_records_request_id'),
    )

    op.create_table(
        'data_transfer_runs',
        sa.Column('run_id', sa.String(), nullable=False),
        sa.Column('direction', sa.Enum('EXPORT', 'IMPORT_DRY_RUN', 'IMPORT_COMMIT', name='datatransferdirection'), nullable=False),
        sa.Column('source_company_id', sa.String(), nullable=True),
        sa.Column('target_company_id', sa.String(), nullable=True),
        sa.Column('initiated_by_user_id', sa.String(), nullable=False),
        sa.Column('export_schema_version', sa.Integer(), nullable=False),
        sa.Column('based_on_run_id', sa.String(), nullable=True),
        sa.Column('rows_attempted', sa.Integer(), nullable=False),
        sa.Column('rows_succeeded', sa.Integer(), nullable=False),
        sa.Column('rows_failed', sa.Integer(), nullable=False),
        sa.Column('status', sa.Enum('PENDING', 'SUCCESS', 'FAILED', 'COMMITTED', name='datatransferstatus'), nullable=False),
        sa.Column('file_path', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['source_company_id'], ['companies.company_id']),
        sa.ForeignKeyConstraint(['target_company_id'], ['companies.company_id']),
        sa.ForeignKeyConstraint(['initiated_by_user_id'], ['users.user_id']),
        sa.ForeignKeyConstraint(['based_on_run_id'], ['data_transfer_runs.run_id']),
        sa.PrimaryKeyConstraint('run_id'),
    )
    with op.batch_alter_table('data_transfer_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_data_transfer_runs_source_company_id'), ['source_company_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_data_transfer_runs_target_company_id'), ['target_company_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('data_transfer_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_data_transfer_runs_target_company_id'))
        batch_op.drop_index(batch_op.f('ix_data_transfer_runs_source_company_id'))
    op.drop_table('data_transfer_runs')
    op.drop_table('exercise_tax_records')
