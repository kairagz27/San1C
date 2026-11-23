"""Separate browser sessions and machine-to-machine ingestion credentials."""

import getpass
import hashlib
import hmac
import os
import secrets
import time
from collections import OrderedDict
from datetime import timedelta
from threading import Lock
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy import delete
from sqlalchemy.orm import Session

from .db import get_db
from .models import SourceAccess, WebSession, as_utc, utcnow


router = APIRouter(prefix="/api/auth", tags=["auth"])
Mode = Literal["demo", "live"]
Role = Literal["owner", "admin"]
COOKIE = "san1c_session"
SESSION_SECONDS = 8 * 60 * 60
ITERATIONS = 600_000
_attempts: OrderedDict[str, tuple[float, int]] = OrderedDict()
_attempt_lock = Lock()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def password_hash(password: str) -> str:
    salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS)
    return f"pbkdf2_sha256:{ITERATIONS}:{salt}:{hashed.hex()}"


def password_matches(password: str, encoded: str) -> bool:
    try:
        scheme, rounds, salt, expected = encoded.split(":")
        if scheme != "pbkdf2_sha256" or not 100_000 <= int(rounds) <= 2_000_000:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, OverflowError):
        return False


def current_upload_hash(access: SourceAccess) -> str:
    if access.managed:
        return access.token_hash or ""
    return os.getenv("UPLOAD_TOKEN_SHA256", "").lower()


def verify_upload_key(authorization: str, access: SourceAccess) -> None:
    expected = current_upload_hash(access)
    scheme, _, value = authorization.partition(" ")
    authorized = bool(value) and expected.isascii() and hmac.compare_digest(digest(value), expected)
    if scheme.lower() != "bearer" or not authorized:
        raise HTTPException(401, "Нужен действующий токен загрузки")


def require_upload_token(request: Request, db: Session = Depends(get_db)) -> None:
    if request.url.path == "/api/import/days":
        verify_upload_key(request.headers.get("authorization", ""), db.get(SourceAccess, 1))
        db.rollback()  # Release the connection while the client streams its body.
    else:
        state = db.get(SourceAccess, 1, with_for_update=True)
        verify_upload_key(request.headers.get("authorization", ""), state)


def role_hash(role: Role) -> str:
    owner = os.getenv("OWNER_PASSWORD_HASH", "")
    admin = os.getenv("ADMIN_PASSWORD_HASH", "")
    if role == "owner":
        return owner or admin  # Original single-password installations have owner access.
    return admin if owner else ""


def active_role(request: Request, db: Session) -> Role | None:
    cookie = request.cookies.get(COOKIE)
    if not cookie or len(cookie) > 128:
        return None
    session = db.get(WebSession, digest(cookie))
    if not session or session.role not in ("owner", "admin") or as_utc(session.expires_at) <= utcnow():
        return None
    credential = role_hash(session.role)
    if credential and hmac.compare_digest(session.credential_tag, digest(credential)):
        return session.role
    return None


def verify_browser_origin(request: Request) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Откройте сервис по его основному адресу")
    origin = request.headers.get("origin")
    if origin is None:  # Native clients do not have a browser origin.
        return
    try:
        parsed = urlsplit(origin)
        local = request.url.hostname in {"localhost", "127.0.0.1", "::1"}
        schemes = {"https", "http"} if local and request.url.scheme == "http" else {"https"}
        valid = parsed.scheme in schemes and parsed.netloc.lower() == request.headers.get("host", "").lower()
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(403, "Откройте сервис по его основному адресу")


def access_dataset(mode: Mode, request: Request, db: Session = Depends(get_db)) -> Mode:
    verify_browser_origin(request)
    if mode == "live" and not active_role(request, db):
        raise HTTPException(401, "Войдите для просмотра данных 1С")
    return mode


def require_live(request: Request, db: Session = Depends(get_db)) -> Role:
    verify_browser_origin(request)
    role = active_role(request, db)
    if not role:
        raise HTTPException(401, "Войдите для просмотра данных 1С")
    return role


def require_admin(role: Role = Depends(require_live)) -> None:
    if role != "admin":
        raise HTTPException(403, "Управление источником доступно администратору")


def _reserve_attempt(host: str) -> None:
    now = time.monotonic()
    with _attempt_lock:
        start, count = _attempts.pop(host, (now, 0))
        if now - start >= 300:
            start, count = now, 0
        _attempts[host] = (start, count + 1)
        if len(_attempts) > 4096:
            _attempts.popitem(last=False)
        if count >= 10:
            raise HTTPException(429, "Слишком много попыток входа. Повторите через пять минут.")


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: SecretStr
    role: Role = "owner"


@router.get("/session")
def session_status(request: Request, db: Session = Depends(get_db)):
    role = active_role(request, db)
    return {"authenticated": bool(role), "configured": bool(role_hash("owner")), "role": role}


@router.post("/login")
def login(data: Credentials, request: Request, response: Response, db: Session = Depends(get_db)):
    verify_browser_origin(request)
    encoded = role_hash(data.role)
    if not encoded:
        raise HTTPException(503, "Администратор ещё не настроил доступ к рабочей базе")
    host = request.client.host if request.client else ""
    _reserve_attempt(host)
    password = data.password.get_secret_value()
    if len(password) > 1024 or not password_matches(password, encoded):
        raise HTTPException(401, "Неверный пароль")
    with _attempt_lock:
        _attempts.pop(host, None)
    value = secrets.token_urlsafe(32)
    now = utcnow()
    db.execute(delete(WebSession).where(WebSession.expires_at <= now))
    db.add(WebSession(digest=digest(value), credential_tag=digest(encoded), role=data.role,
                      expires_at=now + timedelta(seconds=SESSION_SECONDS)))
    db.commit()
    local = request.url.hostname in {"localhost", "127.0.0.1", "::1"}
    https_origin = request.headers.get("origin", "").lower().startswith("https://")
    secure = not local or request.url.scheme == "https" or https_origin or os.getenv("SESSION_COOKIE_SECURE") == "1"
    response.set_cookie(
        COOKIE, value, max_age=SESSION_SECONDS, httponly=True, samesite="strict",
        secure=secure, path="/api",
    )
    return {"authenticated": True, "configured": True, "role": data.role}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    verify_browser_origin(request)
    cookie = request.cookies.get(COOKIE)
    if cookie:
        db.execute(delete(WebSession).where(WebSession.digest == digest(cookie)))
        db.commit()
    response.delete_cookie(COOKIE, path="/api", httponly=True, samesite="strict")
    return {"authenticated": False, "configured": bool(role_hash("owner")), "role": None}


def issue_credentials() -> None:
    password = getpass.getpass("Пароль владельца (минимум 12 символов): ")
    if not 12 <= len(password) <= 1024 or password != getpass.getpass("Подтвердите пароль: "):
        raise SystemExit("Пароль или подтверждение не подходят")
    admin = getpass.getpass("Отдельный пароль администратора (минимум 12 символов): ")
    if not 12 <= len(admin) <= 1024 or admin == password or admin != getpass.getpass("Подтвердите пароль администратора: "):
        raise SystemExit("Нужны разные пароли и совпадающее подтверждение")
    token = secrets.token_urlsafe(32)
    print("В .env сервера:")
    print("OWNER_PASSWORD_HASH=" + password_hash(password))
    print("ADMIN_PASSWORD_HASH=" + password_hash(admin))
    print("UPLOAD_TOKEN_SHA256=" + digest(token))
    print("Однократно скопируйте в настройки обработки 1С токен загрузки:")
    print(token)


if __name__ == "__main__":
    issue_credentials()
