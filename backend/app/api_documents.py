import hashlib
import os
import uuid

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from redis import Redis
from rq import Queue
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Document, ProcessingEvent

router = APIRouter(prefix="/documents", tags=["documents"])

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/uploads")
MAX_FILE_MB = int(os.environ.get("MAX_FILE_MB", "200"))
MAX_FILES = 50
ALLOWED = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".xlsx", ".xls", ".csv"}

_queue = None


def get_queue():
    global _queue
    if _queue is None:
        _queue = Queue("documents", connection=Redis.from_url(os.environ["REDIS_URL"]))
    return _queue


def doc_summary(d):
    pct = round(100 * d.units_done / d.units_total) if d.units_total else 0
    return {
        "id": str(d.id),
        "filename": d.filename,
        "file_type": d.file_type,
        "file_size": d.file_size,
        "status": d.status,
        "error_code": d.error_code,
        "error_message": d.error_message,
        "units_total": d.units_total,
        "units_done": d.units_done,
        "progress_pct": pct,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "finished_at": d.finished_at.isoformat() if d.finished_at else None,
    }


def _reject(name, error):
    return {"filename": name, "accepted": False, "error": error}


@router.post("", status_code=202)
def upload(files: list[UploadFile] = File(...)):
    if len(files) > MAX_FILES:
        raise HTTPException(413, f"Too many files (max {MAX_FILES} per request).")
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    results = []
    for f in files:
        name = os.path.basename(f.filename or "file")
        ext = os.path.splitext(name)[1].lower()
        if ext not in ALLOWED:
            results.append(_reject(name, f"unsupported_type: {ext or 'none'}"))
            continue

        doc_id = uuid.uuid4()
        path = os.path.join(UPLOAD_DIR, f"{doc_id}{ext}")
        sha, size, too_big = hashlib.sha256(), 0, False
        with open(path, "wb") as out:
            while chunk := f.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_FILE_MB * 1024 * 1024:
                    too_big = True
                    break
                sha.update(chunk)
                out.write(chunk)
        if too_big or size == 0:
            os.remove(path)
            results.append(_reject(name, "file_too_large" if too_big else "empty_file"))
            continue

        with SessionLocal() as s:
            doc = Document(
                id=doc_id, filename=name[:500], file_size=size,
                sha256=sha.hexdigest(), storage_path=path, status="queued",
            )
            doc.events.append(ProcessingEvent(event="uploaded", detail={"size": size}))
            s.add(doc)
            s.commit()
            try:
                get_queue().enqueue("app.tasks.process_document", str(doc_id), job_timeout=3600)
                doc.events.append(ProcessingEvent(event="queued"))
            except Exception:
                doc.status = "failed"
                doc.error_code = "queue_unavailable"
                doc.error_message = "Could not queue the job. Try reprocessing later."
            s.commit()
            results.append({
                "id": str(doc_id), "filename": name,
                "accepted": doc.status == "queued", "status": doc.status,
            })
    return {"files": results}


@router.get("")
def list_documents(
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    with SessionLocal() as s:
        q = select(Document)
        if status:
            q = q.where(Document.status == status)
        total = s.scalar(select(func.count()).select_from(q.subquery()))
        rows = s.scalars(q.order_by(Document.created_at.desc()).limit(limit).offset(offset)).all()
        return {"total": total, "items": [doc_summary(d) for d in rows]}


@router.get("/{doc_id}")
def get_document(doc_id: uuid.UUID):
    with SessionLocal() as s:
        d = s.get(Document, doc_id)
        if not d:
            raise HTTPException(404, "Document not found")
        out = doc_summary(d)
        out["attempts"] = d.attempts
        out["events"] = [
            {"event": e.event, "detail": e.detail, "at": e.created_at.isoformat()}
            for e in sorted(d.events, key=lambda e: e.id)
        ]
        return out
