import os
import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Integer,
    Computed, Index, Numeric, String, Text, UniqueConstraint, Uuid, func,
)
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Document(Base):
    """One uploaded file."""
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    filename: Mapped[str] = mapped_column(String(500))
    file_type: Mapped[str | None] = mapped_column(String(20))
    file_size: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_path: Mapped[str] = mapped_column(Text)
    # queued | processing | done | needs_review | failed
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    error_code: Mapped[str | None] = mapped_column(String(50))
    error_message: Mapped[str | None] = mapped_column(Text)
    units_total: Mapped[int] = mapped_column(Integer, default=0)
    units_done: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    units: Mapped[list["DocumentUnit"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    records: Mapped[list["Record"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    events: Mapped[list["ProcessingEvent"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class DocumentUnit(Base):
    """One page, image frame or sheet, with its extracted text."""
    __tablename__ = "document_units"
    __table_args__ = (UniqueConstraint("document_id", "index"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    index: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(10))
    name: Mapped[str | None] = mapped_column(String(300))
    text: Mapped[str] = mapped_column(Text, default="")
    method: Mapped[str] = mapped_column(String(20))
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    warnings: Mapped[list] = mapped_column(JSONB, default=list)

    document: Mapped["Document"] = relationship(back_populates="units")


class Record(Base):
    """One extracted invoice. ai_data is the untouched AI answer; the other fields are the current values."""
    __tablename__ = "records"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    unit_start: Mapped[int] = mapped_column(Integer)
    unit_end: Mapped[int] = mapped_column(Integer)
    ai_data: Mapped[dict] = mapped_column(JSONB, default=dict)

    invoice_number: Mapped[str | None] = mapped_column(String(100), index=True)
    invoice_date: Mapped[date | None] = mapped_column(Date, index=True)
    supplier_name: Mapped[str | None] = mapped_column(String(300), index=True)
    customer_name: Mapped[str | None] = mapped_column(String(300))
    subtotal: Mapped[float | None] = mapped_column(Numeric(18, 2))
    tax: Mapped[float | None] = mapped_column(Numeric(18, 2))
    discount: Mapped[float | None] = mapped_column(Numeric(18, 2))
    total: Mapped[float | None] = mapped_column(Numeric(18, 2), index=True)
    currency: Mapped[str | None] = mapped_column(String(3))
    line_items: Mapped[list] = mapped_column(JSONB, default=list)
    uncertain_fields: Mapped[list] = mapped_column(JSONB, default=list)

    model_used: Mapped[str | None] = mapped_column(String(80))
    extraction_seconds: Mapped[float | None] = mapped_column(Float)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # pending | approved | corrected
    review_status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    document: Mapped["Document"] = relationship(back_populates="records")
    issues: Mapped[list["ValidationIssue"]] = relationship(back_populates="record", cascade="all, delete-orphan")
    corrections: Mapped[list["Correction"]] = relationship(back_populates="record", cascade="all, delete-orphan")


class ValidationIssue(Base):
    __tablename__ = "validation_issues"

    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(50))
    severity: Mapped[str] = mapped_column(String(10))  # error | warning
    field: Mapped[str | None] = mapped_column(String(50))
    message: Mapped[str] = mapped_column(Text)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    record: Mapped["Record"] = relationship(back_populates="issues")


class Correction(Base):
    """Log of every manual edit."""
    __tablename__ = "corrections"

    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(50))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    record: Mapped["Record"] = relationship(back_populates="corrections")


class ProcessingEvent(Base):
    """Processing history of a document."""
    __tablename__ = "processing_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    event: Mapped[str] = mapped_column(String(50))
    detail: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document: Mapped["Document"] = relationship(back_populates="events")


class RecordSearch(Base):
    """Search text, full-text vector and embedding for one record."""
    __tablename__ = "record_search"
    __table_args__ = (
        Index("ix_record_search_tsv", "tsv", postgresql_using="gin"),
        Index("ix_record_search_embedding", "embedding", postgresql_using="hnsw",
              postgresql_ops={"embedding": "vector_cosine_ops"}),
    )

    record_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"), primary_key=True)
    search_text: Mapped[str] = mapped_column(Text, default="")
    tsv: Mapped[str | None] = mapped_column(TSVECTOR, Computed("to_tsvector('simple', search_text)", persisted=True))
    embedding = mapped_column(Vector(int(os.environ.get("EMBEDDING_DIM", "768"))), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(80))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
