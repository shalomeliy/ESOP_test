from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from backend.app.api.routes import router
from backend.app.version import VERSION, get_version

# אין כאן יצירת סכימה (create_all) בכוונה: Alembic הוא מקור האמת היחיד לסכימה.
# כששני המנגנונים חיים יחד, create_all "משלים" בשקט טבלאות חסרות אבל לא מוסיף
# עמודות לטבלה קיימת - וכך DB שנשאר מאחור עולה כאילו הכל תקין ונופל רק בזמן
# ריצה (בדיוק תקלת "no such column: is_active" שכבר נתקלנו בה). בלי create_all,
# DB לא מעודכן ייכשל מיד ובמפורש, וההרצה הנכונה היא: alembic upgrade head.
app = FastAPI(title="ESOP Enterprise Engine API", version=VERSION)

# הגדרת מנגנון CORS כדי לאפשר לפורטלים לפנות ל-API גם מקבצים מקומיים וגם מדפדפן
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # מאפשר גישה מכל מקור (Origin)
    allow_credentials=True,
    allow_methods=["*"],  # מאפשר את כל מתודות ה-HTTP (GET, POST וכו')
    allow_headers=["*"],  # מאפשר את כל ה-Headers
)

app.include_router(router, prefix="/api/v1")

# הגשת הקליינטים (UI) ישירות מהשרת
app.mount("/clients", StaticFiles(directory="clients"), name="clients")

@app.get("/")
def root():
    return {"message": "ESOP Engine API is Running. Access /docs for API documentation.", "version": get_version()}