import json
import os
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from .analytics import freshness
from .auth import Mode, access_dataset, current_upload_hash, digest, require_admin, require_upload_token
from .db import get_db
from .demo import seed_demo
from .models import DayIssue, Snapshot, SourceAccess, SyncEvent, as_utc, utcnow
from .plans import clock_now
from .settings import company_timezone


router = APIRouter(tags=["source"])
TAX_BASIS = "Суммы в KZT с НДС"
PROFILE_RELATIVE = Path("integrations/1c/profiles/educational/profile.json")


def integration_profile(mode: Mode) -> dict:
    if mode == "demo":
        return {
            "id": "demo", "name": "Демонстрационная сеть", "configuration": "Синтетические операции",
            "release": "Демо", "status": "Демо-данные", "tax_basis": TAX_BASIS, "query_template": None,
        }
    configured = os.environ.get("INTEGRATION_PROFILE")
    if configured:
        file = Path(configured)
    else:
        file = next((base / PROFILE_RELATIVE for base in Path(__file__).resolve().parents
                     if (base / PROFILE_RELATIVE).is_file()), PROFILE_RELATIVE)
    try:
        definition = json.loads(file.read_text(encoding="utf-8-sig"))
        profile = {key: definition[key] for key in ("id", "name", "configuration", "release", "status", "tax_basis")}
        profile["organization_uuid"] = definition.get("organization_uuid")
        profile["currency"] = definition.get("currency")
        profile["amount_includes_vat"] = definition.get("amount_includes_vat")
        profile["kzt_amount_metadata"] = definition.get("kzt_amount_metadata")
        profile["pilot_points"] = definition.get("pilot_points", [])
        query = definition.get("query_file")
        profile["query_template"] = None
        if query:
            template = (file.parent / query).resolve()
            if file.parent.resolve() not in template.parents:
                raise ValueError("profile query path")
            profile["query_template"] = template.read_text(encoding="utf-8-sig")
        return profile
    except (OSError, ValueError, KeyError, TypeError):
        return {"id": None, "name": "Профиль недоступен", "configuration": None, "release": None,
                "status": "Проверьте настройки профиля интеграции", "tax_basis": TAX_BASIS, "query_template": None}


def stamp(value: datetime | None) -> str | None:
    return as_utc(value).isoformat() if value else None


def missing_setup(profile):
    if not all(profile.get(k) for k in ("id", "configuration", "release", "query_template")):
        return "Укажите профиль, точный релиз конфигурации и файл запроса"
    try:
        if not UUID(str(profile.get("organization_uuid"))).int:
            raise ValueError
    except ValueError:
        return "В профиле нужен UUID выбранной организации из базы 1С"
    if (profile.get("currency") != "KZT" or profile.get("amount_includes_vat") is not True
            or not profile.get("kzt_amount_metadata")):
        return "Укажите подтверждённые поля сумм продаж и возвратов в KZT с НДС"
    points = profile.get("pilot_points", [])
    if (not isinstance(points, list) or len(points) > 2
            or any(not isinstance(p, dict) or not isinstance(p.get("id"), str) or not p["id"]
                   or not isinstance(p.get("name"), str) or not p["name"] for p in points)
            or len({p["id"] for p in points}) != len(points)):
        return "Проверьте идентификаторы и наименования выбранных торговых точек"
    return None


@router.get("/api/import/check", dependencies=[Depends(require_upload_token)])
def import_check(db: Session = Depends(get_db)):
    profile = integration_profile("live")
    state = db.get(SourceAccess, 1)
    missing = missing_setup(profile)
    state.last_check_at = utcnow()
    state.last_check_status = "profile_unavailable" if missing else "ok"
    db.commit()
    if missing:
        raise HTTPException(503, missing)
    return {"status": "ok", "profile_id": profile["id"], "configuration": profile["configuration"],
            "release": profile["release"], "organization_uuid": profile["organization_uuid"],
            "timezone": company_timezone("live"), "pilot_points": profile["pilot_points"]}


def replace_token(db: Session, value: str | None):
    state = db.get(SourceAccess, 1, with_for_update=True)
    state.managed = True
    state.token_hash = digest(value) if value else None
    state.last_check_at = state.last_check_status = None
    db.commit()


@router.post("/api/live/source/disable", dependencies=[Depends(require_admin)])
def disable_import(db: Session = Depends(get_db)):
    replace_token(db, None)
    return {"enabled": False}


