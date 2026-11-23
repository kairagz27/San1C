from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app import auth, ingestion, reconciliation, source
from app.db import get_db
from app.models import DayIssue, Fact, Snapshot, SyncEvent, WebSession
from app.plans import clock_now


DAY = "2026-03-16"
DAY_DATE = date.fromisoformat(DAY)
ORIGINAL = "2026-03-17T04:00:00Z"
CORRECTED = "2026-03-18T04:00:00Z"


@pytest.fixture
def storage(test_engine, monkeypatch):
    sessions = sessionmaker(bind=test_engine, expire_on_commit=False)
    app = FastAPI()
    for module in (auth, ingestion, source, reconciliation):
        app.include_router(module.router)

    def isolated_db():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = isolated_db
    app.dependency_overrides[clock_now] = lambda: datetime(2026, 11, 23, 6, tzinfo=timezone.utc)
    token = uuid4().hex
    monkeypatch.setenv("UPLOAD_TOKEN_SHA256", auth.digest(token))
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
    monkeypatch.delenv("OWNER_PASSWORD_HASH", raising=False)
    with TestClient(app, base_url="https://localhost") as client:
        yield client, sessions, {"Authorization": "Bearer " + token}


def row(line="doc/1", amount="350.75", operation="sale"):
    return dict(
        business_date=DAY, line_id=line, document_id=line.split("/")[0],
        store_id="s1", store_name="Центр", category_id="c1", category_name="Дом",
        product_id="p1", product_name="Чай", operation=operation, amount=amount, quantity="1.000",
    )


def body(stamp=ORIGINAL, rows=None):
    lines = [row()] if rows is None else rows
    return dict(business_date=DAY, generated_at=stamp, complete=True, row_count=len(lines), rows=lines)


def test_replace_retry_correct_unpost_and_stale(storage):
    client, sessions, headers = storage

    def post(payload):
        return client.post("/api/import/days", headers=headers, json=payload)

    assert post(body()).json()["status"] == "accepted"
    assert post(body(rows=[row(amount="350.750")])).json()["status"] == "unchanged"
    fixed = body(CORRECTED, [row(amount="20.30"), row("ret/1", "2.33", "return")])
    assert post(fixed).status_code == 200
    assert post(body()).status_code == 409
    with sessions() as db:
        assert sorted(db.scalars(select(Fact.amount))) == [Decimal("2.33"), Decimal("20.30")]
        assert db.get(Snapshot, ("live", DAY_DATE)).row_count == 2
        assert db.scalar(select(func.count()).select_from(DayIssue)) == 0
    assert post(body("2026-03-19T04:00:00Z", [])).status_code == 200
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Fact)) == 0
        assert db.get(Snapshot, ("live", DAY_DATE)).row_count == 0


@pytest.mark.parametrize("change,code", [
    (lambda p: p.update(complete=False), "incomplete"),
    (lambda p: p.update(row_count=2), "incomplete"),
    (lambda p: p["rows"][0].update(business_date="2026-03-15"), "invalid_snapshot"),
    (lambda p: p.update(rows=[row(), row()], row_count=2), "invalid_snapshot"),
    (lambda p: p["rows"][0].update(amount="-21"), "invalid_snapshot"),
    (lambda p: p["rows"][0].update(amount="15.998"), "invalid_snapshot"),
    (lambda p: p["rows"][0].update(customer="private name"), "invalid_snapshot"),
    (lambda p: p["rows"][0].update(amount="10000000000000000"), "invalid_snapshot"),
    (lambda p: p["rows"][0].update(store_name="bad\0text"), "invalid_snapshot"),
])
def test_partial_transfer_preserves_last_day_and_marks_gap(storage, change, code):
    client, sessions, headers = storage
    assert client.post("/api/import/days", headers=headers, json=body()).is_success
    bad = body(CORRECTED)
    change(bad)
    result = client.post("/api/import/days", headers=headers, json=bad)
    assert result.status_code == 422
    assert result.json()["detail"] == code
    with sessions() as db:
        assert db.scalar(select(Fact.amount)) == Decimal("350.75")
        issue = db.get(DayIssue, ("live", DAY_DATE))
        assert issue.code == code
        assert list(db.scalars(select(SyncEvent.code).order_by(SyncEvent.id))) == ["accepted", code]
    assert client.post("/api/import/days", headers=headers, json=body()).status_code == 409
    assert client.post("/api/import/days", headers=headers, json=body(CORRECTED)).is_success
    with sessions() as db:
        assert db.get(DayIssue, ("live", DAY_DATE)) is None


