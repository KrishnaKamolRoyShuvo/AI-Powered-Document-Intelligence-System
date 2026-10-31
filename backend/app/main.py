from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from app.api_documents import router as documents_router
from app.db import SessionLocal, init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Document Intelligence API", lifespan=lifespan)
app.include_router(documents_router)


@app.get("/health")
def health():
    with SessionLocal() as s:
        s.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}
