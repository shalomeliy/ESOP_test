"""Smoke: האפליקציה בכלל עולה, וה-VERSION שהיא מדווחת הוא זה שבקובץ VERSION.

הסיכון: קובץ VERSION אחד אמור להזין את ה-backend ואת שלושת הפורטלים (ראו
version.py). אם ה-endpoint מדווח גרסה אחרת מהקובץ, כל דיווח באג של משתמש
מצביע על גרסה שגויה.
"""

from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"


def test_root_returns_version_matching_the_version_file(client):
    response = client.get("/")

    assert response.status_code == 200
    body = response.json()
    assert "version" in body, f"אין שדה version בתשובה: {body}"
    assert body["version"] == VERSION_FILE.read_text(encoding="utf-8").strip()