def test_equal_version_conflict(storage):
    client, sessions, headers = storage
    assert client.post("/api/import/days", headers=headers, json=body()).is_success
    assert client.post("/api/import/days", headers=headers, json=body(rows=[row(amount="4")])).status_code == 409
    with sessions() as db:
        assert db.scalar(select(Fact.amount)) == Decimal("350.75")
        assert db.get(DayIssue, ("live", DAY_DATE)).code == "version_conflict"


def test_failed_write_rolls_back_the_whole_day(storage, test_engine):
    client, sessions, headers = storage
    assert client.post("/api/import/days", headers=headers, json=body()).is_success

    def fail_fact_write(conn, cursor, statement, params, context, executemany):
        if statement.startswith("INSERT INTO facts"):
            raise OperationalError(statement, None, RuntimeError("test transaction interruption"))

    event.listen(test_engine, "before_cursor_execute", fail_fact_write)
    try:
        corrected = body(CORRECTED, [row(amount="88.12")])
        response = client.post("/api/import/days", headers=headers, json=corrected)
        assert response.status_code == 503
    finally:
        event.remove(test_engine, "before_cursor_execute", fail_fact_write)
    with sessions() as db:
        assert db.scalar(select(Fact.amount)) == Decimal("350.75")
        assert db.get(DayIssue, ("live", DAY_DATE)).code == "storage_unavailable"
    assert client.post("/api/import/days", headers=headers, json=corrected).is_success


def test_size_limits_and_corrupt_stream_do_not_replace(storage, monkeypatch):
    client, sessions, headers = storage
    assert client.post("/api/import/days", headers=headers, json=body()).is_success
    monkeypatch.setattr(ingestion, "MAX_BYTES", 100)
    headers.update({"X-Business-Date": DAY, "X-Snapshot-Generated-At": CORRECTED})
    assert client.post("/api/import/days", headers=headers, content=bytes(101)).status_code == 413
    assert client.post("/api/import/days", headers=headers, content=b'{"unexpected":').status_code == 422
    assert client.post("/api/import/days", headers=headers, content=b'{"complete":false,"complete":true}').status_code == 422
    with sessions() as db:
        assert db.scalar(select(Fact.amount)) == Decimal("350.75")
        assert db.get(DayIssue, ("live", DAY_DATE)).code == "invalid_json"


def test_parallel_replacements(storage):
    client, sessions, headers = storage

    def send(version):
        payload = body(f"2026-03-17T04:00:0{version}Z", [row(amount=str(version))])
        return client.post("/api/import/days", headers=headers, json=payload).status_code

    with ThreadPoolExecutor(max_workers=6) as executor:
        assert set(executor.map(send, (2, 4, 6, 1, 5, 3))) <= {200, 409}
    with sessions() as db:
        assert list(db.scalars(select(Fact.amount))) == [Decimal(6)]
        assert db.scalar(select(func.count()).select_from(Snapshot)) == 1


def test_auth_data_isolation_token_hashes_and_source(storage, monkeypatch):
    client, sessions, upload = storage
    password = uuid4().hex
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", auth.password_hash(password))
    assert client.get("/api/live/source", headers=upload).status_code == 401
    assert client.post("/api/import/days", json=body()).status_code == 401
    assert client.post("/api/import/days", json=body(), headers=upload).is_success
    assert client.post("/api/auth/login", json={"password": "incorrect"}).status_code == 401
    login = client.post("/api/auth/login", json={"password": password})
    assert login.json() == {"authenticated": True, "configured": True, "role": "owner"}
    assert all(flag in login.headers["set-cookie"].lower() for flag in ["httponly", "secure", "samesite=strict"])
    assert client.post("/api/auth/logout", headers={"Origin": "https://other.example"}).status_code == 403
    with sessions() as db:
        assert db.scalar(select(WebSession.digest)) == auth.digest(client.cookies.get(auth.COOKIE))
    assert client.post("/api/import/days", json=body()).status_code == 401
    window = {"start": DAY, "end": "2026-03-17"}
    live = client.get("/api/live/source", params=window).json()
    assert live["coverage"]["confirmed"] == 1
    assert live["coverage"]["missing"] == ["2026-03-17"]
    assert live["last_success_at"] and live["logs"][0]["code"] == "accepted"
    demo = client.get("/api/demo/source", params=window).json()
    assert demo["coverage"]["confirmed"] == 0 and demo["profile"]["id"] == "demo"
    assert all(log["business_date"] != DAY for log in demo["logs"])
    assert demo["last_success_at"] == "2026-11-23T06:00:00+00:00"
    bad = body(CORRECTED)
    bad["complete"] = False
    assert client.post("/api/import/days", json=bad, headers=upload).status_code == 422
    live = client.get("/api/live/source", params=window).json()
    assert live["coverage"]["days"][0]["status"] == "incomplete"
    assert live["coverage"]["first_confirmed"] is None
    assert client.post("/api/import/days", json=body(CORRECTED, []), headers=upload).is_success
    assert client.get("/api/live/source", params=window).json()["coverage"]["days"][0]["status"] == "empty"
    assert client.post("/api/auth/logout").is_success
    assert client.get("/api/live/source").status_code == 401
    assert not client.get("/api/auth/session").json()["authenticated"]


