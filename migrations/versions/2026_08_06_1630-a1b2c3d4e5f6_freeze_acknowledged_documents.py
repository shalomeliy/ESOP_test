"""freeze acknowledged documents

Revision ID: a1b2c3d4e5f6
Revises: 56baedac6e53
Create Date: 2026-08-06 16:30:00.000000

הגנת שינוי ברמת ה-DB על מסמך שאושר (v0.9.0 שלב 2, סוגר את R-070/סיכון 6 של
שלב 1). אותו דפוס בדיוק כמו trg_ledger_events_no_update/no_delete מהמיגרציה
של v0.6.0: RAISE(ABORT) בטריגר, לא בדיקה בקוד אפליקציה - כי קוד אפשר לעקוף
(GOAL.md חוק ברזל 4: invariants נאכפים בשכבת הנתונים).

בשונה מ-ledger_events (שכולה append-only), כאן הקיפאון *מותנה במצב*: טיוטה
ומסמך שנשלח עדיין חייבים להיות ניתנים לעדכון (זה בדיוק המעבר SENT->ACKNOWLEDGED),
ורק אחרי ACKNOWLEDGED השורה נעולה. לכן WHEN OLD.status = 'ACKNOWLEDGED'.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '56baedac6e53'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TRIGGER trg_documents_no_update_once_acknowledged
        BEFORE UPDATE ON documents
        WHEN OLD.status = 'ACKNOWLEDGED'
        BEGIN SELECT RAISE(ABORT, 'documents: an ACKNOWLEDGED document is frozen; UPDATE is rejected'); END;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_documents_no_delete_once_acknowledged
        BEFORE DELETE ON documents
        WHEN OLD.status = 'ACKNOWLEDGED'
        BEGIN SELECT RAISE(ABORT, 'documents: an ACKNOWLEDGED document is frozen; DELETE is rejected'); END;
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER IF EXISTS trg_documents_no_delete_once_acknowledged")
    op.execute("DROP TRIGGER IF EXISTS trg_documents_no_update_once_acknowledged")
