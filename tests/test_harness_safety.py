"""בדיקות על *תשתית הבדיקות עצמה*.

רשת ביטחון שלא נבדקת היא לא רשת ביטחון. הכלל בפרויקט הוא שאסור לבדיקות לגעת
ב-esop_database.db (הוא מחזיק נתוני עבודה אמיתיים), והמנגנון שמונע את זה יושב
ב-conftest. הקובץ הזה מאמת שהמנגנון באמת עובד ובאמת עבד בריצה הנוכחית.
"""

import pytest

from backend.app.database import engine
from tests.conftest import (PRODUCTION_DB_MTIME_AT_IMPORT, PRODUCTION_DB_PATH,
                            PROJECT_ROOT, TEST_DB_PATH, TEST_DB_URL,
                            _assert_not_production)


def test_guard_rejects_the_production_database_url():
    """הוכחה שהשומר לא אינרטי: מול ה-URL האמיתי הוא זורק."""
    with pytest.raises(RuntimeError):
        _assert_not_production("sqlite:///./esop_database.db")


def test_guard_accepts_the_scratch_url():
    """בקרה חיובית - אחרת הבדיקה שמעל יכולה לעבור גם אם השומר זורק על הכל."""
    _assert_not_production(TEST_DB_URL)


def test_engine_points_at_a_scratch_file_outside_the_repo():
    assert str(engine.url) == TEST_DB_URL
    assert "esop_database.db" not in str(engine.url)
    # קובץ ה-scratch נוצר מחוץ לעץ הפרויקט, כך שאפילו קובץ שנשכח מאחור
    # לא יתערבב עם ה-DB האמיתי ולא ייכנס ל-git.
    assert PROJECT_ROOT not in TEST_DB_PATH.resolve().parents


@pytest.mark.skipif(PRODUCTION_DB_MTIME_AT_IMPORT is None,
                    reason="esop_database.db לא קיים בסביבה הזו - אין מה להשוות")
def test_production_database_was_not_modified_by_this_run():
    """ה-mtime של ה-DB החי כפי שנמדד בטעינת ה-conftest, מול עכשיו.

    מגבלה מודעת: הבדיקה רצה בנקודה כלשהי באמצע הריצה, ולא בסופה, ולכן היא
    לא מכסה בדיקות שירוצו אחריה. היא נועדה לתפוס דליפה שכבר קרתה (בעיקר
    ב-import/collection ובבדיקות שרצו לפניה); האימות המלא של הריצה כולה נעשה
    בהשוואת sha256 לפני ואחרי, בדוח האימות של הגרסה.
    """
    assert PRODUCTION_DB_PATH.stat().st_mtime == PRODUCTION_DB_MTIME_AT_IMPORT, (
        "esop_database.db השתנה במהלך ריצת הבדיקות - עצור והבן למה לפני שממשיכים"
    )