def test_source_roles_key_revocation_and_profile_check(storage, monkeypatch):
    from pathlib import Path
    from app.models import SourceAccess

    client, sessions, upload = storage
    owner, admin = uuid4().hex, uuid4().hex
    monkeypatch.setenv("OWNER_PASSWORD_HASH", auth.password_hash(owner))
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", auth.password_hash(admin))
    assert client.post("/api/auth/login", json={"password": admin, "role": "owner"}).status_code == 401
    assert client.post("/api/auth/login", json={"password": owner, "role": "owner"}).json()["role"] == "owner"
    for action in ("disable", "rotate"):
        assert client.post("/api/live/source/" + action).status_code == 403
    assert client.post("/api/auth/login", json={"password": admin, "role": "admin"}).json()["role"] == "admin"
    forbidden = client.post("/api/live/source/disable", headers={"Origin": "https://elsewhere.example"})
    assert forbidden.status_code == 403
    assert client.post("/api/live/source/disable").json() == {"enabled": False}
    assert client.get("/api/import/check", headers=upload).status_code == 401
    assert client.post("/api/import/days", headers=upload, json=body()).status_code == 401
    value = client.post("/api/live/source/rotate").json()["upload_token"]
    replacement = {"Authorization": "Bearer " + value}
    assert client.get("/api/import/check", headers=upload).status_code == 401
    monkeypatch.setenv("INTEGRATION_PROFILE", "/missing/profile.json")
    assert client.get("/api/import/check", headers=replacement).status_code == 503
    base = Path(__file__).resolve().parents[2] / "integrations/1c/profiles/educational"
    monkeypatch.setenv("INTEGRATION_PROFILE", str(base / "profile.json"))
    missing = client.get("/api/import/check", headers=replacement)
    assert missing.status_code == 503 and "UUID" in missing.json()["detail"]
    monkeypatch.setenv("INTEGRATION_PROFILE", str(base / "experiment.json"))
    check = client.get("/api/import/check", headers=replacement)
    assert check.status_code == 200 and check.json()["profile_id"] == "san1c-file-lab-v1"
    state = client.get("/api/live/source").json()
    assert state["connection"]["enabled"] and state["connection"]["last_check_status"] == "ok"
    assert value not in str(state) and auth.digest(value) not in str(state)
    with sessions() as db:
        assert db.get(SourceAccess, 1).token_hash == auth.digest(value)
    assert client.post("/api/import/days", json=body(), headers=replacement).status_code == 200


