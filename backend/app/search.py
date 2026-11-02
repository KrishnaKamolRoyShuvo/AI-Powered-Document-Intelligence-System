import os

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import defer

from app.embeddings import embed_texts
from app.models import Document, DocumentUnit, Record, RecordSearch

RRF_K = 60
CANDIDATES = 100
CHUNK = 200
MIN_SIM = float(os.environ.get("SEMANTIC_MIN_SIM", "0.5"))
REL_GAP = float(os.environ.get("SEMANTIC_REL_GAP", "0.05"))
SEM_TOP_K = int(os.environ.get("SEMANTIC_TOP_K", "5"))


def _like(text):
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def build_search_text(rec, filename, unit_text=None, is_sheet=False):
    parts = [f"File: {filename}"]
    total = f"{rec.total} {rec.currency or ''}".strip() if rec.total is not None else None
    for label, value in (
        ("Invoice", rec.invoice_number), ("Supplier", rec.supplier_name),
        ("Customer", rec.customer_name), ("Date", rec.invoice_date), ("Total", total),
    ):
        if value:
            parts.append(f"{label}: {value}")
    items = "; ".join(li.get("description") for li in (rec.line_items or []) if li.get("description"))
    if items:
        parts.append("Items: " + items[:1500])
    if unit_text and not is_sheet:
        parts.append("Text: " + " ".join(unit_text.split())[:1500])
    return "\n".join(parts)


def index_records(s, records):
    """Builds the search text for each record and (re)computes embeddings where needed."""
    stats = {"indexed": 0, "embedded": 0, "embed_failed": 0}
    for i in range(0, len(records), CHUNK):
        chunk = records[i:i + CHUNK]
        ids = [r.id for r in chunk]
        doc_ids = {r.document_id for r in chunk}
        docs = {d.id: d for d in s.scalars(select(Document).where(Document.id.in_(doc_ids)))}
        page_docs = [d for d, doc in docs.items() if doc.file_type != "spreadsheet"]
        units = {}
        if page_docs:
            rows = s.execute(
                select(DocumentUnit.document_id, DocumentUnit.index, DocumentUnit.text).where(
                    DocumentUnit.document_id.in_(page_docs),
                    DocumentUnit.index.in_({r.unit_start for r in chunk}),
                )
            )
            for doc_id, idx, text in rows:
                units[(doc_id, idx)] = text
        existing = {
            row.record_id: row
            for row in s.scalars(
                select(RecordSearch).options(defer(RecordSearch.embedding)).where(RecordSearch.record_id.in_(ids))
            )
        }
        have_vec = set(s.scalars(
            select(RecordSearch.record_id).where(RecordSearch.record_id.in_(ids), RecordSearch.embedding.isnot(None))
        ))
        todo = []
        for r in chunk:
            doc = docs[r.document_id]
            text = build_search_text(r, doc.filename, units.get((r.document_id, r.unit_start)), doc.file_type == "spreadsheet")
            row = existing.get(r.id)
            if row is None:
                row = RecordSearch(record_id=r.id, search_text=text)
                s.add(row)
                need = True
            else:
                changed = row.search_text != text
                need = changed or r.id not in have_vec
                row.search_text = text
                if changed:
                    row.embedding = None  # old vector no longer matches the text
            if need and "\n" in text:  # records with only a file name carry no meaning to embed
                todo.append(row)
            stats["indexed"] += 1
        s.flush()
        if todo:
            try:
                vectors = embed_texts([row.search_text for row in todo], "RETRIEVAL_DOCUMENT")
                for row, vec in zip(todo, vectors):
                    row.embedding = vec
                    row.embedding_model = os.environ.get("EMBEDDING_MODEL")
                stats["embedded"] += len(todo)
            except Exception:
                stats["embed_failed"] += len(todo)
        s.commit()
    return stats


def _conds(p):
    c = []
    for field, col in (("supplier", Record.supplier_name), ("customer", Record.customer_name), ("invoice_number", Record.invoice_number)):
        if p.get(field):
            c.append(col.ilike(_like(p[field]), escape="\\"))
    if p.get("currency"):
        c.append(Record.currency == p["currency"].upper())
    if p.get("status"):
        c.append(Document.status == p["status"])
    if p.get("needs_review") is not None:
        c.append(Record.needs_review.is_(p["needs_review"]))
    if p.get("date_from"):
        c.append(Record.invoice_date >= p["date_from"])
    if p.get("date_to"):
        c.append(Record.invoice_date <= p["date_to"])
    if p.get("min_total") is not None:
        c.append(Record.total >= p["min_total"])
    if p.get("max_total") is not None:
        c.append(Record.total <= p["max_total"])
    if p.get("document_id"):
        c.append(Record.document_id == p["document_id"])
    return c


