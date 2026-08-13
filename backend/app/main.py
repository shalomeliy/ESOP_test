import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from backend.app.api import (
    auth, employees, company, grants, exercise_requests, audit, ledger,
    trustee, employee_dashboard, documents, notifications, search_meta, export,
    cap_table, reports,
)
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

# פיצול הראוטר המונוליטי לפי תחום (v0.9.1) - ראו HANDOFF.md. כל APIRouter()
# נשאר bare (בלי prefix משלו): /api/v1 הוא הקידומת היחידה, tags= כאן קובע
# את הקיבוץ ב-/docs בלי לשנות אף נתיב בפועל.
app.include_router(search_meta.router, prefix="/api/v1", tags=["search_meta"])
app.include_router(auth.router, prefix="/api/v1", tags=["auth"])
app.include_router(notifications.router, prefix="/api/v1", tags=["notifications"])
app.include_router(employees.router, prefix="/api/v1", tags=["employees"])
app.include_router(company.router, prefix="/api/v1", tags=["company"])
app.include_router(grants.router, prefix="/api/v1", tags=["grants"])
app.include_router(exercise_requests.router, prefix="/api/v1", tags=["exercise_requests"])
app.include_router(audit.router, prefix="/api/v1", tags=["audit"])
app.include_router(ledger.router, prefix="/api/v1", tags=["ledger"])
app.include_router(trustee.router, prefix="/api/v1", tags=["trustee"])
app.include_router(employee_dashboard.router, prefix="/api/v1", tags=["employee_dashboard"])
app.include_router(documents.router, prefix="/api/v1", tags=["documents"])
app.include_router(export.router, prefix="/api/v1", tags=["export"])
app.include_router(cap_table.router, prefix="/api/v1", tags=["cap_table"])
app.include_router(reports.router, prefix="/api/v1", tags=["reports"])

# הגשת הקליינטים (UI) ישירות מהשרת
app.mount("/clients", StaticFiles(directory="clients"), name="clients")

@app.get("/")
def root():
    return {"message": "ESOP Engine API is Running. Access /docs for API documentation.", "version": get_version()}