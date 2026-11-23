"""Build and exercise San1C in a fresh file-mode infobase using the installed 1C platform."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from uuid import uuid4


SOURCE = Path(__file__).resolve().parent
sys.path.insert(0, str(SOURCE.parent))
from build_epf import PROCESSOR, prepare_processor

sys.path.insert(0, str(SOURCE.parent / "lab" / "transport"))
from lab import check as verify_san1c

PROFILE = SOURCE.parent / "profiles" / "educational" / "experiment.json"
DAY = "2026-11-16"


def prepare(work, pilot=False):
    work.mkdir(parents=True, exist_ok=False)
    cfg = shutil.copytree(SOURCE / "configuration", work / "configuration")
    for xml in cfg.rglob("*.xml"):
        ET.parse(xml)
    common = cfg / "CommonModules" / "УчебнаяСинхронизация" / "Ext" / "Module.bsl"
    common.write_text(common.read_text(encoding="utf-8-sig").replace(
        "__SAN1C_PILOT_DIRECTORY__", str(work.resolve()).replace('"', '""')), encoding="utf-8-sig")
    return cfg, prepare_processor(work / "processor", PROFILE, laboratory=True, pilot=pilot)


def run_step(exe, arguments, log, timeout):
    print(f"1C · {log.stem}", flush=True)
    command = [str(exe), *map(str, arguments), "/Out", str(log)]
    try:
        result = subprocess.run(command, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise SystemExit(f"Время ожидания истекло. Проверьте диалог учебной версии и {log}") from None
    if result.returncode:
        raise SystemExit(f"1С завершилась с кодом {result.returncode}. Журнал: {log}")


def build(exe, work, cfg, epf_source, timeout):
    db_dir = work / "db"
    output = work / (PROCESSOR + ".epf")
    db_dir.mkdir()
    # Windows quotes the entire argument when the path contains spaces. Embedded
    # double quotes would be escaped as backslashes that 1C reads literally.
    connection = "File='" + str(db_dir).replace("'", "''") + "';"
    run_step(exe, ["CREATEINFOBASE", connection, "/DisableStartupDialogs"],
             work / "01-create.log", timeout)
    if not (db_dir / "1Cv8.1CD").is_file():
        raise SystemExit("Не найден файл ИБ. Проверьте 01-create.log.")
    designer = ["DESIGNER", "/F", db_dir, "/DisableStartupDialogs", "/DisableStartupMessages"]
    run_step(exe, [*designer, "/LoadConfigFromFiles", cfg], work / "02-load.log", timeout)
    run_step(exe, [*designer, "/UpdateDBCfg"], work / "03-update.log", timeout)
    run_step(exe, [*designer, "/LoadExternalDataProcessorOrReportFromFiles", epf_source, output],
             work / "04-build-epf.log", timeout)
    if not output.is_file() or output.stat().st_size == 0:
        raise SystemExit("Конфигуратор не создал внешнюю обработку. Проверьте 04-build-epf.log.")
    return db_dir, output


def run_native(exe, work, cfg, epf_source, transport, timeout):
    credentials = transport / "credentials.json"
    ca = transport / "ca.pem"
    for required in (credentials, ca):
        if not required.is_file():
            raise SystemExit(f"Для нативного HTTPS-стенда нужен {required}")
    db_dir, output = build(exe, work, cfg, epf_source, timeout)

    for phase in ("first", "repeat"):
        result_file = work / f"native-{phase}.json"
        job_file = work / f"job-{phase}.json"
        job = {"phase": phase, "credentials_file": str(credentials),
               "ca_file": str(ca), "result_file": str(result_file)}
        job_file.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8-sig")
        # Only the path to a nonsecret job is included in the process command line.
        run_step(exe, ["ENTERPRISE", "/F", db_dir, "/Execute", output, "/C", "SAN1C-LAB:" + str(job_file)],
                 work / f"client-{phase}.log", timeout)
        if not result_file.is_file():
            raise SystemExit(f"Клиент завершился без результата. Проверьте client-{phase}.log и открытие формы.")
        outcome = json.loads(result_file.read_text(encoding="utf-8-sig"))
        expected = {"sales": 10000, "returns": 2000, "net": 8000, "sale_documents": 1,
                    "lines": 2, "store_name": "Учебная точка"}
        receipts = outcome.get("receipts", [])
        if (outcome.get("ok") is not True or outcome.get("controls") != expected or len(receipts) != 1
                or receipts[0]["row_count"] != 2 or receipts[0]["business_date"] != DAY
                or receipts[0]["status"] not in ("accepted", "unchanged")
                or not 200 <= receipts[0]["http_status"] < 300):
            raise SystemExit(f"Контроль не пройден. Этап {outcome.get('stage')}. Результат: {result_file}")
        print(f"{phase}: 1С {outcome['platform']}, HTTP {receipts[0]['http_status']}, "
              f"{receipts[0]['status']}, выручка {outcome['controls']['net']} ₸")
        evidence_file = work / f"api-{phase}.json"
        verify_san1c("native", transport_dir=transport, evidence_file=evidence_file, receipt=receipts[0])
        print(f"{phase}: рабочий отчёт API проверен, результаты в {evidence_file}", flush=True)


def main():
    lab = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "San1C" / "lab"
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--platform", type=Path, help="Полный путь к установленному исполняемому файлу с режимом DESIGNER")
    mode.add_argument("--prepare-only", action="store_true", help="Сформировать исходники без запуска 1С")
    parser.add_argument("--work-dir", type=Path, help="Новый каталог для этой ИБ и доказательств прогона")
    parser.add_argument("--transport-dir", type=Path, default=lab / "transport" / "native")
    parser.add_argument("--timeout", type=int, default=300, help="Тайм-аут каждой команды платформы, секунды")
    args = parser.parse_args()
    if args.platform and not args.platform.is_file():
        parser.error("Не найден исполняемый файл платформы.")
    name = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:8]
    work = (args.work_dir or lab / "native-runs" / name).resolve()
    if work.exists():
        parser.error("Укажите новый каталог. Каждый прогон создаёт отдельную файловую ИБ.")
    cfg, epf = prepare(work)
    print(f"Исходники и журналы прогона: {work}", flush=True)
    if args.prepare_only:
        print("XML прочитан. Нативный запуск ожидает платформу.")
        return
    run_native(args.platform.resolve(), work, cfg, epf, args.transport_dir.resolve(), args.timeout)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    main()
