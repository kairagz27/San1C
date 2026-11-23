import hashlib
import json
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, DecimalException
from typing import Annotated, Literal, Never

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StringConstraints, ValidationError, field_validator, model_validator
from sqlalchemy import delete, insert, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect

from .auth import require_upload_token, verify_upload_key
from .db import get_db
from .models import DayIssue, Fact, Snapshot, SourceAccess, SyncEvent, as_utc, utcnow


router = APIRouter(tags=["import"])
MAX_BYTES = 4 * 1024 * 1024
MAX_ROWS = 10_000
Ident = Annotated[str, StringConstraints(min_length=1, max_length=160, strip_whitespace=True)]
Name = Annotated[str, StringConstraints(min_length=1, max_length=240, strip_whitespace=True)]


def parse_date(value: object) -> date | None:
    if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    return None


def parse_instant(value: object) -> datetime | None:
    if isinstance(value, str) and len(value) <= 40:
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is not None:
                return as_utc(stamp)
        except (ValueError, OverflowError):
            pass
    return None


class Row(BaseModel):
    model_config = ConfigDict(extra="forbid")
    business_date: date
    line_id: Ident
    document_id: Ident
    store_id: Ident
    store_name: Name
    product_id: Ident
    product_name: Name
    category_id: Ident
    category_name: Name
    operation: Literal["sale", "return"]
    quantity: Decimal = Field(ge=0, max_digits=18, decimal_places=3)
    amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)

    @field_validator("business_date", mode="before")
    @classmethod
    def iso_day(cls, value):
        if not (day := parse_date(value)):
            raise ValueError("invalid business date")
        return day

    @field_validator("line_id", "document_id", "store_id", "store_name", "product_id", "product_name", "category_id", "category_name")
    @classmethod
    def printable_text(cls, value):
        if any(unicodedata.category(char) in {"Cc", "Cs"} for char in value):
            raise ValueError("invalid text")
        return value


class DayPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    business_date: date
    generated_at: datetime
    complete: StrictBool
    row_count: Annotated[StrictInt, Field(ge=0, le=MAX_ROWS)]
    rows: Annotated[list[Row], Field(max_length=MAX_ROWS)]

    @field_validator("generated_at", mode="before")
    @classmethod
    def explicit_instant(cls, value):
        if not (stamp := parse_instant(value)):
            raise ValueError("timezone required")
        return stamp

    @field_validator("business_date", mode="before")
    @classmethod
    def iso_day(cls, value):
        if not (day := parse_date(value)):
            raise ValueError("invalid business date")
        return day

    @model_validator(mode="after")
    def full_day(self):
        if not self.complete or self.row_count != len(self.rows):
            raise ValueError("incomplete day")
        if any(row.business_date != self.business_date for row in self.rows):
            raise ValueError("wrong business date")
        if len({row.line_id for row in self.rows}) != self.row_count:
            raise ValueError("duplicate line")
        return self


def day_lock(db: Session, day: date) -> None:
    # Existing and previously unseen dates use the same transaction-scoped lock.
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(731102, :day)"), {"day": day.toordinal()})
    else:
        db.execute(text("BEGIN IMMEDIATE"))


def record_event(db: Session, day: date | None, code: str, rows: int | None = None) -> None:
    db.add(SyncEvent(dataset="live", business_date=day, code=code, rows=rows))


def reject_transfer(db: Session, day: date | None, stamp: datetime | None, code: str, status: int) -> Never:
    if day is not None:
        day_lock(db, day)
        key = ("live", day)
        current = db.get(Snapshot, key)
        issue = db.get(DayIssue, key)
        latest = [as_utc(x.generated_at) for x in (current, issue) if x and x.generated_at]
        if stamp is None or all(stamp >= known for known in latest):
            if issue is None:
                issue = DayIssue(dataset="live", business_date=day)
                db.add(issue)
            # Retain a known version barrier on a later request with a missing timestamp.
            issue.generated_at = stamp or issue.generated_at
            issue.received_at = utcnow()
            issue.code = code
    record_event(db, day, code)
    db.commit()
    raise HTTPException(status, code)


