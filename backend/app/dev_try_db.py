from sqlalchemy import func, select

from app.db import SessionLocal, init_db
from app.models import Document, DocumentUnit, ProcessingEvent, Record, ValidationIssue

init_db()
with SessionLocal() as s:
    d = Document(filename="test.pdf", file_type="pdf", file_size=123, sha256="x" * 64, storage_path="/tmp/test.pdf")
    d.units.append(DocumentUnit(index=1, kind="page", text="hello", method="text-layer", warnings=[]))
    r = Record(unit_start=1, unit_end=1, invoice_number="T-1", total=100.5, currency="USD", ai_data={"total": 100.5}, line_items=[])
    r.issues.append(ValidationIssue(code="total_mismatch", severity="error", message="demo"))
    d.records.append(r)
    d.events.append(ProcessingEvent(event="queued"))
    s.add(d)
    s.commit()
    print("saved document", d.id, "| status:", d.status)
    print("records in db:", s.scalar(select(func.count()).select_from(Record)))
    s.delete(d)
    s.commit()
    print("records after delete:", s.scalar(select(func.count()).select_from(Record)))
