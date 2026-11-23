from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import analytics, auth, ingestion
from app.analytics import run_plan, sales_dashboard
from app.auth import access_dataset
from app.db import get_db
from app.demo import seed_demo
from app.ingestion import DayPayload, replace_day
from app.models import DayIssue, Fact, Snapshot, Widget
from app.plans import DAY, Filters, Plan, business_today, clock_now, parse_question, resolve_ranges
from app.reports import router

NOW = datetime(2026, 3, 15, 20, 15, tzinfo=timezone.utc)  # Monday 16 March in Almaty
DROP = "Где сильнее всего упала выручка за прошлую неделю?"
TREND = "Покажи выручку по дням за последние 30 дней"
TOP = "Какие 5 товаров дали больше всего выручки за этот месяц?"
RETURNS = "Покажи возвраты по точкам за прошлый месяц"
START = date(2026, 3, 9)


@pytest.fixture
def db(test_engine):
    with Session(test_engine) as db:
        yield db


def confirm(db, first, last):
    day = first
    while day <= last:
        db.add(Snapshot(dataset="live", business_date=day, generated_at=NOW, received_at=NOW,
                        row_count=0, digest="0" * 64))
        day += DAY


def line(db, day, key, amount, operation="sale", point="shop", category="food", document="a"):
    db.add(Fact(dataset="live", business_date=day, line_id=key, document_id=document,
                store_id=point, store_name=point, product_id=key, product_name=key,
                category_id=category, category_name=category, operation=operation,
                quantity=Decimal("1.0"), amount=Decimal(amount)))


def weekly(db, now=NOW, filters=None):
    return run_plan(db, "live", DROP, parse_question(DROP, filters or Filters()), now)


def test_completed_week_at_company_midnight_leap_month_and_custom_boundaries():
    local = business_today(NOW, "Asia/Almaty")
    assert local == date(2026, 3, 16)
    week, before = resolve_ranges("last_week", local)
    assert (week.start, week.end) == (START, date(2026, 3, 15))
    assert (before.start, before.end) == (date(2026, 3, 2), date(2026, 3, 8))
    assert resolve_ranges("last_week", local - DAY)[0] == before
    feb, jan = resolve_ranges("last_month", date(2024, 3, 31))
    assert feb.end == date(2024, 2, 29) and jan.end == date(2024, 1, 31)
    today, prev = resolve_ranges("this_month", date(2025, 3, 31))
    assert prev.end == date(2025, 2, 28)
    current, prior = resolve_ranges("custom", today.end, START, START)
    assert current.start == current.end == START and prior.end == START - DAY
    with pytest.raises(ValueError):
        resolve_ranges("custom", local, START, START - DAY)


def test_shared_filters_money_documents_return_date_and_no_percentage_base(db):
    confirm(db, START - 7 * DAY, START + 6 * DAY)
    line(db, START - DAY, "prev", "800.40")
    line(db, START, "one", "1000.30")
    line(db, START, "two", "250.20")  # another line of the same sale
    line(db, START + DAY, "refund", "50.25", "return")
    line(db, START, "excluded_cat", "4000", category="technology", document="b")
    line(db, START + DAY, "excluded_store", "8000", point="other")
    line(db, START + 7 * DAY, "excluded_next_monday", "5000")
    filtered = Filters(point="shop", category="food")
    result = sales_dashboard(db, "live", "last_week", filtered, NOW)
    kpis = result["kpis"]
    assert [kpis[k]["value"] for k in ["sales", "returns", "net", "documents"]] == ["1250.50", "50.25", "1200.25", 1]
    assert kpis["net"]["delta"] == "399.85"
    assert kpis["returns"]["percent"] is None
    assert result["daily"][0]["returns"] == "0.00"
    assert result["daily"][1]["net"] == "-50.25"
    assert result["daily"][6]["previous"] == "800.40"
    assert result["points"][0]["net"] == "1200.25"
    assert {r["id"] for r in result["products"]} == {"one", "two", "refund"}
    assert weekly(db, filters=filtered)["rows"][0]["delta"] == kpis["net"]["delta"]


