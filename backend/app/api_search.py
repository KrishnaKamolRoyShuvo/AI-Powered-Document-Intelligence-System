import uuid
from datetime import date

from fastapi import APIRouter, Query
from sqlalchemy import func, or_, select

from app.db import SessionLocal
from app.models import Record, RecordSearch
from app.search import index_records, run_search

router = APIRouter(prefix="/search", tags=["search"])


@router.get("")
def search(
    q: str | None = Query(None, max_length=200),
    mode: str = Query("hybrid", pattern="^(hybrid|keyword|semantic)$"),
    supplier: str | None = Query(None, max_length=200),
    customer: str | None = Query(None, max_length=200),
    invoice_number: str | None = Query(None, max_length=100),
    currency: str | None = Query(None, max_length=3),
    status: str | None = Query(None, pattern="^(queued|processing|done|needs_review|failed)$"),
    needs_review: bool | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    min_total: float | None = None,
    max_total: float | None = None,
    document_id: uuid.UUID | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    params = {
        "q": q, "mode": mode, "supplier": supplier, "customer": customer, "invoice_number": invoice_number,
        "currency": currency, "status": status, "needs_review": needs_review, "date_from": date_from,
        "date_to": date_to, "min_total": min_total, "max_total": max_total, "document_id": document_id,
        "limit": limit, "offset": offset,
    }
    with SessionLocal() as s:
        return run_search(s, params)


@router.post("/reindex")
def reindex(limit: int = Query(500, ge=1, le=5000)):
    """Indexes records that have no search row or no embedding yet."""
    missing = or_(RecordSearch.record_id.is_(None), RecordSearch.embedding.is_(None))
    with SessionLocal() as s:
        recs = s.scalars(
            select(Record).outerjoin(RecordSearch, RecordSearch.record_id == Record.id).where(missing).limit(limit)
        ).all()
        stats = index_records(s, recs)
        remaining = s.scalar(
            select(func.count()).select_from(Record).outerjoin(RecordSearch, RecordSearch.record_id == Record.id).where(missing)
        )
        return {"processed": len(recs), **stats, "remaining": remaining}
