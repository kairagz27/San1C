"""Match a client's independently exported control report to complete daily snapshots."""
import csv
import hashlib
import io
import os
import re
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import Field
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from .analytics import aggregates
from .auth import require_live
from .db import get_db
from .models import DayIssue, Fact, Reconciliation, Snapshot, as_utc
from .plans import ClosedModel, DateRange, Filters, business_today, clock_now
from .settings import company_timezone
from .source import integration_profile, missing_setup


router = APIRouter(prefix="/api/live/reconciliation", dependencies=[Depends(require_live)])
METRICS = ("sales", "returns", "net")
ZERO = dict.fromkeys(METRICS, Decimal(0))
HEADERS = ["date", "point_id", *METRICS]
MONEY_PATTERN = re.compile(r"-?[0-9]{1,18}(?:[.,][0-9]{1,2})?")


class ControlReport(ClosedModel):
    start: date
    end: date
    report_name: str = Field(min_length=1, max_length=200)
    point: str | None = Field(default=None, min_length=1, max_length=160)
    csv: str = Field(min_length=1, max_length=256_000)


def control_values(body):
    reader = csv.reader(io.StringIO(body.csv.lstrip("\ufeff"), newline=""), delimiter=";", strict=True)
    rows = {}
    try:
        if next(reader, None) != HEADERS:
            raise HTTPException(422, "CSV должен начинаться с date;point_id;sales;returns;net")
        for fields in reader:
            if not fields:
                continue
            if len(fields) != len(HEADERS):
                raise ValueError
            text_date, point, *amounts = (x.strip() for x in fields)
            day = date.fromisoformat(text_date)
            if (day.isoformat() != text_date or not body.start <= day <= body.end
                    or not point or len(point) > 160 or any(ord(c) < 32 for c in point)
                    or body.point and point != body.point
                    or not all(MONEY_PATTERN.fullmatch(x) for x in amounts)):
                raise ValueError
            values = dict(zip(METRICS, (Decimal(x.replace(",", ".")) for x in amounts)))
            if values["sales"] < 0 or values["returns"] < 0:
                raise ValueError
            if (day, point) in rows:
                raise HTTPException(422, f"Строка {reader.line_num} повторяет дату и точку")
            rows[day, point] = values
    except (csv.Error, ValueError):
        raise HTTPException(422, f"Строка {reader.line_num}: проверьте дату, точку и суммы KZT с точностью до тиына") from None
    if not rows:
        raise HTTPException(422, "В CSV нет контрольных строк. Для закрытого дня без операций укажите нули")
    return rows


def money(values):
    return {key: format(values[key], ".2f") for key in METRICS} if values is not None else None


def comparison(expected, actual):
    delta = None
    status = "incomplete"
    if expected is not None and actual is not None:
        delta = {key: actual[key] - expected[key] for key in METRICS}
        status = "mismatch" if any(delta.values()) else "matched"
    return {"expected": money(expected), "actual": money(actual), "delta": money(delta), "status": status}


def subtotal(rows):
    def summed(side):
        if any(row[side] is None for row in rows):
            return None
        return {key: sum(Decimal(row[side][key]) for row in rows) for key in METRICS}

    result = comparison(summed("expected"), summed("actual"))
    # A positive error on Monday can cancel a negative error on Tuesday.
    if result["status"] == "matched" and any(row["status"] == "mismatch" for row in rows):
        result["status"] = "mismatch"
    return result