def test_closed_store_is_in_comparison_negative_base_and_partial_coverage(db):
    confirm(db, START - 7 * DAY, START + 6 * DAY)
    line(db, START - 2 * DAY, "prev", "500", point="closed")
    line(db, START - 2 * DAY, "debt", "10", "return", point="negative")
    line(db, START, "now", "300", point="negative")
    result = weekly(db)
    assert result["meta"]["complete"] and "closed" in result["answer"]
    assert [(r["id"], r["delta"], r["percent"]) for r in result["rows"]] == [
        ("closed", "-500.00", "-100.0"), ("negative", "310.00", None)]
    db.add(DayIssue(dataset="live", business_date=START + DAY, generated_at=NOW, received_at=NOW, code="incomplete"))
    partial = weekly(db)
    assert not partial["meta"]["complete"] and partial["meta"]["missing_days"] == ["2026-03-10"]
    assert "closed" not in partial["answer"] and partial["rows"][0]["delta"] is None
    db.execute(delete(DayIssue))
    db.execute(delete(Snapshot).where(Snapshot.business_date == START - DAY))
    missing_prev = weekly(db)
    assert missing_prev["meta"]["previous_missing_days"] == ["2026-03-08"]
    assert not missing_prev["meta"]["complete"] and "снизилась" not in missing_prev["answer"]


def test_invalidated_day_is_a_gap_in_all_aggregates_while_last_snapshot_is_retained(db):
    confirm(db, START - 7 * DAY, START + 6 * DAY)
    line(db, START, "valid", "80")
    line(db, START + DAY, "changed", "500")
    db.add(DayIssue(dataset="live", business_date=START + DAY, generated_at=NOW, code="incomplete"))
    report = sales_dashboard(db, "live", "last_week", Filters(), NOW)
    assert report["kpis"]["net"]["value"] == report["points"][0]["net"] == "80.00"
    assert report["daily"][1]["net"] is None
    assert [r["id"] for r in report["products"]] == ["valid"]
    assert db.get(Fact, ("live", START + DAY, "changed")).amount == Decimal(500)


def test_confirmed_empty_day_vs_missing_snapshot(db):
    confirm(db, START - 7 * DAY, START + 6 * DAY)
    assert weekly(db)["answer"].startswith("За выбранный период операций")
    q = parse_question(TREND, Filters())
    report = run_plan(db, "live", TREND, q, NOW)
    observed = {r["id"]: r["value"] for r in report["rows"]}
    assert observed["2026-03-09"] == "0.00" and observed["2026-03-16"] is None
    assert not report["meta"]["complete"]


def test_catalog_rejects_unsupported_plans_and_questions():
    for question in (DROP, TREND, TOP, RETURNS):
        assert parse_question(question, Filters()) is not None
    for question in ("Покажи валовую прибыль", TREND + " и остатки"):
        assert parse_question(question, Filters()) is None
    raw = parse_question(TOP, Filters()).model_dump()
    for bad in ({"group_by": "buyer"}, {"metric": "margin"}, {"sql": "anything"},
                {"comparison": "previous_week"}, {"limit": 300}, {"start": START}):
        with pytest.raises(ValidationError):
            Plan.model_validate(raw | bad)


def test_api_demo_widget_persistence_isolation_relative_rollover_and_csv(test_engine):
    app = FastAPI()
    app.include_router(router)
    with Session(test_engine) as session:
        seed_demo(session, NOW)
    def sessions():
        with Session(test_engine) as session:
            yield session
    app.dependency_overrides[get_db] = sessions
    app.dependency_overrides[clock_now] = lambda: NOW
    with TestClient(app) as client:
        assert client.get("/api/live/sales").status_code == 401
        for question in (TREND, DROP, TOP, RETURNS):
            reply = client.post("/api/demo/ask", json={"question": question}).json()
            assert "answer" in reply and reply["rows"] and reply["meta"]["complete"]
        reply = client.post("/api/demo/ask", json={"question": DROP}).json()
        saved_body = {"question": DROP, "plan": reply["plan"]}
        saved = client.post("/api/demo/widgets", json=saved_body)
        assert saved.status_code == 201
        widget_id = saved.json()["id"]
        assert client.post("/api/demo/widgets", json=saved_body).json()["id"] == widget_id
        with TestClient(app) as stranger:
            assert stranger.get("/api/demo/widgets").json() == []
        assert client.get("/api/demo/widgets").json()[0]["answer"] == reply["answer"]
        app.dependency_overrides[clock_now] = lambda: NOW + timedelta(days=7)
        refreshed = client.get("/api/demo/widgets").json()[0]
        assert refreshed["id"] == widget_id and refreshed["range"]["start"] == "2026-03-16"
        assert refreshed["meta"]["complete"] and refreshed["plan"]["period"] == "last_week"
        keys = {r["id"] for r in refreshed["rows"]}
        point = next(iter(keys))
        filtered = client.get("/api/demo/sales", params={"point": point}).json()
        assert [r["id"] for r in filtered["points"]] == [point]
        csv = client.get("/api/demo/sales.csv", params={"point": point})
        assert csv.status_code == 200 and len(csv.text.splitlines()) == 2
        exported = client.post("/api/demo/report.csv", json={"plan": refreshed["plan"]})
        assert "Изменение, %" in exported.text and len(exported.text.splitlines()) == len(keys) + 1
        assert client.post("/api/demo/report.csv", json={"plan": {**saved_body["plan"], "sql": "anything"}}).status_code == 422
        assert "clarification" in client.post("/api/demo/ask", json={"question": "Остатки?"}).json()
        with Session(test_engine) as session:
            widgets = list(session.scalars(select(Widget)))
            assert len(widgets) == 1 and "start" not in widgets[0].plan


