"""The closed vocabulary accepted by the reporting engine."""

import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

Metric = Literal["sales", "returns", "net", "documents"]
Period = Literal["last_30_days", "this_month", "last_month", "last_week", "custom"]
Group = Literal["day", "point", "product", "category"]
Key = Annotated[str, Field(min_length=1, max_length=160)]
DAY = timedelta(days=1)


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Filters(ClosedModel):
    point: Key | None = None
    category: Key | None = None


class Plan(ClosedModel):
    metric: Metric
    period: Period
    group_by: Group
    filters: Filters = Field(default_factory=Filters)
    comparison: Literal["none", "previous_week"] = "none"
    view: Literal["line", "bar", "table"]
    limit: Literal[5] | None = None
    start: date | None = None
    end: date | None = None

    @model_validator(mode="after")
    def validate_semantics(self):
        validate_dates(self.period, self.start, self.end)
        if self.comparison == "previous_week" and (
            self.period != "last_week" or self.group_by != "point" or self.metric != "net"
        ):
            raise ValueError("Недельное сравнение доступно для выручки по точкам.")
        if self.limit is not None and self.group_by != "product":
            raise ValueError("Ограничение топ-5 доступно для товаров.")
        if self.view == "line" and self.group_by != "day":
            raise ValueError("Линейный график доступен для разбивки по дням.")
        return self


def validate_dates(period: str, start: date | None, end: date | None):
    if period == "custom":
        if not start or not end or start > end:
            raise ValueError("Укажите обе границы периода в порядке возрастания.")
        if (end - start).days >= 366:
            raise ValueError("Выберите период до 366 дней.")
        if (end - start).days + 1 >= start.toordinal():
            raise ValueError("Предыдущий период выходит за границы календаря.")
    elif start is not None or end is not None:
        raise ValueError("Даты вручную задаются для произвольного периода.")


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date

    def days(self):
        return (self.start + timedelta(days=i) for i in range((self.end - self.start).days + 1))

    def json(self):
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}


def clock_now():
    return datetime.now(timezone.utc)


def resolve_ranges(period: Period, today: date, start=None, end=None):
    validate_dates(period, start, end)
    if period == "last_week":
        end = today - timedelta(days=today.weekday() + 1)
        start = end - 6 * DAY
    elif period == "last_30_days":
        start, end = today - 29 * DAY, today
    elif period in ("this_month", "last_month"):
        first = today.replace(day=1)
        previous_end = first - DAY
        if period == "this_month":
            previous = previous_end.replace(day=min(today.day, monthrange(previous_end.year, previous_end.month)[1]))
            return DateRange(first, today), DateRange(previous.replace(day=1), previous)
        before = previous_end.replace(day=1) - DAY
        return DateRange(previous_end.replace(day=1), previous_end), DateRange(before.replace(day=1), before)
    length = (end - start) + DAY
    return DateRange(start, end), DateRange(start - length, start - DAY)


def business_today(now: datetime, zone: str):
    return now.astimezone(ZoneInfo(zone)).date()


def parse_question(question: str, filters: Filters) -> Plan | None:
    # Anchor the whole utterance. Extra clauses must never silently disappear.
    normalized = " ".join(question.lower().replace("ё", "е").strip(" !?.").split())
    prefix = r"(?:(?:покажи(?:те)?|выведи(?:те)?) )?"
    examples = [
        (prefix + r"выручку по дням за последние 30 дней",
         ("net", "last_30_days", "day", "none", "line", None)),
        (r"где сильнее всего (?:упала|снизилась) выручка за (?:прошлую|предыдущую) неделю",
         ("net", "last_week", "point", "previous_week", "bar", None)),
        (r"какие (?:5|пять) товаров дали больше всего выручки за (?:этот|текущий) месяц",
         ("net", "this_month", "product", "none", "bar", 5)),
        (prefix + r"возвраты по (?:точкам|магазинам) за (?:прошлый|предыдущий) месяц",
         ("returns", "last_month", "point", "none", "bar", None)),
    ]
    for pattern, fields in examples:
        if re.fullmatch(pattern, normalized):
            metric, period, group, comparison, view, limit = fields
            return Plan(metric=metric, period=period, group_by=group, comparison=comparison,
                        filters=filters, view=view, limit=limit)
    return None
