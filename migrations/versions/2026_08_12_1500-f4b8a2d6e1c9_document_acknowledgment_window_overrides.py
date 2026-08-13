"""document acknowledgment window overrides

Revision ID: f4b8a2d6e1c9
Revises: 137b929afafc
Create Date: 2026-08-12 15:00:00.000000

v1.0.2 (debt item 2, HANDOFF.md): 137b929afafc נתן חלון אישור (acknowledgment_window_days)
פר-חברה יחיד. הטבלה הזו מוסיפה שכבה שנייה, עדינה יותר - פר-(company_id,
template_type). CREATE TABLE טרי בלבד, FK רגיל ל-companies - לא ALTER על
טבלה קיימת, ולכן אין כאן את בעיית ה-batch-recreate/PRAGMA foreign_keys שהניעה
את 137b929afafc/bd65db40f654 (אין כאן DROP של טבלה שמצביעים אליה, רק INSERT
לטבלה חדשה). שורה חסרה = אין override לסוג הזה - נופל ל-
companies.acknowledgment_window_days, ואם גם הוא NULL - לקבוע הגלובלי
(ACKNOWLEDGMENT_WINDOW_DAYS, document_status.py). CHECK דוחה 0/שלילי, אותו
לקח בדיוק כמו 137b929afafc.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4b8a2d6e1c9'
down_revision: Union[str, Sequence[str], None] = '137b929afafc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'document_acknowledgment_window_overrides',
        sa.Column('override_id', sa.String(), nullable=False),
        sa.Column('company_id', sa.String(), nullable=False),
        sa.Column('template_type', sa.String(), nullable=False),
        sa.Column('window_days', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['companies.company_id']),
        sa.PrimaryKeyConstraint('override_id'),
        sa.UniqueConstraint('company_id', 'template_type',
                            name='uq_doc_ack_window_override_company_type'),
        sa.CheckConstraint('window_days > 0',
                           name='ck_doc_ack_window_override_positive'),
    )
    with op.batch_alter_table('document_acknowledgment_window_overrides', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_document_acknowledgment_window_overrides_company_id'),
            ['company_id'], unique=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('document_acknowledgment_window_overrides', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_document_acknowledgment_window_overrides_company_id'))
    op.drop_table('document_acknowledgment_window_overrides')
