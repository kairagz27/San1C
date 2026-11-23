"""Run the two-week file-mode 1C pilot against the isolated localhost:8448 database."""

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

from run import PROFILE, build, prepare, run_step
from build_epf import build_adapter
from lab import check, client, get, login, private_directory, state_dir
from compare_native import save_control


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_controls(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, ["date", "point_id", "sales", "returns", "net"],
                                delimiter=";", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def scheduled_log(db):
    """Extract native job lifecycle events for this job from the file-mode event log."""
    folder = db / "1Cv8Log"
    dictionary = (folder / "1Cv8.lgf").read_text(encoding="utf-8-sig")
    names = {number: name for name, number in re.findall(r'\{4,"(_\$Job\$_\.\w+)",(\d+)\}', dictionary)}
    metadata = re.search(r'\{5,[^,]+,"РегламентноеЗадание\.УчебнаяВыгрузкаПродаж",(\d+)\}', dictionary)
    if not metadata:
        raise RuntimeError("Журнал регистрации не содержит метаданные задания")
    events = []
    for fragment in sorted(folder.glob("*.lgp")):
        log = fragment.read_text(encoding="utf-8-sig")
        for dt, event, level, obj in re.findall(
                r'\{(\d{14}),[A-Z],\s*\{[^}]*\},\d+,\d+,\d+,\d+,(\d+),([A-Z]),"(?:[^"]|"")*",(\d+),', log):
            if obj == metadata.group(1) and event in names:
                events.append({"time_local": datetime.strptime(dt, "%Y%m%d%H%M%S").isoformat(),
                               "event": names[event], "level": level})
    return events


def verify_phase(opener, work, name, rows):
    reports = []
    for point in sorted({r["point_id"] for r in rows}):
        expected = {r["date"]: r for r in rows if r["point_id"] == point}
        search = urlencode({"period": "custom", "start": min(expected), "end": max(expected), "point": point})
        result = get(opener, "pilot", "/api/live/sales?" + search)
        if not result["meta"]["current_complete"] or {r["date"] for r in result["daily"]} != set(expected):
            raise RuntimeError(f"{name}: неполное покрытие")
        for day in result["daily"]:
            row = expected[day["date"]]
            actual = {k: day[k] for k in ("sales", "returns", "net", "documents")}
            control = {k: row[k] for k in ("sales", "returns", "net")}
            control["documents"] = row["sale_documents"]
            if not day["covered"] or any(Decimal(str(actual[k])) != Decimal(str(control[k])) for k in actual):
                raise RuntimeError(f"{name}: расхождение {day['date']} {point}: {actual} / {control}")
            reports.append({"date": day["date"], "point_id": point, **actual})
    if name in ("correct", "unpost"):
        changed = next(r for r in rows if r["point_id"].endswith("0002"))
        amount = Decimal("1550.25") if name == "correct" else Decimal("0")
        if Decimal(str(changed["sales"])) != amount or changed["sale_documents"] != int(amount > 0):
            raise RuntimeError(f"{name}: сумма или проведение изменённого документа не совпали")
    write(work / f"api-{name}.json", reports)
    write_controls(work / f"control-{name}.csv", rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", required=True, type=Path)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    if not args.platform.is_file():
        parser.error("Исполняемый файл платформы не найден")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:8]
    work = (args.work_dir or Path(os.environ["LOCALAPPDATA"]) / "San1C/lab/pilot-runs" / stamp).resolve()
    if work.exists():
        parser.error("Нужен новый каталог для файловой ИБ")
    cfg, source = prepare(work, pilot=True)
    private_directory(work)
    print(f"Каталог прогона {work}", flush=True)
    db, _ = build(args.platform, work, cfg, source, args.timeout)

    build_adapter(args.platform, db, PROFILE, work / "San1CАдаптер.epf", timeout=args.timeout)
    transport = state_dir("pilot")
    opener = client("pilot")
    login(opener, "pilot", account="admin")
    job = {"credentials_file": str(transport / "credentials.json"), "ca_file": str(transport / "ca.pem"), "port": 8448}
    current_rows = {}
    for name in ("first", "repeat", "seed14", "repeat14", "correct", "unpost", "schedule"):
        result = work / f"native-{name}.json"
        job.update(phase=name, result_file=str(result))
        write(work / "pilot-job.json", job)
        run_step(args.platform, ["ENTERPRISE", "/F", db, "/C", "SAN1C-PILOT", "/DisableStartupMessages"],
                 work / f"client-{name}.log", max(args.timeout, 420) if name == "schedule" else args.timeout)
        if name == "schedule":
            scheduled = [read(path) for path in sorted(work.glob("scheduled-*.json"), key=lambda p: p.stat().st_mtime)]
            if (len(scheduled) != 3 or scheduled[0].get("preflight", {}).get("http_status") != 401
                    or scheduled[0].get("ok") is not False or not all(x.get("ok") and len(x["receipts"]) == 3 for x in scheduled[1:])):
                raise RuntimeError("Нужны отказ и две последующие полные выгрузки планировщика")
            verify_phase(opener, work, "schedule", scheduled[-1]["controls"])
            write(result, scheduled)
            events = scheduled_log(db)
            if sum(e["event"] == "_$Job$_.Start" for e in events) < 3:
                raise RuntimeError("Не найдены три старта задания в журнале самой 1С")
            write(work / "schedule-events.json", events)
            continue
        outcome = read(result)
        if not outcome["ok"]:
            raise RuntimeError(f"{name}: {outcome.get('message') or outcome.get('preflight') or outcome.get('stage')}")
        if name in ("first", "repeat"):
            if outcome["controls"]["net"] != 8000 or len(outcome["receipts"]) != 1 or outcome["receipts"][0]["row_count"] != 2:
                raise RuntimeError("Регрессия контроля 16.11")
            check("pilot", receipt=outcome["receipts"][0], evidence_file=work / f"api-{name}.json")
        else:
            if name == "seed14" and (outcome["rejected_token_check"]["http_status"] != 401
                    or "профиль" not in outcome["wrong_release"] or "KZT" not in outcome["unconfirmed_kzt_amount"]
                    or "НесуществующаяСумма" not in outcome["missing_field"] or "закрытый день" not in outcome["open_day"]):
                raise RuntimeError("Проверки отказа на непригодном профиле и токене не пройдены")
            verify_phase(opener, work, name, outcome["controls"])
            for row in outcome["controls"]:
                current_rows[row["date"], row["point_id"]] = row
            write_controls(work / "control-final.csv", list(current_rows.values()))
            save_control(opener, work / "control-final.csv", work / f"reconciliation-{name}.json",
                         f"Учебная 1С · {name} · {stamp}")
    get(opener, "pilot", "/api/auth/logout", data=b"")
    print(f"Регрессия, две недели, исправление, отмена проведения и планировщик проверены. {work}", flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
