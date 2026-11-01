import os
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.extractor import extract_invoice
from app.models import Document, DocumentUnit, ProcessingEvent, Record, ValidationIssue
from app.readers import ReadError, load_sheets, read_file, unit_image_bytes
from app.sheet_mapper import extract_sheet
from app.validation import revalidate

CONCURRENCY = int(os.environ.get("EXTRACT_CONCURRENCY", "3"))


def _clean(text):
    return (text or "").replace("\x00", "")


def _log(s, doc_id, event, detail=None):
    s.add(ProcessingEvent(document_id=doc_id, event=event, detail=detail))


def _fail(s, doc, code, message, detail=None):
    doc.status = "failed"
    doc.error_code = code
    doc.error_message = message
    doc.finished_at = datetime.now(timezone.utc)
    _log(s, doc.id, "failed", {"code": code, **(detail or {})})
    s.commit()


def _to_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _extract(path, file_type, unit):
    try:
        img, mime = unit_image_bytes(path, file_type, unit.index)
    except Exception:
        img, mime = None, "image/jpeg"
    return extract_invoice(ocr_text=_clean(unit.text) or None, image_bytes=img, mime_type=mime)


def _issue(rec, code, severity, field, message):
    rec.issues.append(ValidationIssue(code=code, severity=severity, field=field, message=message))


def _save_record(s, doc_id, unit, res):
    rec = Record(document_id=doc_id, unit_start=unit.index, unit_end=unit.index)
    if unit.method == "failed":
        _issue(rec, "read_failed", "error", None, "This page could not be read.")
    elif not res["ok"]:
        _issue(rec, "extraction_failed", "error", None, "The AI extraction failed for this page. Try reprocessing.")
    else:
        d = res["data"]
        rec.ai_data = d.model_dump()
        rec.invoice_number = d.invoice_number
        rec.invoice_date = _to_date(d.invoice_date)
        rec.supplier_name = d.supplier_name
        rec.customer_name = d.customer_name
        rec.subtotal = d.subtotal
        rec.tax = d.tax
        rec.discount = d.discount
        rec.total = d.total
        rec.currency = (d.currency or "")[:3].upper() or None
        rec.line_items = [li.model_dump() for li in d.line_items]
        rec.uncertain_fields = d.uncertain_fields
        rec.model_used = res["model"]
        rec.extraction_seconds = res["seconds"]
        if d.invoice_date and rec.invoice_date is None:
            _issue(rec, "invalid_date", "warning", "invoice_date", f"Could not read the date '{d.invoice_date}'.")
        for field in d.uncertain_fields:
            _issue(rec, "uncertain_field", "warning", field, "The AI was not sure about this value.")
        if "low_ocr_confidence" in unit.warnings:
            _issue(rec, "low_ocr_confidence", "warning", None, "OCR quality was low on this page.")
    rec.needs_review = len(rec.issues) > 0
    s.add(rec)


def _process(doc_id):
    did = uuid.UUID(doc_id)
    with SessionLocal() as s:
        doc = s.get(Document, did)
        if doc is None:
            return
        # clear old results (this also makes reprocessing work)
        s.execute(delete(Record).where(Record.document_id == did))
        s.execute(delete(DocumentUnit).where(DocumentUnit.document_id == did))
        doc.status = "processing"
        doc.attempts += 1
        doc.error_code = None
        doc.error_message = None
        doc.units_total = 0
        doc.units_done = 0
        doc.finished_at = None
        _log(s, did, "started", {"attempt": doc.attempts})
        s.commit()

        try:
            result = read_file(doc.storage_path)
        except ReadError as e:
            _fail(s, doc, e.code, e.message)
            return

        doc.file_type = result.file_type
        doc.units_total = len(result.units)
        for u in result.units:
            s.add(DocumentUnit(
                document_id=did, index=u.index, kind=u.kind, name=u.name,
                text=_clean(u.text), method=u.method,
                ocr_confidence=u.ocr_confidence, warnings=u.warnings,
            ))
        _log(s, did, "read_done", {"units": len(result.units), "file_type": result.file_type})
        s.commit()

        if result.file_type == "spreadsheet":
            _process_sheets(s, doc, did)
            return

        # pages that could not be read are recorded right away
        for u in result.units:
            if u.method == "failed":
                _save_record(s, did, u, None)
                doc.units_done += 1
        s.commit()

        todo = [u for u in result.units if u.method != "failed"]
        with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
            futures = {pool.submit(_extract, doc.storage_path, result.file_type, u): u for u in todo}
            for fut in as_completed(futures):
                u = futures[fut]
                try:
                    res = fut.result()
                except Exception:
                    res = {"ok": False, "data": None, "model": None, "seconds": 0, "errors": ["exception"]}
                _save_record(s, did, u, res)
                doc.units_done += 1
                s.commit()

        records = s.scalars(select(Record).where(Record.document_id == did)).all()
        for r in records:
            revalidate(s, r)
        s.commit()
        failed = [r for r in records if not r.ai_data]
        if records and len(failed) == len(records):
            _fail(s, doc, "extraction_failed", "No page could be extracted. Try reprocessing.")
            return
        doc.status = "needs_review" if any(r.needs_review for r in records) else "done"
        doc.finished_at = datetime.now(timezone.utc)
        _log(s, did, "finished", {"status": doc.status, "records": len(records), "failed_pages": len(failed)})
        s.commit()


