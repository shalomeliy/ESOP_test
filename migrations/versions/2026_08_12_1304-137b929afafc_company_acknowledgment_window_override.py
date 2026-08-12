"""company acknowledgment window override

Revision ID: 137b929afafc
Revises: bd65db40f654
Create Date: 2026-08-12 13:04:02.145731

v1.0.1 (debt item 4, HANDOFF.md): עד כאן חלון האישור (ACKNOWLEDGMENT_WINDOW_DAYS)
היה קבוע גלובלי יחיד - כל חברה קיבלה בדיוק 30 יום. עמודה nullable אחת, אותו
דפוס בדיוק כמו companies.total_authorized_shares (bd65db40f654): None אומר
"אין override, השתמש בקבוע" ולא "0 יום". אין backfill - שורות קיימות מקבלות
NULL, לא 30 מפורש, כדי שההבחנה בין "לא הוגדר" ל"הוגדר במפורש לערך המחדל"
תישאר אמיתית.

CHECK דוחה 0/שלילי: "פוקע מיד" אינו צורך שהוצהר, וטעות הקלדה כאן (החסרת ה-30)
הייתה מפילה כל מסמך חדש לפג-תוקף באותו רגע שהוא נשלח.

*** לקח מ-bd65db40f654, לא לפתוח מחדש: הוספת CHECK/FK על טבלה קיימת מחייבת
SQLite לבצע recreate מלא (batch mode: טבלה חדשה + copy + DROP הישנה + rename),
ו-companies מוצבעת ע"י FK NOT NULL מכמעט כל טבלה בסכימה (employees/option_pools/
documents/share_classes/shareholders/share_issuances/...) - בדיוק כמו שה-DROP
של option_pools נכשל כל עוד grants.pool_id הפנה אליו. PRAGMA foreign_keys=OFF/ON
סביב הבלוק, אותו דפוס בדיוק. ***
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '137b929afafc'
down_revision: Union[str, Sequence[str], None] = 'bd65db40f654'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("PRAGMA foreign_keys=OFF")
    with op.batch_alter_table('companies', schema=None) as batch_op:
        batch_op.add_column(sa.Column('acknowledgment_window_days', sa.Integer(), nullable=True))
        batch_op.create_check_constraint(
            'ck_companies_acknowledgment_window_days_positive',
            'acknowledgment_window_days IS NULL OR acknowledgment_window_days > 0',
        )
    op.execute("PRAGMA foreign_keys=ON")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("PRAGMA foreign_keys=OFF")
    with op.batch_alter_table('companies', schema=None) as batch_op:
        batch_op.drop_constraint('ck_companies_acknowledgment_window_days_positive', type_='check')
        batch_op.drop_column('acknowledgment_window_days')
    op.execute("PRAGMA foreign_keys=ON")
