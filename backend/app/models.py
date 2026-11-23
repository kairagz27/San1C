from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(stamp: datetime) -> datetime:
    # SQLite returns naive UTC datetimes, PostgreSQL returns aware timestamps.
    if stamp.tzinfo is None:
        return stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


class Snapshot(Base):
    __tablename__ = "snapshots"
    dataset: Mapped[str] = mapped_column(String(4), primary_key=True)
    business_date: Mapped[date] = mapped_column(primary_key=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    row_count: Mapped[int]
    digest: Mapped[str] = mapped_column(String(64))


class Fact(Base):
    __tablename__ = "facts"
    __table_args__ = (
        CheckConstraint("operation IN ('sale','return')", name="ck_facts_operation"),
        CheckConstraint("amount >= 0 AND quantity >= 0", name="ck_facts_unsigned"),
        Index("ix_facts_store", "dataset", "store_id", "business_date"),
        Index("ix_facts_category", "dataset", "category_id", "business_date"),
    )
    dataset: Mapped[str] = mapped_column(String(4), primary_key=True)
    business_date: Mapped[date] = mapped_column(primary_key=True)
    line_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(160))
    store_id: Mapped[str] = mapped_column(String(160))
    store_name: Mapped[str] = mapped_column(String(240))
    product_id: Mapped[str] = mapped_column(String(160))
    product_name: Mapped[str] = mapped_column(String(240))
    category_id: Mapped[str] = mapped_column(String(160))
    category_name: Mapped[str] = mapped_column(String(240))
    operation: Mapped[str] = mapped_column(String(6))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))


class DayIssue(Base):
    """Latest unsuccessful transfer that has not been superseded by a complete day."""

    __tablename__ = "day_issues"
    dataset: Mapped[str] = mapped_column(String(4), primary_key=True)
    business_date: Mapped[date] = mapped_column(primary_key=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    code: Mapped[str] = mapped_column(String(40))


class SyncEvent(Base):
    __tablename__ = "sync_events"
    __table_args__ = (Index("ix_sync_events_dataset_id", "dataset", "id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    dataset: Mapped[str] = mapped_column(String(4))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    business_date: Mapped[date | None]
    code: Mapped[str] = mapped_column(String(40))
    rows: Mapped[int | None]


class WebSession(Base):
    __tablename__ = "web_sessions"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    credential_tag: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(8), default="owner", server_default="owner")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SourceAccess(Base):
    __tablename__ = "source_access"
    id: Mapped[int] = mapped_column(primary_key=True)
    managed: Mapped[bool] = mapped_column(default=False)
    token_hash: Mapped[str | None] = mapped_column(String(64))
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_check_status: Mapped[str | None] = mapped_column(String(32))


class Reconciliation(Base):
    __tablename__ = "reconciliations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    report_name: Mapped[str] = mapped_column(String(200))
    start: Mapped[date]
    end: Mapped[date]
    point: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(16))
    result: Mapped[dict] = mapped_column(JSON)


class Widget(Base):
    __tablename__ = "widgets"
    __table_args__ = (Index("ix_widgets_owner", "dataset", "owner"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    dataset: Mapped[str] = mapped_column(String(4))
    owner: Mapped[str] = mapped_column(String(128), default="company")
    question: Mapped[str] = mapped_column(Text)
    plan: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
