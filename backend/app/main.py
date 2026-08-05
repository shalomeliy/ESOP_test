import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from backend.app.api.routes import router
from backend.app.version import VERSION, get_version

# רשימת מקורות מפורשת במקום "*". ברירת המחדל מכסה שני דפוסי השימוש בפועל:
# הפורטלים מוגשים מהשרת עצמו (127.0.0.1:8000/localhost:8000), וגם פתיחת קובץ
# ה-HTML ישירות מהדיסק (Origin: "null" - כך דפדפנים שולחים אותו ל-file://).
# ESOP_CORS_ALLOWED_ORIGINS (רשימה מופרדת בפסיקים) דורס את ברירת המחדל לסביבות
# אחרות (למשל דומיין פרוד עתידי) בלי לגעת בקוד.
_DEFAULT_CORS_ORIGINS = "http://127.0.0.1:8000,http://localhost:8000,null"
ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get("ESOP_CORS_ALLOWED_ORIGINS", _DEFAULT_CORS_ORIGINS).split(",")
    if o.strip()
]

# אין כאן יצירת סכימה (create_all) בכוונה: Alembic הוא מקור האמת היחיד לסכימה.
# כששני המנגנונים חיים יחד, create_all "משלים" בשקט טבלאות חסרות אבל לא מוסיף
# עמודות לטבלה קיימת - וכך DB שנשאר מאחור עולה כאילו הכל תקין ונופל רק בזמן
# ריצה (בדיוק תקלת "no such column: is_active" שכבר נתקלנו בה). בלי create_all,
# DB לא מעודכן ייכשל מיד ובמפורש, וההרצה הנכונה היא: alembic upgrade head.
app = FastAPI(title="ESOP Enterprise Engine API", version=VERSION)

# הגדרת מנגנון CORS - מוקצה למקורות מפורשים (ראו ALLOWED_ORIGINS למעלה).
# allow_credentials=False בכוונה: האימות כולו עובר ב-Authorization: Bearer,
# אף fetch בשלושת הפורטלים לא שולח credentials:"include", ואין שימוש בעוגיות -
# כלומר הדגל לא היה עושה כלום מלבד להרחיב את משטח החשיפה. גם ללא הדגל,
# "*" ביחד עם credentials=True הוא קומבינציה שדפדפנים דוחים מלכתחילה.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],  # מאפשר את כל מתודות ה-HTTP (GET, POST וכו')
    allow_headers=["*"],  # מאפשר את כל ה-Headers
)

app.include_router(router, prefix="/api/v1")

# הגשת הקליינטים (UI) ישירות מהשרת
app.mount("/clients", StaticFiles(directory="clients"), name="clients")

@app.get("/")
def root():
    return {"message": "ESOP Engine API is Running. Access /docs for API documentation.", "version": get_version()}