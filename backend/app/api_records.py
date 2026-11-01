import json
import uuid
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Correction, Document, ProcessingEvent, Record
from app.schemas import LineItem
from app.search import index_records
from app.validation import revalidate

router = APIRouter(prefix="/records", tags=["records"])

NUMERIC = {"subtotal", "tax", "discount", "total"}


def _num(v):
    return float(v) if v is not None else None


def record_out(rec, with_history=False):
    out = {
        "id": str(rec.id),
        "document_id": str(rec.document_id),
        "unit_start": rec.unit_start,
        "unit_end": rec.unit_end,
        "invoice_number": rec.invoice_number,
        "invoice_date": rec.invoice_date.isoformat() if rec.invoice_date else None,
        "supplier_name": rec.supplier_name,
        "customer_name": rec.customer_name,
        "subtotal": _num(rec.subtotal),
        "tax": _num(rec.tax),
        "discount": _num(rec.discount),
        "total": _num(rec.total),
        "currency": rec.currency,
        "line_items": rec.line_items or [],
        "uncertain_fields": rec.uncertain_fields or [],
        "needs_review": rec.needs_review,
        "review_status": rec.review_status,
        "model_used": rec.model_used,
        "extraction_seconds": rec.extraction_seconds,
        "ai_data": rec.ai_data or {},
        "issues": [
            {"id": i.id, "code": i.code, "severity": i.severity, "field": i.field,
             "message": i.message, "resolved": bool(i.resolved)}
            for i in sorted(rec.issues, key=lambda i: i.id or 0)
        ],
    }
    if with_history:
        out["corrections"] = [
            {"field": c.field, "old_value": c.old_value, "new_value": c.new_value, "at": c.created_at.isoformat()}
            for c in sorted(rec.corrections, key=lambda c: c.id)
        ]
    return out


def refresh_document_status(s, doc):
    if doc and doc.status in ("done", "needs_review"):
        flagged = s.scalar(
            select(func.count()).select_from(Record).where(Record.document_id == doc.id, Record.needs_review.is_(True))
        )
        doc.status = "needs_review" if flagged else "done"


class RecordPatch(BaseModel):
    invoice_number: str | None = Field(default=None, max_length=100)
    invoice_date: date | None = None
    supplier_name: str | None = Field(default=None, max_length=300)
    customer_name: str | None = Field(default=None, max_length=300)
    subtotal: float | None = None
    tax: float | None = None
    discount: float | None = None
    total: float | None = None
    currency: str | None = None
    line_items: list[LineItem] | None = Field(default=None, max_length=500)

    @field_validator("invoice_number", "supplier_name", "customer_name")
    @classmethod
    def _text(cls, v):
        if v is None:
            return None
        return v.strip() or None

    @field_validator("subtotal", "tax", "discount", "total")
    @classmethod
    def _amount(cls, v):
        if v is not None and abs(v) > 1e13:
            raise ValueError("amount is too large")
        return v

    @field_validator("currency")
    @classmethod
    def _currency(cls, v):
        if v is None:
            return None
        v = v.strip().upper()
        if len(v) != 3 or not v.isalpha():
            raise ValueError("currency must be a 3-letter code")
        return v


def _get(s, record_id):
    rec = s.get(Record, record_id)
    if not rec:
        raise HTTPException(404, "Record not found")
    return rec


@router.get("/{record_id}")
def get_record(record_id: uuid.UUID):
    with SessionLocal() as s:
        return record_out(_get(s, record_id), with_history=True)


@router.patch("/{record_id}")
def patch_record(record_id: uuid.UUID, body: RecordPatch):
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "No fields to change.")
    with SessionLocal() as s:
        rec = _get(s, record_id)
        changed = []
        for field, new in changes.items():
            old = getattr(rec, field)
            if field in NUMERIC:
                old = _num(old)
            if field == "line_items":
                old = old or []
            if old == new:
                continue
            as_text = (lambda v: None if v is None else (json.dumps(v) if field == "line_items" else str(v)))
            s.add(Correction(record_id=rec.id, field=field, old_value=as_text(old), new_value=as_text(new)))
            setattr(rec, field, new)
            changed.append(field)
        if changed:
            rec.review_status = "corrected"
            rec.uncertain_fields = [f for f in (rec.uncertain_fields or []) if f not in changed]
            for iss in rec.issues:
                if iss.code in ("uncertain_field", "invalid_date") and iss.field in changed:
                    iss.resolved = True
                if iss.code in ("read_failed", "extraction_failed"):
                    iss.resolved = True
            revalidate(s, rec)
            try:
                index_records(s, [rec])
            except Exception:
                pass
            refresh_document_status(s, s.get(Document, rec.document_id))
            s.commit()
        return record_out(rec, with_history=True)


@router.post("/{record_id}/approve")
def approve_record(record_id: uuid.UUID):
    with SessionLocal() as s:
        rec = _get(s, record_id)
        rec.review_status = "approved"
        rec.needs_review = False
        s.add(ProcessingEvent(document_id=rec.document_id, event="record_approved", detail={"record_id": str(rec.id)}))
        refresh_document_status(s, s.get(Document, rec.document_id))
        s.commit()
        return record_out(rec, with_history=True)
