"""Собрать обычную внешнюю обработку для своего профиля в Конфигураторе файловой тестовой базы."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROCESSOR = "San1CВыгрузка"


def prepare_processor(destination, profile_file, *, laboratory=False, pilot=False):
    profile_file = profile_file.resolve(strict=True)
    profile = json.loads(profile_file.read_text(encoding="utf-8-sig"))
    query = (profile_file.parent / profile["query_file"]).resolve(strict=True)
    if not query.is_relative_to(profile_file.parent) or not query.is_file():
        raise ValueError("Файл запроса должен находиться в каталоге профиля")
    destination = shutil.copytree(ROOT / "experiment/processor", destination)
    root = destination / PROCESSOR
    modules = {
        "Ext/ObjectModule.bsl": ["ObjectModule.bsl"],
        "Forms/Форма/Ext/Form/Module.bsl": ["FormModule.bsl"],
    }
    if laboratory:
        modules["Ext/ObjectModule.bsl"].append("experiment/ExperimentObject.bsl")
        modules["Forms/Форма/Ext/Form/Module.bsl"].append("experiment/ExperimentForm.bsl")
        if pilot:
            modules["Ext/ObjectModule.bsl"].append("experiment/PilotObject.bsl")
    else:
        form = root / "Forms/Форма/Ext/Form.xml"
        content = form.read_text(encoding="utf-8-sig")
        event = '<Event name="OnOpen">ПриОткрытии</Event>'
        if content.count(event) != 1:
            raise ValueError("Проверьте шаблон формы: ожидается один обработчик открытия лаборатории")
        form.write_text(content.replace(event, ""), encoding="utf-8-sig")
    for target, files in modules.items():
        path = root / target
        path.parent.mkdir(parents=True, exist_ok=True)
        parts = [(ROOT / name).read_text(encoding="utf-8-sig") for name in files]
        path.write_text("\n\n".join(parts), encoding="utf-8-sig")
    for name, source in [("Профиль", profile_file), ("ЗапросДня", query)]:
        path = root / "Templates" / name / "Ext/Template.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source.read_text(encoding="utf-8-sig"), encoding="utf-8-sig")
    for xml in destination.rglob("*.xml"):
        ET.parse(xml)
    return destination / (PROCESSOR + ".xml")


def build_adapter(exe, database, profile, output, *, timeout=300, user=None):
    if database.name.lower() == "1cv8.1cd" and database.is_file():
        database = database.parent
    if not exe.is_file() or not (database / "1Cv8.1CD").is_file():
        raise ValueError("Укажите исполняемый файл Конфигуратора и существующую файловую тестовую базу")
    if output.suffix.lower() != ".epf":
        raise ValueError("Выходной файл должен иметь расширение .epf")
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".san1c-build-", dir=output.parent) as staging:
        staging = Path(staging)
        source = prepare_processor(staging / "processor", profile)
        candidate, log = staging / "adapter.epf", staging / "Designer.log"
        command = [str(exe.resolve()), "DESIGNER", "/F", str(database.resolve()), "/DisableStartupMessages"]
        if user:
            command.extend(["/N", user])
        command.extend(["/LoadExternalDataProcessorOrReportFromFiles", str(source), str(candidate), "/Out", str(log)])
        try:
            subprocess.run(command, check=True, timeout=timeout)
            if not candidate.is_file() or not candidate.stat().st_size:
                raise ValueError("Конфигуратор завершился без EPF")
        except (ValueError, subprocess.SubprocessError) as exc:
            diagnostic = output.with_suffix(".build.log")
            if log.exists():
                shutil.copyfile(log, diagnostic)
            raise RuntimeError(f"Сборка не завершена. Журнал Конфигуратора: {diagnostic}") from exc
        os.replace(candidate, output)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True, help="Каталог существующей файловой копии или 1Cv8.1CD")
    parser.add_argument("--profile", type=Path, required=True, help="JSON согласованного профиля рядом с query_file")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--user", help="Имя пользователя Конфигуратора, если требуется")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    try:
        result = build_adapter(args.platform, args.database, args.profile, args.output, timeout=args.timeout, user=args.user)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        parser.exit(1, str(exc) + "\n")
    print(f"Собрана внешняя обработка: {result}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
