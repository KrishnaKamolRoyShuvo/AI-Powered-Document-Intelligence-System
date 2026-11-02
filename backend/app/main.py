import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api_documents import router as documents_router
from app.api_records import router as records_router
from app.api_search import router as search_router
from app.db import SessionLocal, init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Document Intelligence API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()],
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["*"],
)
app.include_router(documents_router)
app.include_router(records_router)
app.include_router(search_router)


@app.get("/health")
def health():
    with SessionLocal() as s:
        s.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}
