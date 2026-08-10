"""document acknowledgment deadline

Revision ID: c8d5e2f0a1b4
Revises: b7c4d1e9f2a3
Create Date: 2026-08-09 14:00:00.000000

נותן ל-`EXPIRED` משמעות. עד כאן הוא היה ערך במכונת המצבים שאף קוד לא ייצר,
כלומר מצב שקיים בסכמה ולא בעולם - `FEATURE_SPEC.md` שורה 157 אמר את זה במפורש.

ההכרעה (09/08/2026): בקשת אישור קבלה שנשלחה תקפה 30 יום מרגע השליחה. זה הנוהג
המקובל בבקשות אישור קבלה, והוא הדבר היחיד כאן שהוא מדיניות מוצר ולא חוק - אין
בסעיף 102 תקופת תוקף לבקשת אישור, ולכן גם אין כאן כלל מס שמומצא.

*** `expires_at` הוא חותמת זמן ולא תאריך, וזו הנקודה ***: `sent_at + 30 יום`
הוא רגע פיזי מדויק, ולכן ההשוואה `utcnow() > expires_at` נכונה בכל אזור זמן
ואינה נזקקת להכרעה בין UTC לשעון העסקי - בדיוק הבעיה שח1/ח2 היו. גבול שנמדד
בימים קלנדריים היה מחזיר את אותה שאלה דרך הדלת האחורית.

`expires_at` מצטרף לעמודות הקפואות של טריגר האישור: הוא חלק מתנאי מה שנשלח,
ולכן לא ניתן לשנותו בדיעבד על מסמך שכבר אושר. `is_latest` נשאר מחוץ לרשימה
מאותה סיבה בדיוק כמו ב-b7c4d1e9f2a3.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c8d5e2f0a1b4'
down_revision: Union[str, Sequence[str], None] = 'b7c4d1e9f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_FROZEN_COLUMNS_BEFORE = [
    "status", "acknowledged_at", "acknowledged_by_user_id",
    "template_type", "grant_id", "company_id", "employee_id", "trustee_id",
    "version", "file_path", "file_sha256", "generated_at", "sent_at",
]
_FROZEN_COLUMNS_AFTER = _FROZEN_COLUMNS_BEFORE + ["expires_at"]


def _freeze_trigger(columns: list[str]) -> str:
    # IS NOT ולא <> : ב-SQLite השוואה רגילה מול NULL מחזירה NULL ולא TRUE,
    # ולכן <> הייתה מפספסת בדיוק את המעבר NULL -> ערך.
    changed = " OR ".join(f"NEW.{col} IS NOT OLD.{col}" for col in columns)
    return f"""
        CREATE TRIGGER trg_documents_no_update_once_acknowledged
        BEFORE UPDATE ON documents
        WHEN OLD.status = 'ACKNOWLEDGED' AND ({changed})
        BEGIN SELECT RAISE(ABORT, 'documents: an ACKNOWLEDGED document is frozen; only is_latest may change'); END;
        """


def upgrade() -> None:
    """Upgrade schema."""
    # nullable ובלי server_default בכוונה: מסמכים שנשלחו לפני המיגרציה נשארים
    # בלי דדליין, ו-NULL נקרא בקוד כ"אין תוקף" ולא כ"פג". דדליין רטרואקטיבי
    # היה מפקיע בקשות פתוחות ברגע השדרוג, בלי שאיש הודיע לעובד.
    op.add_column('documents', sa.Column('expires_at', sa.DateTime(), nullable=True))
    op.execute("DROP TRIGGER IF EXISTS trg_documents_no_update_once_acknowledged")
    op.execute(_freeze_trigger(_FROZEN_COLUMNS_AFTER))


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER IF EXISTS trg_documents_no_update_once_acknowledged")
    op.execute(_freeze_trigger(_FROZEN_COLUMNS_BEFORE))
    op.drop_column('documents', 'expires_at')
