"""saved reports

Revision ID: 23416d683773
Revises: f4b8a2d6e1c9
Create Date: 2026-08-13 10:00:00.000000

v1.1.0 ("דוחות, ייצוא ו-BI"): הטבלה החדשה היחידה בגרסה הזו - שומרת
קונפיגורציית דוח (סוג+פילטרים) שמשתמש admin ביקש לשמור, לא את תוצאת הדוח
עצמה (זו ממשיכה להיחשב בזמן קריאה, בדיוק כמו compute_cap_table_snapshot).
CREATE TABLE טרי בלבד, שני FK רגילים (companies/users) - לא ALTER על טבלה
קיימת, ולכן אין כאן את בעיית ה-batch-recreate/PRAGMA foreign_keys שהניעה
את bd65db40f654/137b929afafc (אין DROP של טבלה שמצביעים אליה).

saved_reports נרשמה ב-company_scope.SPECIAL_CASED_TABLES (לא TABLE_REGISTRY) -
ראו הנימוק המלא ב-backend/app/services/company_scope.py וב-models.py::
SavedReport docstring: קרובה מבחינה מושגית ל"נוחות עבודה אישית" (saved
filter) ולא לדאטה עסקי ליבתי, ותלויה ב-owner_user_id שמצביע על users -
טבלה שכבר לעולם לא מיוצאת/מיובאת.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '23416d683773'
down_revision: Union[str, Sequence[str], None] = 'f4b8a2d6e1c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'saved_reports',
        sa.Column('report_id', sa.String(), nullable=False),
        sa.Column('company_id', sa.String(), nullable=False),
        sa.Column('owner_user_id', sa.String(), nullable=False),
        sa.Column('is_private', sa.Boolean(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('report_type', sa.String(), nullable=False),
        sa.Column('filter_params', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['companies.company_id']),
        sa.ForeignKeyConstraint(['owner_user_id'], ['users.user_id']),
        sa.PrimaryKeyConstraint('report_id'),
    )
    with op.batch_alter_table('saved_reports', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_saved_reports_company_id'),
            ['company_id'], unique=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('saved_reports', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_saved_reports_company_id'))
    op.drop_table('saved_reports')
