"""allow superseding an acknowledged document

Revision ID: b7c4d1e9f2a3
Revises: a1b2c3d4e5f6
Create Date: 2026-08-09 09:00:00.000000

מצמצם את טריגר ההקפאה של `a1b2c3d4e5f6` כך שיחסום את *תוכן* האישור בלבד,
ויאפשר לסמן מסמך שאושר כגרסה מיושנת.

*** הבאג שזה מתקן ***: הטריגר המקורי חסם כל UPDATE על שורה ACKNOWLEDGED, בלי
אבחנה בין עמודות. אבל `generate_document` מסמן את הגרסה הקודמת ב-`is_latest=0`
כשנוצרת גרסה חדשה - ולכן יצירת מסמך חדש למענק שכבר יש לו מסמך מאושר מאותו סוג
נפלה ב-IntegrityError, כלומר **500 למשתמש**. ההכרעה: הנייר הישן תמיד נשמר, ותמיד
אפשר להוציא נייר חדש מעודכן ולאשר אותו.

למה זה לא מחליש את ההגנה: `is_latest` אינו חלק ממה שהעובד אישר. הוא אומר "קיימת
גרסה חדשה יותר", לא "מה שאישרת השתנה". כל מה שמהווה את רשומת האישור עצמה -
הסטטוס, מי אישר ומתי, ואיזה קובץ בדיוק אושר (`file_path`/`file_sha256`) וכן
זהות המסמך (`template_type`/`grant_id`/`version`) - נשאר קפוא בדיוק כמו קודם.
נסיון לשנות אחד מהם עדיין נדחה, ו-DELETE נדחה במלואו ללא שינוי.

למה זה לא נתפס קודם: הטריגרים לא היו קיימים באף סביבה שבודקים בה - `seed_data`
בנתה סכימה ב-create_all (בלי DDL של מיגרציות) וגם conftest עושה זאת, ו-DB החי
מעולם לא הועלה ל-head. ברגע שהזריעה תוקנה להריץ `upgrade head`, ה-500 הופיע
מיד. ראו tests/test_document_triggers.py, שרץ מול סכימה ממוגרצת בכוונה.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b7c4d1e9f2a3'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# העמודות שמהוות את רשומת האישור. `is_latest` בכוונה *אינו* ברשימה - זה כל
# התיקון. `sent_at`/`generated_at` נכללים כי הם חלק מהתיעוד של מה שאושר ומתי.
_FROZEN_COLUMNS = [
    "status", "acknowledged_at", "acknowledged_by_user_id",
    "template_type", "grant_id", "company_id", "employee_id", "trustee_id",
    "version", "file_path", "file_sha256", "generated_at", "sent_at",
]

_CHANGED = " OR ".join(
    # IS NOT ולא <> : ב-SQLite השוואה רגילה מול NULL מחזירה NULL (לא TRUE),
    # כך ש-<> הייתה מפספסת בדיוק את המעבר NULL -> ערך, שהוא המקרה המעניין.
    f"NEW.{col} IS NOT OLD.{col}" for col in _FROZEN_COLUMNS
)


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("DROP TRIGGER IF EXISTS trg_documents_no_update_once_acknowledged")
    op.execute(
        f"""
        CREATE TRIGGER trg_documents_no_update_once_acknowledged
        BEFORE UPDATE ON documents
        WHEN OLD.status = 'ACKNOWLEDGED' AND ({_CHANGED})
        BEGIN SELECT RAISE(ABORT, 'documents: an ACKNOWLEDGED document is frozen; only is_latest may change'); END;
        """
    )
    # טריגר ה-DELETE נשאר כפי שהוא - מסמך שאושר לא נמחק, נקודה.


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER IF EXISTS trg_documents_no_update_once_acknowledged")
    op.execute(
        """
        CREATE TRIGGER trg_documents_no_update_once_acknowledged
        BEFORE UPDATE ON documents
        WHEN OLD.status = 'ACKNOWLEDGED'
        BEGIN SELECT RAISE(ABORT, 'documents: an ACKNOWLEDGED document is frozen; UPDATE is rejected'); END;
        """
    )