def process_document(doc_id):
    try:
        _process(doc_id)
    except Exception as e:
        with SessionLocal() as s:
            doc = s.get(Document, uuid.UUID(doc_id))
            if doc:
                _fail(s, doc, "internal_error", "Unexpected error while processing.", {"exception": type(e).__name__})
        raise


# ---------- spreadsheets ----------

MAX_SHEET_RECORDS = 20000


def _cut(value, n):
    return value[:n] if value else None


def _save_sheet_record(s, doc_id, unit_index, r, label):
    rec = Record(document_id=doc_id, unit_start=unit_index, unit_end=unit_index)
    rec.ai_data = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in r.items() if k != "unparsed"}
    rec.invoice_number = _cut(r["invoice_number"], 100)
    rec.invoice_date = r["invoice_date"]
    rec.supplier_name = _cut(r["supplier_name"], 300)
    rec.customer_name = _cut(r["customer_name"], 300)
    rec.subtotal = r["subtotal"]
    rec.tax = r["tax"]
    rec.discount = r["discount"]
    rec.total = r["total"]
    rec.currency = r["currency"]
    rec.line_items = r["line_items"]
    rec.uncertain_fields = r["uncertain_fields"]
    rec.model_used = _cut(label, 80)
    for f in r["unparsed"]:
        if f == "invoice_date":
            _issue(rec, "invalid_date", "warning", f, "Could not read the date in this row.")
        else:
            _issue(rec, "uncertain_field", "warning", f, f"Could not read a number for {f} in this row.")
    for f in r["uncertain_fields"]:
        _issue(rec, "uncertain_field", "warning", f, "The AI was not sure about this value.")
    rec.needs_review = len(rec.issues) > 0
    s.add(rec)


def _process_sheets(s, doc, did):
    try:
        sheets = load_sheets(doc.storage_path)
    except ReadError as e:
        _fail(s, doc, e.code, e.message)
        return
    failures = 0
    for idx, (name, df) in enumerate(sheets.items(), start=1):
        if df.empty:
            doc.units_done += 1
            s.commit()
            continue
        try:
            out = extract_sheet(name, df)
        except Exception as e:
            _log(s, did, "sheet_failed", {"sheet": name, "exception": type(e).__name__})
            failures += 1
            doc.units_done += 1
            s.commit()
            continue
        _log(s, did, "sheet_mapped", {
            "sheet": name, "layout": out["layout"], "method": out["method"],
            "records": len(out["records"]), "mapping": out["mapping"], "errors": out["errors"],
        })
        label = out["model"] or out["method"]
        for n, r in enumerate(out["records"][:MAX_SHEET_RECORDS], start=1):
            _save_sheet_record(s, did, idx, r, label)
            if n % 500 == 0:
                s.commit()
        if len(out["records"]) > MAX_SHEET_RECORDS:
            _log(s, did, "sheet_truncated", {"sheet": name, "kept": MAX_SHEET_RECORDS})
        doc.units_done += 1
        s.commit()

    records = s.scalars(select(Record).where(Record.document_id == did)).all()
    if not records:
        if failures:
            _fail(s, doc, "extraction_failed", "The spreadsheet could not be processed. Try reprocessing.")
        else:
            _fail(s, doc, "no_invoice_data", "No invoice data was found in this spreadsheet.")
        return
    for r in records:
        revalidate(s, r)
    s.commit()
    doc.status = "needs_review" if any(r.needs_review for r in records) else "done"
    doc.finished_at = datetime.now(timezone.utc)
    _log(s, did, "finished", {"status": doc.status, "records": len(records)})
    s.commit()
