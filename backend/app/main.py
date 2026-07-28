from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from backend.app.database import engine, Base
from backend.app.api.routes import router
from backend.app.version import VERSION, get_version

Base.metadata.create_all(bind=engine)

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