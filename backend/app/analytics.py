from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import case, distinct, exists, func, select
from sqlalchemy.orm import Session

from .models import DayIssue, Fact, Snapshot
from .plans import DateRange, Filters, Plan, business_today, resolve_ranges
from .report_text import FORMULAS, RU
from .settings import company_timezone

MONEY = Decimal("0.01")
METRICS = tuple(FORMULAS)
GROUPS = {
    "day": (Fact.business_date, None),
    "point": (Fact.store_id, Fact.store_name),
    "product": (Fact.product_id, Fact.product_name),
    "category": (Fact.category_id, Fact.category_name),
}


def cast_value(metric, value):
    if value is None:
        return None
    if metric == "documents":
        return int(value)
    return str(Decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP))


def difference(current, previous, metric, complete):
    change = current - previous
    percent = (
        str((100 * Decimal(change) / Decimal(previous)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
        if complete and previous > 0 else None
    )
    return {
        "value": cast_value(metric, current), "previous": cast_value(metric, previous),
        "delta": cast_value(metric, change) if complete else None, "percent": percent,
    }


def aggregates(db: Session, mode: str, period: DateRange, filters: Filters, group=None):
    sale = Fact.operation == "sale"
    returned = Fact.operation == "return"
    sales = func.coalesce(func.sum(case((sale, Fact.amount), else_=0)), 0)
    returns = func.coalesce(func.sum(case((returned, Fact.amount), else_=0)), 0)
    query = select(
        sales.label("sales"), returns.label("returns"), (sales - returns).label("net"),
        func.count(distinct(case((sale, Fact.document_id)))).label("documents"),
    ).where(
        Fact.dataset == mode, Fact.business_date.between(period.start, period.end),
        ~exists().where(DayIssue.dataset == Fact.dataset, DayIssue.business_date == Fact.business_date),
    )
    if filters.point is not None:
        query = query.where(Fact.store_id == filters.point)
    if filters.category is not None:
        query = query.where(Fact.category_id == filters.category)
    if group == "point_day":
        query = query.add_columns(Fact.store_id, Fact.business_date, func.max(Fact.store_name).label("name")
                                  ).group_by(Fact.store_id, Fact.business_date)
    elif group:
        identifier, label = GROUPS[group]
        query = query.add_columns(identifier.label("id")).group_by(identifier)
        if label is not None:
            query = query.add_columns(func.max(label).label("name"))
    return [dict(row) for row in db.execute(query).mappings()]


def freshness(db: Session, mode: str, today):
    closed = today - timedelta(days=1)
    last = db.scalar(select(func.max(Snapshot.business_date)).where(
        Snapshot.dataset == mode, Snapshot.business_date <= closed, ~exists().where(
            DayIssue.dataset == Snapshot.dataset, DayIssue.business_date == Snapshot.business_date)))
    return {"latest_confirmed_day": last, "lag_days": max(0, (closed - last).days) if last else None}


def choices(db, mode):
    result = {}
    for group, key in (("point", "points"), ("category", "categories")):
        ident, label = GROUPS[group]
        result[key] = [dict(r) for r in db.execute(
            select(ident.label("id"), func.max(label).label("name"))
            .where(Fact.dataset == mode).group_by(ident).order_by(func.max(label), ident)
        ).mappings()]
    return result


def coverage(db, mode, current, previous, metric):
    lower = min(current.start, previous.start) if previous else current.start
    upper = max(current.end, previous.end) if previous else current.end
    snapshots = db.execute(
        select(Snapshot.business_date, Snapshot.received_at, Snapshot.generated_at)
        .where(Snapshot.dataset == mode, Snapshot.business_date.between(lower, upper))
    ).all()
    confirmed = {row.business_date for row in snapshots}
    issues = set(db.scalars(select(DayIssue.business_date).where(
        DayIssue.dataset == mode, DayIssue.business_date.between(lower, upper)
    )))

    def missing(period):
        return [day.isoformat() for day in period.days() if day not in confirmed or day in issues]

    missing_current = missing(current)
    missing_previous = missing(previous) if previous else []
    used_dates = set(current.days()) | (set(previous.days()) if previous else set())
    visible = [row for row in snapshots if row.business_date in used_dates and row.business_date not in issues]

    def latest_stamp(stamps):
        latest = max(stamps, default=None)
        if latest and latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        return latest.isoformat() if latest else None
    return {
        "complete": not missing_current and not missing_previous,
        "current_complete": not missing_current,
        "previous_complete": not missing_previous, "missing_days": missing_current,
        "previous_missing_days": missing_previous, "source": RU["source_" + mode],
        "updated_at": latest_stamp(row.received_at for row in visible),
        "generated_at": latest_stamp(row.generated_at for row in visible),
        "formula": FORMULAS[metric], "formulas": FORMULAS, "timezone": company_timezone(mode),
        "vat": RU["vat"],
    }


def summary_row(row):
    return {metric: cast_value(metric, row[metric]) for metric in METRICS}


def daily_rows(db, mode, dates, filters, meta, previous):
    values = {r["id"]: r for r in aggregates(db, mode, dates, filters, "day")}
    before = {r["id"]: r["net"] for r in aggregates(db, mode, previous, filters, "day")}
    missing = set(meta["missing_days"])
    missing_previous = set(meta["previous_missing_days"])
    days = []
    for day in dates.days():
        covered = day.isoformat() not in missing
        data = values.get(day, dict.fromkeys(METRICS, 0))
        paired = previous.start + (day - dates.start)
        comparable = paired <= previous.end and paired.isoformat() not in missing_previous
        days.append({"date": day.isoformat(), "covered": covered,
                     "previous": cast_value("net", before.get(paired, 0)) if comparable else None,
                     **(summary_row(data) if covered else dict.fromkeys(METRICS))})
    return days


def group_comparison(db, mode, current, previous, filters, group, metric, complete):
    current_rows = {r["id"]: r for r in aggregates(db, mode, current, filters, group)}
    previous_rows = {r["id"]: r for r in aggregates(db, mode, previous, filters, group)}
    zero = dict.fromkeys(METRICS, 0)
    rows = []
    for key in current_rows.keys() | previous_rows.keys():
        row = current_rows.get(key, zero)
        before = previous_rows.get(key, zero)
        rows.append({
            "id": key, "name": row.get("name") or before["name"], **summary_row(row),
            **difference(row[metric], before[metric], metric, complete),
        })
    return rows


def sales_dashboard(db, mode, period, filters, now: datetime, start=None, end=None):
    today = business_today(now, company_timezone(mode))
    current, previous = resolve_ranges(period, today, start, end)
    meta = coverage(db, mode, current, previous, "net")
    meta.update(freshness(db, mode, today))
    latest, before = [aggregates(db, mode, dates, filters)[0] for dates in (current, previous)]
    kpis = {m: difference(latest[m], before[m], m, meta["complete"]) for m in METRICS}
    points = group_comparison(db, mode, current, previous, filters, "point", "net", meta["complete"])
    points.sort(key=lambda p: (-Decimal(p["net"]), p["name"], p["id"]))
    products = sorted(aggregates(db, mode, current, filters, "product"),
                      key=lambda p: (-p["net"], p["name"], p["id"]))
    if filters.point is None:
        products = products[:5]
    return {
        "range": current.json(), "previous_range": previous.json(), "filters": filters.model_dump(),
        "options": choices(db, mode), "kpis": kpis, "daily": daily_rows(db, mode, current, filters, meta, previous),
        "points": points, "products": [{"id": p["id"], "name": p["name"], **summary_row(p)} for p in products],
        "meta": meta,
    }


def format_amount(number):
    value = Decimal(number)
    precision = 0 if value == value.to_integral_value() else 2
    return f"{value:,.{precision}f}".replace(",", " ").replace(".", ",")


def short_answer(plan, rows, meta, total, has_activity):
    if not meta["complete"]:
        return RU["incomplete" if plan.comparison != "none" else "partial"]
    if not has_activity:
        return RU["empty"]
    if plan.comparison == "previous_week":
        minimum = min(Decimal(r["delta"]) for r in rows)
        if minimum >= 0:
            return RU["no_drop"]
        names = [r["name"] for r in rows if Decimal(r["delta"]) == minimum]
        if len(names) == 1:
            return RU["largest_drop"].format(names=names[0], amount=format_amount(-minimum))
        return RU["tied_drop"].format(names=", ".join(f"«{n}»" for n in names), amount=format_amount(-minimum))
    if plan.group_by == "product" and plan.metric == "net" and rows:
        return RU["top"].format(name=rows[0]["name"], amount=format_amount(rows[0]["value"]))
    return RU["total"].format(metric=RU[plan.metric], amount=format_amount(total),
                              unit="" if plan.metric == "documents" else " ₸")


def run_plan(db: Session, mode: str, question: str, plan: Plan, now: datetime):
    today = business_today(now, company_timezone(mode))
    current, prior = resolve_ranges(plan.period, today, plan.start, plan.end)
    previous = prior if plan.comparison == "previous_week" else None
    meta = coverage(db, mode, current, previous, plan.metric)
    totals = aggregates(db, mode, current, plan.filters)[0]
    if previous:
        rows = group_comparison(db, mode, current, previous, plan.filters, plan.group_by, plan.metric, meta["complete"])
        rows.sort(key=lambda r: (Decimal(r["delta"] or 0), r["name"], r["id"]))
        activity = bool(rows)
    else:
        grouped = aggregates(db, mode, current, plan.filters, plan.group_by)
        activity = bool(grouped)
        if plan.group_by == "day":
            values = {r["id"]: r[plan.metric] for r in grouped}
            missing = set(meta["missing_days"])
            grouped = [{"id": d.isoformat(), "name": d.isoformat(),
                        plan.metric: None if d.isoformat() in missing else values.get(d, 0)}
                       for d in current.days()]
        else:
            grouped.sort(key=lambda r: (-r[plan.metric], r["name"], r["id"]))
        rows = [{"id": r["id"], "name": r["name"], "value": cast_value(plan.metric, r[plan.metric]),
                 "previous": None, "delta": None, "percent": None} for r in grouped[:plan.limit]]
    return {
        "question": question, "plan": plan.model_dump(mode="json", exclude={"start", "end"} if plan.period != "custom" else set()),
        "answer": short_answer(plan, rows, meta, totals[plan.metric], activity),
        "rows": rows, "range": current.json(), "previous_range": previous.json() if previous else None,
        "meta": meta,
    }