def _item(rec, filename, doc_status, score=None, sim=None, by=None):
    return {
        "record_id": str(rec.id), "document_id": str(rec.document_id), "filename": filename,
        "document_status": doc_status, "unit_start": rec.unit_start,
        "invoice_number": rec.invoice_number,
        "invoice_date": rec.invoice_date.isoformat() if rec.invoice_date else None,
        "supplier_name": rec.supplier_name, "customer_name": rec.customer_name,
        "total": float(rec.total) if rec.total is not None else None, "currency": rec.currency,
        "needs_review": rec.needs_review, "review_status": rec.review_status,
        "score": round(score, 4) if score is not None else None, "similarity": sim, "matched_by": by or [],
    }


def run_search(s, p):
    conds = _conds(p)
    q = (p.get("q") or "").strip()
    limit, offset = p["limit"], p["offset"]
    notes = []
    base = select(Record, Document.filename, Document.status).join(Document, Document.id == Record.document_id)

    if not q:  # filters only
        total = s.scalar(
            select(func.count()).select_from(Record).join(Document, Document.id == Record.document_id).where(*conds)
        )
        rows = s.execute(
            base.where(*conds).order_by(Record.created_at.desc(), Record.unit_start).limit(limit).offset(offset)
        ).all()
        return {"total": total, "limit": limit, "offset": offset, "notes": notes,
                "items": [_item(r, fn, st) for r, fn, st in rows]}

    base = base.outerjoin(RecordSearch, RecordSearch.record_id == Record.id)
    merged = {}
    mode = p["mode"]

    def add(rec, fn, st, rank, by, sim=None):
        m = merged.setdefault(rec.id, {"row": (rec, fn, st), "score": 0.0, "by": [], "sim": None})
        m["score"] += 1.0 / (RRF_K + rank + 1)
        m["by"].append(by)
        if sim is not None:
            m["sim"] = sim

    if mode in ("hybrid", "keyword"):
        tsq = func.websearch_to_tsquery("simple", q)
        like = _like(q)
        exact = or_(
            Record.invoice_number.ilike(like, escape="\\"), Record.supplier_name.ilike(like, escape="\\"),
            Record.customer_name.ilike(like, escape="\\"), Document.filename.ilike(like, escape="\\"),
        )
        score = func.coalesce(func.ts_rank_cd(RecordSearch.tsv, tsq), 0) + case((exact, 1.0), else_=0.0)
        rows = s.execute(
            base.add_columns(score.label("score"))
            .where(*conds, or_(RecordSearch.tsv.op("@@")(tsq), exact))
            .order_by(score.desc()).limit(CANDIDATES)
        ).all()
        for rank, (rec, fn, st, _sc) in enumerate(rows):
            add(rec, fn, st, rank, "keyword")

    if mode in ("hybrid", "semantic"):
        try:
            qvec = embed_texts([q], "RETRIEVAL_QUERY")[0]
            dist = RecordSearch.embedding.cosine_distance(qvec)
            rows = s.execute(
                base.add_columns((1 - dist).label("sim"))
                .where(*conds, RecordSearch.embedding.isnot(None), (1 - dist) >= MIN_SIM)
                .order_by(dist).limit(CANDIDATES)
            ).all()
            if rows:
                best = float(rows[0][3])
                rows = [r for r in rows if float(r[3]) >= best - REL_GAP]
                rows = rows[:SEM_TOP_K]
            for rank, (rec, fn, st, sim) in enumerate(rows):
                add(rec, fn, st, rank, "semantic", round(float(sim), 3))
        except Exception:
            notes.append("semantic_search_unavailable: showing keyword results only")

    ranked = sorted(merged.values(), key=lambda m: -m["score"])
    page = ranked[offset:offset + limit]
    return {"total": len(ranked), "limit": limit, "offset": offset, "notes": notes,
            "items": [_item(*m["row"], score=m["score"], sim=m["sim"], by=m["by"]) for m in page]}
