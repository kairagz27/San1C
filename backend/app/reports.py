"""HTTP surface for the built-in, read-only reporting catalog."""

import csv
import hashlib
import io
import json
import re
import secrets
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .analytics import choices, run_plan, sales_dashboard
from .auth import access_dataset
from .db import get_db
from .demo import seed_demo
from .models import Widget
from .plans import ClosedModel, Filters, Period, Plan, clock_now, parse_question, validate_dates
from .report_text import RU

router = APIRouter(prefix="/api/{mode}", tags=["reports"])
ModeDep = Annotated[str, Depends(access_dataset)]
TimeDep = Annotated[datetime, Depends(clock_now)]
DbDep = Annotated[Session, Depends(get_db)]
DEMO_COOKIE = "san1c_demo"


def report_session(mode: ModeDep, now: TimeDep, db: DbDep):
    if mode == "demo":
        seed_demo(db, now)
    # Finish the auth lookup / demo day check, then pin one snapshot on the same session.
    db.rollback()
    if db.get_bind().dialect.name == "postgresql":
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    return db


ReportDb = Annotated[Session, Depends(report_session)]


class Question(ClosedModel):
    question: str = Field(min_length=1, max_length=500)
    filters: Filters = Field(default_factory=Filters)


class CheckedPlan(ClosedModel):
    plan: Plan


class SaveWidget(CheckedPlan):
    question: str = Field(min_length=1, max_length=500)


def sales_params(
    period: Period = "last_30_days", start: date | None = None, end: date | None = None,
    point: str | None = Query(default=None, min_length=1, max_length=160),
    category: str | None = Query(default=None, min_length=1, max_length=160),
):
    try:
        validate_dates(period, start, end)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return dict(period=period, start=start, end=end, filters=Filters(point=point, category=category))


@router.get("/options")
def options(mode: ModeDep, db: ReportDb):
    return choices(db, mode)


@router.get("/sales")
def sales(mode: ModeDep, db: ReportDb, now: TimeDep, params: dict = Depends(sales_params)):
    return sales_dashboard(db, mode, now=now, **params)


@router.post("/ask")
def ask(body: Question, mode: ModeDep, db: ReportDb, now: TimeDep):
    plan = parse_question(body.question, body.filters)
    if plan is None:
        return {"clarification": RU["clarification"]}
    return run_plan(db, mode, body.question, plan, now)


def widget_owner(mode: str, request: Request, response: Response, create: bool):
    if mode == "live":
        return "company"
    cookie = request.cookies.get(DEMO_COOKIE, "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", cookie):
        if not create:
            return None
        cookie = secrets.token_urlsafe(32)
        response.set_cookie(DEMO_COOKIE, cookie, max_age=365 * 86400, httponly=True,
                            secure=request.url.scheme == "https", samesite="strict", path="/api/demo")
    return hashlib.sha256(cookie.encode()).hexdigest()


@router.get("/widgets")
def widgets(request: Request, response: Response, mode: ModeDep, db: ReportDb, now: TimeDep):
    owner = widget_owner(mode, request, response, False)
    if owner is None:
        return []
    saved = db.scalars(select(Widget).where(Widget.dataset == mode, Widget.owner == owner)
                       .order_by(Widget.created_at.desc(), Widget.id))
    return [{"id": w.id, **run_plan(db, mode, w.question, Plan.model_validate(w.plan), now)} for w in saved]


@router.post("/widgets", status_code=201)
def save_widget(body: SaveWidget, request: Request, response: Response, mode: ModeDep,
                db: ReportDb, now: TimeDep):
    if parse_question(body.question, body.plan.filters) != body.plan:
        raise HTTPException(422, "Вопрос и параметры должны соответствовать проверенному плану.")
    report = run_plan(db, mode, body.question, body.plan, now)
    db.rollback()  # Release the read snapshot before the idempotent widget write.
    owner = widget_owner(mode, request, response, True)
    signature = json.dumps([mode, owner, body.question, report["plan"]], sort_keys=True, ensure_ascii=False)
    widget_id = str(uuid5(NAMESPACE_URL, signature))
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    db.execute(insert(Widget).values(id=widget_id, dataset=mode, owner=owner, question=body.question,
                                     plan=report["plan"]).on_conflict_do_nothing(index_elements=["id"]))
    db.commit()
    return {"id": widget_id, **report}


def export_csv(name, headers, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, delimiter=";")
    writer.writerow(headers)
    writer.writerows(rows)
    return Response("\ufeff" + stream.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


def csv_label(value: str):
    # Spreadsheet programs may interpret user-controlled product/store names as formulas.
    if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value


@router.get("/sales.csv")
def sales_csv(mode: ModeDep, db: ReportDb, now: TimeDep, table: Literal["points", "products", "daily"] = "points",
              product_view: Literal["top5", "all"] = "top5", params: dict = Depends(sales_params)):
    report = sales_dashboard(db, mode, now=now, **params)
    dim = {"points": "point", "products": "product", "daily": "day"}[table]
    cols = {
        "daily": ["sales", "returns", "net", "documents"],
        "points": ["net", "delta", "percent"],
        "products": ["net"],
    }[table]
    rows = []
    visible = report[table][:5] if table == "products" and product_view == "top5" else report[table]
    for row in visible:
        label = csv_label(row.get("name", row.get("date", "")))
        values = [row[c] for c in cols]
        if table == "points" and row["percent"] is None:
            values[-1] = RU["no_base"] if report["meta"]["complete"] else RU["no_comparison"]
        rows.append([label, *values])
    return export_csv(f"sales-{table}-{report['range']['start']}-{report['range']['end']}",
                      [RU[dim], *(RU[c] for c in cols)], rows)


@router.post("/report.csv")
def report_csv(body: CheckedPlan, mode: ModeDep, db: ReportDb, now: TimeDep):
    report = run_plan(db, mode, "", body.plan, now)
    headers = [RU[body.plan.group_by], RU[body.plan.metric]]
    compared = body.plan.comparison != "none"
    if compared:
        headers.extend(RU[c] for c in ("previous", "delta", "percent"))
    rows = []
    for row in report["rows"]:
        values = [csv_label(row["name"]), row["value"]]
        if compared:
            no_percent = RU["no_base"] if report["meta"]["complete"] else RU["no_comparison"]
            values.extend([row["previous"], row["delta"], row["percent"] or no_percent])
        rows.append(values)
    return export_csv(f"report-{report['range']['start']}-{report['range']['end']}", headers, rows)