def test_saved_control_detects_offsetting_daily_errors_and_survives_reimport(storage, monkeypatch):
    client, _, upload = storage
    url = "/api/live/reconciliation"
    control = {"start": DAY, "end": "2026-03-17", "report_name": "Контроль по дням", "point": None,
               "csv": "date;point_id;sales;returns;net\n2026-03-16;s1;90,50;0;90,50\n2026-03-17;s1;110.50;0;110.50"}
    assert client.post(url + "/preview", json=control).status_code == 401
    password = uuid4().hex
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", auth.password_hash(password))
    client.post("/api/auth/login", json={"password": password}).raise_for_status()
    for day in (DAY, "2026-03-17"):
        payload = body(rows=[dict(row(amount="100.50"), business_date=day)])
        payload["business_date"] = day
        client.post("/api/import/days", json=payload, headers=upload).raise_for_status()
    mismatch = client.post(url + "/save", json=control).json()
    assert mismatch["status"] == "mismatch" and mismatch["total"]["delta"]["net"] == "0.00"
    assert [r["delta"]["sales"] for r in mismatch["rows"]] == ["10.00", "-10.00"]
    assert mismatch["points"][0]["status"] == "mismatch"
    saved_url = url + "/" + mismatch["id"]
    client.post("/api/import/days", headers=upload, json=body(CORRECTED, [])).raise_for_status()
    current = client.post(url + "/preview", json=control).json()
    assert current["days"][0]["actual"]["sales"] == "0.00"
    assert client.get(saved_url).json() == mismatch
    assert client.get(url).json()[0]["id"] == mismatch["id"]
    partial_control = {**control, "csv": control["csv"].rsplit("\n", 1)[0]}
    assert client.post(url + "/preview", json=partial_control).json()["total"]["expected"] is None
    bad = dict(body(CORRECTED), complete=False)
    assert client.post("/api/import/days", headers=upload, json=bad).status_code == 422
    partial = client.post(url + "/preview", json=control).json()
    assert partial["missing_days"] == [DAY] and partial["status"] == "incomplete"
    assert partial["rows"][0]["actual"] is None and partial["total"]["delta"] is None
    duplicate = control["csv"] + "\n2026-03-16;s1;1;0;1"
    assert client.post(url + "/preview", json={**control, "csv": duplicate}).status_code == 422
    unknown = {**control, "point": "unknown", "csv": control["csv"].replace(";s1;", ";unknown;")}
    report = client.post(url + "/preview", json=unknown).json()
    assert report["status"] == "incomplete" and report["unmapped_points"] == ["unknown"]


def test_confirmed_empty_day_includes_a_mapped_point_with_no_history(storage, monkeypatch, tmp_path):
    import json
    from pathlib import Path

    client, _, upload = storage
    base = Path(__file__).resolve().parents[2] / "integrations/1c/profiles/educational"
    definition = json.loads((base / "experiment.json").read_text(encoding="utf-8-sig"))
    definition["pilot_points"] = [{"id": "empty", "name": "Точка без операций"}, {"id": "s1", "name": "Центр"}]
    (tmp_path / "profile.json").write_text(json.dumps(definition), encoding="utf-8")
    (tmp_path / definition["query_file"]).write_text("SELECT", encoding="utf-8")
    monkeypatch.setenv("INTEGRATION_PROFILE", str(tmp_path / "profile.json"))
    password = uuid4().hex
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", auth.password_hash(password))
    client.post("/api/auth/login", json={"password": password}).raise_for_status()
    client.post("/api/import/days", json=body(rows=[]), headers=upload).raise_for_status()
    data = {"start": DAY, "end": DAY, "report_name": "Отчёт", "csv":
            f"date;point_id;sales;returns;net\n{DAY};empty;0;0;0\n{DAY};s1;0;0;0"}
    report = client.post("/api/live/reconciliation/preview", json=data).json()
    assert report["status"] == "matched" and report["total"]["actual"]["net"] == "0.00"
    assert [r["point_name"] for r in report["rows"]] == ["Точка без операций", "Центр"]
    data["csv"] = data["csv"].rsplit("\n", 1)[0]
    partial = client.post("/api/live/reconciliation/preview", json=data).json()
    assert partial["status"] == "incomplete" and partial["rows"][1]["expected"] is None


def test_revoke_during_upload_prevents_commit(storage, monkeypatch):
    import json
    from threading import Event

    client, sessions, upload = storage
    admin = uuid4().hex
    monkeypatch.setenv("OWNER_PASSWORD_HASH", auth.password_hash(uuid4().hex))
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", auth.password_hash(admin))
    client.post("/api/auth/login", json={"role": "admin", "password": admin}).raise_for_status()
    streaming, finish = Event(), Event()

    def delayed_body():
        data = json.dumps(body()).encode("utf-8")
        streaming.set()
        yield data[:10]
        assert finish.wait(timeout=10)
        yield data[10:]

    # The sync body generator blocks its test event loop. Use an independent
    # client loop for the owner of the still-streaming connection.
    with TestClient(client.app, base_url="https://localhost") as uploader, ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(uploader.post, "/api/import/days", headers=upload, content=delayed_body())
        try:
            assert streaming.wait(timeout=10)
            client.post("/api/live/source/disable").raise_for_status()
        finally:
            finish.set()
        assert pending.result(timeout=10).status_code == 401
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Snapshot)) == 0
        assert db.scalar(select(func.count()).select_from(SyncEvent)) == 0
