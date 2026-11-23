"""Save a two-week 1C control export on the pilot instance and verify its immutable readback."""
import argparse
import csv
import json
import sys
from decimal import Decimal
from pathlib import Path

from lab import client, get, login


def save_control(opener, control_file: Path, evidence_file: Path, report_name: str):
    text = control_file.read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(text.splitlines(), delimiter=";"))
    dates = sorted({r["date"] for r in rows})
    if len(dates) != 14 or len(rows) != 28:
        raise ValueError("Учебный контроль должен включать две точки и 14 дней, включая пустые")
    request = {"start": dates[0], "end": dates[-1], "point": None, "report_name": report_name, "csv": text}
    record = get(opener, "pilot", "/api/live/reconciliation/save",
                 data=json.dumps(request, ensure_ascii=False).encode(), headers={"Content-Type": "application/json"})
    saved = get(opener, "pilot", "/api/live/reconciliation/" + record["id"])
    evidence_file.write_text(json.dumps(saved, indent=2, ensure_ascii=False), encoding="utf-8")
    totals = {k: sum(Decimal(r[k].replace(",", ".")) for r in rows) for k in ("sales", "returns", "net")}
    if (saved != record or saved["status"] != "matched" or len(saved["rows"]) != 28
            or len(saved["days"]) != 14 or len(saved["points"]) != 2
            or any(Decimal(saved["total"]["actual"][k]) != v for k, v in totals.items())):
        raise RuntimeError(f"Контрольная сверка не прошла; результат сохранён в {evidence_file}")
    print(f"{report_name}: 28 строк совпали, выручка {totals['net']:.2f} KZT", flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("control", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--label", default="Независимый агрегат 1С по точкам и дням")
    args = parser.parse_args()
    opener = client("pilot")
    login(opener, "pilot")
    try:
        save_control(opener, args.control, args.out, args.label)
    finally:
        get(opener, "pilot", "/api/auth/logout", data=b"")