def compare(body: ControlReport, db: Session, now: datetime):
    if not 1 <= (body.end - body.start).days + 1 <= 366:
        raise HTTPException(422, "Выберите период от 1 до 366 дней")
    zone = company_timezone("live")
    if body.end >= business_today(now, zone):
        raise HTTPException(422, "Для сверки выберите только завершённые дни компании")
    expected = control_values(body)
    period = DateRange(body.start, body.end)
    # Pin one view across coverage, labels, and all report aggregates.
    db.rollback()
    if db.get_bind().dialect.name == "postgresql":
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    scope = [Fact.dataset == "live", Fact.business_date.between(body.start, body.end)]
    if body.point:
        scope.append(Fact.store_id == body.point)
    points = {point for _, point in expected}
    points.update(db.scalars(select(distinct(Fact.store_id)).where(*scope).limit(3)))
    profile = integration_profile("live")
    mapped = {} if missing_setup(profile) else {p["id"]: p["name"] for p in profile["pilot_points"]}
    if body.point is None:
        points.update(mapped)
    if len(points) > 2:
        raise HTTPException(422, "Для этого пилота выберите одну точку или отчёт по двум точкам")
    names = dict(db.execute(select(Fact.store_id, func.max(Fact.store_name)).where(
        Fact.dataset == "live", Fact.store_id.in_(points)).group_by(Fact.store_id)).all())
    names.update(mapped)
    covered = set(db.scalars(select(Snapshot.business_date).where(
        Snapshot.dataset == "live", Snapshot.business_date.between(body.start, body.end))))
    covered.difference_update(db.scalars(select(DayIssue.business_date).where(
        DayIssue.dataset == "live", DayIssue.business_date.between(body.start, body.end))))
    facts = {(r["business_date"], r["store_id"]): r for r in
             aggregates(db, "live", period, Filters(point=body.point), "point_day")}
    rows, days, by_point = [], [], defaultdict(list)
    for day in period.days():
        day_rows = []
        for point in sorted(points):
            value = facts.get((day, point), ZERO) if day in covered and point in names else None
            row = {"date": day, "point_id": point, "point_name": names.get(point, point),
                   **comparison(expected.get((day, point)), value)}
            day_rows.append(row)
            by_point[point].append(row)
        rows.extend(day_rows)
        days.append({"date": day, **subtotal(day_rows)})
    points_summary = [{"point_id": key, "point_name": names.get(key, key), **subtotal(items)}
                      for key, items in by_point.items()]
    total = subtotal(rows)
    return {"start": body.start, "end": body.end, "report_name": body.report_name, "point": body.point,
            "status": total["status"], "rows": rows, "days": days, "points": points_summary, "total": total,
            "missing_days": [day for day in period.days() if day not in covered],
            "unmapped_points": sorted(points - names.keys()), "control_sha256": hashlib.sha256(body.csv.encode()).hexdigest(),
            "profile_id": profile["id"], "configuration": profile["configuration"], "release": profile["release"],
            "company": os.getenv("COMPANY_NAME", ""), "organization_uuid": profile.get("organization_uuid"),
            "pilot_points": profile.get("pilot_points", []), "currency": "KZT", "category": None,
            "timezone": zone, "tax_basis": profile["tax_basis"]}


def summary(record):
    return {"id": record.id, "start": record.start, "end": record.end, "point": record.point,
            "report_name": record.report_name, "status": record.status, "created_at": as_utc(record.created_at)}


@router.post("/preview")
def preview(body: ControlReport, db: Session = Depends(get_db), now: datetime = Depends(clock_now)):
    return compare(body, db, now)


@router.post("/save", status_code=201)
def save(body: ControlReport, db: Session = Depends(get_db), now: datetime = Depends(clock_now)):
    result = jsonable_encoder(compare(body, db, now))
    record = Reconciliation(report_name=body.report_name, start=body.start, end=body.end, point=body.point,
                            status=result["status"], result=result)
    db.add(record)
    db.commit()
    return {**result, **summary(record)}


@router.get("")
def history(db: Session = Depends(get_db)):
    records = db.execute(select(
        Reconciliation.id, Reconciliation.created_at, Reconciliation.report_name, Reconciliation.start,
        Reconciliation.end, Reconciliation.point, Reconciliation.status,
    ).order_by(Reconciliation.created_at.desc(), Reconciliation.id).limit(100))
    return [summary(item) for item in records]


@router.get("/{record_id}")
def saved(record_id: str, db: Session = Depends(get_db)):
    record = db.get(Reconciliation, record_id)
    if not record:
        raise HTTPException(404, "Сохранённая сверка не найдена")
    return {**record.result, **summary(record)}