def snapshot_digest(payload: DayPayload) -> str:
    result = hashlib.sha256()
    for row in sorted(payload.rows, key=lambda item: item.line_id):
        data = row.model_dump(mode="json")
        data.update(amount=format(row.amount, ".2f"), quantity=format(row.quantity, ".3f"))
        result.update(json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())
        result.update(b"\n")
    return result.hexdigest()


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def replace_day(db: Session, payload: DayPayload) -> dict:
    day, stamp = payload.business_date, payload.generated_at
    signature = snapshot_digest(payload)
    day_lock(db, day)
    key = ("live", day)
    current = db.get(Snapshot, key)
    issue = db.get(DayIssue, key)
    known = [as_utc(x.generated_at) for x in (current, issue) if x and x.generated_at]
    if any(stamp < version for version in known):
        record_event(db, day, "stale")
        db.commit()
        raise HTTPException(409, "stale")
    same_version = current is not None and stamp == as_utc(current.generated_at)
    if same_version and signature != current.digest:
        if issue is None:
            issue = DayIssue(dataset="live", business_date=day)
            db.add(issue)
        issue.generated_at, issue.received_at, issue.code = stamp, utcnow(), "version_conflict"
        record_event(db, day, "version_conflict")
        db.commit()
        raise HTTPException(409, "version_conflict")
    if issue:
        db.delete(issue)
    status = "unchanged" if same_version else "accepted"
    if not same_version:
        db.execute(delete(Fact).where(Fact.dataset == "live", Fact.business_date == day))
        if payload.rows:
            db.execute(insert(Fact), [dict(dataset="live", **row.model_dump()) for row in payload.rows])
        if current is None:
            current = Snapshot(dataset="live", business_date=day)
            db.add(current)
        current.generated_at = stamp
        current.row_count = payload.row_count
        current.digest = signature
    current.received_at = utcnow()
    record_event(db, day, status, payload.row_count)
    db.commit()
    return {"status": status, "business_date": day, "row_count": payload.row_count}


def handle_payload(db: Session, body: bytearray, day_header: str | None, stamp_header: str | None,
                   read_error: str | None, authorization: str):
    # A stalled client does not hold up revocation. The final key check and
    # snapshot transaction share one lock against key rotation in PostgreSQL.
    access = db.get(SourceAccess, 1, with_for_update={"read": True})
    verify_upload_key(authorization, access)
    day, stamp = parse_date(day_header), parse_instant(stamp_header)
    try:
        if read_error:
            reject_transfer(db, day, stamp, read_error, 413 if read_error == "too_large" else 400)
        try:
            data = json.loads(body.decode("utf-8-sig"), parse_float=Decimal, object_pairs_hook=unique_fields)
            if not isinstance(data, dict):
                raise ValueError
        except (ValueError, RecursionError, DecimalException):
            reject_transfer(db, day, stamp, "invalid_json", 422)
        body_day, body_stamp = parse_date(data.get("business_date")), parse_instant(data.get("generated_at"))
        if (day_header is not None and (not day or day != body_day)) or (
            stamp_header is not None and (not stamp or stamp != body_stamp)
        ):
            reject_transfer(db, day or body_day, stamp or body_stamp, "header_mismatch", 422)
        day, stamp = body_day, body_stamp
        if data.get("complete") is False or isinstance(data.get("rows"), list) and len(data["rows"]) != data.get("row_count"):
            reject_transfer(db, body_day, body_stamp, "incomplete", 422)
        try:
            snapshot = DayPayload.model_validate(data)
        except ValidationError:
            reject_transfer(db, body_day, body_stamp, "invalid_snapshot", 422)
        return replace_day(db, snapshot)
    except SQLAlchemyError:
        db.rollback()
        try:
            # A transaction failure can be transient. Record the gap if storage
            # is reachable after rollback; never include driver parameters.
            reject_transfer(db, day, stamp, "storage_unavailable", 503)
        except SQLAlchemyError:
            db.rollback()
            raise HTTPException(503, "storage_unavailable") from None


@router.post("/api/import/days", dependencies=[Depends(require_upload_token)])
async def import_day(request: Request, db: Session = Depends(get_db)):
    body = bytearray()
    error = None
    try:
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_BYTES:
                error = "too_large"
                break
            body.extend(chunk)
    except ClientDisconnect:
        error = "interrupted"
    return await run_in_threadpool(
        handle_payload, db, body, request.headers.get("x-business-date"),
        request.headers.get("x-snapshot-generated-at"), error, request.headers.get("authorization", ""),
    )