def test_postgres_dashboard_reads_one_version_across_day_replacement(test_engine, monkeypatch):
    if test_engine.dialect.name != "postgresql":
        pytest.skip("Проверка PostgreSQL REPEATABLE READ")
    def replace(amount, timestamp):
        data = dict(business_date=START.isoformat(), line_id="row", document_id="doc",
                    store_id="one", store_name="One", product_id="item", product_name="Item",
                    category_id="food", category_name="Food", operation="sale", quantity="1", amount=amount)
        payload = DayPayload.model_validate({"business_date": START.isoformat(), "generated_at": timestamp.isoformat(),
                                             "row_count": 1, "complete": True, "rows": [data]})
        with Session(test_engine) as writer:
            replace_day(writer, payload)

    replace("10.00", NOW)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[access_dataset] = lambda: "live"
    app.dependency_overrides[clock_now] = lambda: NOW
    def sessions():
        with Session(test_engine) as session:
            yield session
    app.dependency_overrides[get_db] = sessions
    read = analytics.aggregates
    committed = False
    def read_during_sync(*args, **kwargs):
        nonlocal committed
        result = read(*args, **kwargs)
        if not committed:
            replace("77.00", NOW + DAY)
            committed = True
        return result
    monkeypatch.setattr(analytics, "aggregates", read_during_sync)
    with TestClient(app) as client:
        for expected in ("10.00", "77.00"):
            report = client.get("/api/live/sales", params={"period": "custom", "start": START, "end": START}).json()
            assert [report["kpis"]["net"]["value"], report["daily"][0]["net"],
                    report["points"][0]["net"], report["products"][0]["net"]] == [expected] * 4
    assert committed


def test_import_to_live_question_saved_widget_recomputes_after_correction(test_engine, monkeypatch):
    import secrets
    password, token = secrets.token_urlsafe(18), secrets.token_urlsafe(32)
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", auth.password_hash(password))
    monkeypatch.setenv("UPLOAD_TOKEN_SHA256", auth.digest(token))
    app = FastAPI()
    for module in (router, auth.router, ingestion.router):
        app.include_router(module)
    def sessions():
        with Session(test_engine) as session:
            yield session
    app.dependency_overrides[get_db] = sessions
    app.dependency_overrides[clock_now] = lambda: NOW
    with TestClient(app, base_url="https://localhost") as client:
        def upload(day, amount, version=0):
            rows = []
            if amount is not None:
                rows.append(dict(business_date=day.isoformat(), document_id="doc", line_id="doc/1",
                                 product_id="p", product_name="Кофе", category_id="c", category_name="Напитки",
                                 store_id="a", store_name="Центр", operation="sale", amount=amount, quantity="1"))
            payload = {"business_date": day.isoformat(), "generated_at": (NOW + version * DAY).isoformat(),
                       "complete": True, "row_count": len(rows), "rows": rows}
            assert client.post("/api/import/days", json=payload,
                               headers={"Authorization": "Bearer " + token}).status_code == 200
        for i in range(-7, 7):
            upload(START + i * DAY, {-7: "500", 0: "200"}.get(i))
        assert client.get("/api/live/widgets").status_code == 401
        assert client.post("/api/auth/login", json={"password": password}).status_code == 200
        before = client.post("/api/live/ask", json={"question": DROP}).json()
        assert before["rows"][0]["delta"] == "-300.00"
        assert before["meta"]["complete"] and "на 300 ₸" in before["answer"]
        widget = client.post("/api/live/widgets", json={"question": DROP, "plan": before["plan"]}).json()
        upload(START, "550", version=1)
        after = client.get("/api/live/widgets").json()[0]
        assert after["id"] == widget["id"] and after["rows"][0]["delta"] == "50.00"
        assert after["question"] == DROP and "не снизилась" in after["answer"]