@router.post("/api/live/source/rotate", dependencies=[Depends(require_admin)])
def rotate_import(db: Session = Depends(get_db)):
    value = secrets.token_urlsafe(32)
    replace_token(db, value)
    return {"upload_token": value}


@router.get("/api/{mode}/source")
def source(
    start: date | None = None,
    end: date | None = None,
    mode: Mode = Depends(access_dataset),
    now: datetime = Depends(clock_now),
    db: Session = Depends(get_db, use_cache=False),
):
    timezone = company_timezone(mode)
    end = end or now.astimezone(ZoneInfo(timezone)).date()
    if start is None:
        start = date.fromordinal(max(1, end.toordinal() - 27))
    length = (end - start).days + 1
    if not 1 <= length <= 366:
        raise HTTPException(422, "Выберите от 1 до 366 дней")
    if mode == "demo":
        seed_demo(db, now)
        db.rollback()
    if db.get_bind().dialect.name == "postgresql":
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    range_filter = Snapshot.dataset == mode, Snapshot.business_date.between(start, end)
    snapshots = {
        s.business_date: s for s in db.scalars(select(Snapshot).where(*range_filter))
    }
    issues = set(db.scalars(select(DayIssue.business_date).where(
        DayIssue.dataset == mode, DayIssue.business_date.between(start, end)
    )))
    days = []
    missing = []
    for index in range(length):
        day = start + timedelta(days=index)
        snapshot = snapshots.get(day)
        status, count = "missing", None
        if day in issues:
            status = "incomplete"
        elif snapshot:
            count = snapshot.row_count
            status = "confirmed" if count else "empty"
        if count is None:
            missing.append(day)
        days.append({"date": day, "status": status, "rows": count})

    first, last = db.execute(select(func.min(Snapshot.business_date), func.max(Snapshot.business_date)).where(
        Snapshot.dataset == mode, ~exists().where(
            DayIssue.dataset == Snapshot.dataset, DayIssue.business_date == Snapshot.business_date,
        ),
    )).one()
    last_success, last_generation = db.execute(
        select(func.max(Snapshot.received_at), func.max(Snapshot.generated_at)).where(Snapshot.dataset == mode)
    ).one()
    events = db.scalars(select(SyncEvent).where(SyncEvent.dataset == mode)
                        .order_by(SyncEvent.id.desc()).limit(40)).all()
    logs = [{"at": stamp(item.at), "business_date": item.business_date, "code": item.code, "rows": item.rows}
            for item in events]
    if mode == "demo" and not logs:
        batch = db.scalars(select(Snapshot).where(Snapshot.dataset == mode)
                           .order_by(Snapshot.business_date.desc()).limit(12))
        logs = [{"at": stamp(item.received_at), "business_date": item.business_date,
                 "code": "accepted", "rows": item.row_count} for item in batch]

    status = "ready"
    if mode == "demo":
        status = "demo"
    elif missing:
        status = "empty" if not last_success and not events else "incomplete"
    profile = integration_profile(mode)
    connection = {"enabled": False, "last_check_at": None, "last_check_status": None, "message": "Демонстрационные данные"}
    if mode == "live":
        access = db.get(SourceAccess, 1)
        enabled = bool(current_upload_hash(access))
        message = {
            "ok": "API подтвердил токен и вернул профиль",
            "profile_unavailable": missing_setup(profile) or "Повторите проверку после настройки профиля",
        }.get(access.last_check_status, "Ожидается проверка связи из обработки")
        connection = {"enabled": enabled, "last_check_at": stamp(access.last_check_at),
                      "last_check_status": access.last_check_status, "message": message if enabled else "Приём отключён"}
    return {
        "mode": mode, "timezone": timezone, "profile": profile, "status": status, "tax_basis": profile["tax_basis"],
        "coverage": {"start": start, "end": end, "confirmed": length-len(missing), "total": length,
                     "missing": missing, "days": days, "first_confirmed": first, "last_confirmed": last},
        "last_success_at": stamp(last_success), "last_generated_at": stamp(last_generation), "logs": logs,
        "last_attempt": logs[0] if logs else None, "connection": connection,
        "company_name": os.getenv("COMPANY_NAME") if mode == "live" else None,
        **freshness(db, mode, now.astimezone(ZoneInfo(timezone)).date()),
    }
