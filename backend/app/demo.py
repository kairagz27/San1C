"""Deterministic daily snapshots for a fictional retail chain."""

import hashlib
import random
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import insert, select, text
from sqlalchemy.orm import Session

from .models import Fact, Snapshot
from .plans import DAY, business_today, clock_now

ZONE = "Asia/Almaty"
STORES = (
    ("samal", "Алматы · Самал"),
    ("orbita", "Алматы · Орбита"),
    ("esil", "Астана · Есиль"),
    ("centre", "Караганда · Центр"),
)
# Product ID, name, category ID, category name, retail price including VAT.
PRODUCTS = (
    ("rice", "Рис круглозёрный, 1 кг", "grocery", "Бакалея", 810),
    ("oil", "Масло подсолнечное, 1 л", "grocery", "Бакалея", 1190),
    ("pasta", "Макароны, 450 г", "grocery", "Бакалея", 530),
    ("tea", "Чай чёрный, 100 г", "drinks", "Напитки", 960),
    ("juice", "Сок яблочный, 1 л", "drinks", "Напитки", 870),
    ("milk", "Молоко 3,2%, 1 л", "dairy", "Молочная продукция", 640),
    ("cheese", "Сыр сливочный, 250 г", "dairy", "Молочная продукция", 1590),
    ("cookies", "Печенье овсяное, 400 г", "sweets", "Сладости", 780),
    ("chocolate", "Шоколад молочный, 100 г", "sweets", "Сладости", 990),
    ("soap", "Мыло жидкое, 500 мл", "household", "Хозтовары", 750),
    ("towels", "Полотенца бумажные", "household", "Хозтовары", 620),
)


def demo_rows(day):
    rng = random.Random(day.toordinal())
    week = (day.toordinal() - 1) // 7
    for index, (store_id, store_name) in enumerate(STORES):
        base = (30, 26, 25, 18)[index]
        # Two different weekly store cycles give the comparison a visible signal.
        swing = (7, -6, 1, 0)[index] * (1 if week % 2 else -1)
        count = base + swing + (6 if day.weekday() >= 5 else 0) + rng.randint(-2, 2)
        for doc in range(count):
            operation = "return" if rng.random() < 0.055 else "sale"
            document_id = f"{day.isoformat()}/{store_id}/{doc}"
            for line in range(1 if operation == "return" else rng.randint(1, 3)):
                sku, name, category, category_name, price = rng.choice(PRODUCTS)
                quantity = 1 if operation == "return" else rng.randint(1, 5)
                yield {
                    "dataset": "demo", "business_date": day, "document_id": document_id,
                    "line_id": f"{document_id}/{line}", "operation": operation,
                    "store_id": store_id, "store_name": store_name,
                    "product_id": sku, "product_name": name,
                    "category_id": category, "category_name": category_name,
                    "quantity": Decimal(quantity), "amount": Decimal(quantity * price),
                }


def seed_demo(db: Session, now: datetime | None = None):
    """Backfill a rolling window. Concurrent workers serialize the initial seed."""
    now = now or clock_now()
    today = business_today(now, ZONE)
    probe = select(Snapshot.business_date).where(
        Snapshot.dataset == "demo", Snapshot.business_date == today
    )
    if db.scalar(probe) is not None:
        return
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(839783301)"))
    first = today - timedelta(days=119)
    existing = set(db.scalars(select(Snapshot.business_date).where(
        Snapshot.dataset == "demo", Snapshot.business_date.between(first, today)
    )))
    for i in range(120):
        day = first + i * DAY
        if day in existing:
            continue
        rows = list(demo_rows(day))
        stamp = min(now, datetime.combine(day, time(23, 59), ZoneInfo(ZONE))).astimezone(timezone.utc)
        db.execute(insert(Snapshot), [{
            "dataset": "demo", "business_date": day, "generated_at": stamp,
            "received_at": stamp, "row_count": len(rows),
            "digest": hashlib.sha256(f"synthetic-v1/{day}".encode()).hexdigest(),
        }])
        db.execute(insert(Fact), rows)
    db.commit()
