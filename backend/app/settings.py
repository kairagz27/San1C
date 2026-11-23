import os
from zoneinfo import ZoneInfo


def database_url() -> str:
    return os.environ.get("DATABASE_URL", "postgresql+psycopg://localhost/san1c")


def company_timezone(mode: str) -> str:
    value = "Asia/Almaty" if mode == "demo" else os.environ.get("COMPANY_TIMEZONE", "Asia/Almaty")
    ZoneInfo(value)  # Fail early for an unknown IANA name.
    return value
