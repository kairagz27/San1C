"""Isolated local San1C transport experiment on Windows Docker Desktop. Python 3.10+."""

import argparse
import csv
import hashlib
import json
import os
import re
import secrets
import ssl
import subprocess
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, HTTPSHandler, ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
INTEGRATION = HERE.parents[1]
sys.path.insert(0, str(INTEGRATION))
from send_fixture import KeepTokenOnOrigin

DAY = "2026-11-16"
TARGETS = {
    "pilot": ("localhost", 8448, "experiment.json", "53b30828-2512-4419-8170-000000000002"),
    "native": ("localhost", 8446, "experiment.json", "53b30828-2512-4419-8170-000000000002"),
    "fixture": ("127.0.0.1", 8447, "profile.json", "lab-store"),
}
EXPECTED = {"sales": Decimal("10000.00"), "returns": Decimal("2000.00"), "net": Decimal("8000.00"), "documents": 1}


def origin(role):
    host, port, _, _ = TARGETS[role]
    return f"https://{host}:{port}"


def state_dir(role):
    return Path(os.environ["LOCALAPPDATA"]) / "San1C" / "lab" / "transport" / role


def private_directory(folder):
    folder.mkdir(parents=True, exist_ok=True)
    identity = subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"], encoding="utf-8", errors="replace")
    sid = next(csv.reader([identity.strip()]))[-1]
    if not re.fullmatch(r"S-1-[0-9-]+", sid):
        raise RuntimeError("Не удалось определить SID для приватного каталога")
    subprocess.run(["icacls", str(folder), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F",
                    "*S-1-5-18:(OI)(CI)F"], stdout=subprocess.DEVNULL, check=True)


def credentials(role, transport_dir=None):
    return json.loads(((transport_dir or state_dir(role)) / "credentials.json").read_text(encoding="utf-8"))


def initialize(role):
    folder = state_dir(role)
    private_directory(folder)
    path = folder / "credentials.json"
    if not path.exists():
        temp = folder / (".new-" + secrets.token_hex(8))
        try:
            temp.write_text(json.dumps({"owner_password": secrets.token_urlsafe(24),
                                        "upload_token": secrets.token_urlsafe(32),
                                        "salt": secrets.token_hex(16)}, indent=2), encoding="utf-8")
            try:
                temp.rename(path)  # On Windows refuses to replace an existing credential file.
            except FileExistsError:
                pass
        finally:
            temp.unlink(missing_ok=True)
    values = credentials(role)
    if "admin_password" not in values:
        values["admin_password"] = secrets.token_urlsafe(24)
        temp = folder / (".roles-" + secrets.token_hex(8))
        temp.write_text(json.dumps(values, indent=2), encoding="utf-8")
        temp.replace(path)
    return values


def compose(role, *args, capture=False):
    # Explicit project, files and dotenv prevent settings from another instance
    # (including the usual demo in this checkout) from selecting the deployment.
    command = ["docker", "compose", "--project-name", "san1c-lab-" + role,
               "--env-file", str(state_dir(role) / "compose.env"),
               "-f", str(ROOT / "compose.yaml"), "-f", str(HERE / "compose.lab.yaml"), *args]
    env = os.environ.copy()
    for name in ("OWNER_PASSWORD_HASH", "ADMIN_PASSWORD_HASH", "UPLOAD_TOKEN_SHA256", "SESSION_COOKIE_SECURE", "INTEGRATION_PROFILE",
                 "LAB_PROFILE_DIR", "LAB_TLS_HOST", "LAB_TLS_PORT", "COMPANY_TIMEZONE"):
        env.pop(name, None)
    result = subprocess.run(command, cwd=ROOT, env=env, check=True, stdout=subprocess.PIPE if capture else None)
    return result.stdout


def start(role, profile=None):
    private = initialize(role)
    host, port, default_profile, _ = TARGETS[role]
    profile = (profile or INTEGRATION / "profiles" / "educational" / default_profile).resolve(strict=True)
    metadata = json.loads(profile.read_text(encoding="utf-8-sig"))
    if not (profile.parent / metadata["query_file"]).is_file():
        raise RuntimeError("Шаблон профиля отсутствует")
    salt = private["salt"]
    def encoded(name):
        password_hash = hashlib.pbkdf2_hmac("sha256", private[name].encode(), bytes.fromhex(salt), 600_000).hex()
        return f"pbkdf2_sha256:600000:{salt}:{password_hash}"

    config = {
        "OWNER_PASSWORD_HASH": encoded("owner_password"), "ADMIN_PASSWORD_HASH": encoded("admin_password"),
        "UPLOAD_TOKEN_SHA256": hashlib.sha256(private["upload_token"].encode()).hexdigest(),
        "SESSION_COOKIE_SECURE": "1", "COMPANY_TIMEZONE": "Asia/Almaty",
        "LAB_TLS_HOST": host, "LAB_TLS_PORT": str(port), "LAB_PROFILE_DIR": profile.parent.as_posix(),
        "INTEGRATION_PROFILE": "/srv/lab-profile/" + profile.name,
    }
    if any("'" in val or "\n" in val for val in config.values()):
        raise ValueError("Compose path contains an unsupported quote or newline")
    folder = state_dir(role)
    (folder / "compose.env").write_text("".join(f"{key}='{val}'\n" for key, val in config.items()), encoding="utf-8")
    compose(role, "up", "--build", "--detach", "--wait")
    ca = folder / "ca.pem"
    for i in range(30):
        try:
            cert = compose(role, "exec", "-T", "tls", "cat", "/data/caddy/pki/authorities/local/root.crt", capture=True)
            ssl.PEM_cert_to_DER_cert(cert.decode("ascii"))
            ca.write_bytes(cert)
            get(client(role), role, "/api/health")
            break
        except (ValueError, OSError, subprocess.CalledProcessError):
            if i == 29:
                raise RuntimeError("TLS не готов. Проверьте docker compose logs tls")
            time.sleep(1)
    print(f"{role}: {origin(role)} · {metadata['id']} · CA {ca}")


def client(role, transport_dir=None):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=str((transport_dir or state_dir(role)) / "ca.pem"))
    return build_opener(ProxyHandler({}), HTTPSHandler(context=context), KeepTokenOnOrigin(), HTTPCookieProcessor(CookieJar()))


def get(opener, role, path, *, data=None, headers=None):
    request = Request(origin(role) + path, data=data, headers=headers or {})
    with opener.open(request, timeout=30) as response:
        return json.load(response)


def login(opener, role, transport_dir=None, *, account="owner"):
    values = credentials(role, transport_dir)
    account = account if account + "_password" in values else "owner"
    request = {"password": values[account + "_password"]}
    if account == "admin":
        request["role"] = account
    payload = json.dumps(request).encode()
    return get(opener, role, "/api/auth/login", data=payload, headers={"Content-Type": "application/json"})


def source(opener, role):
    return get(opener, role, "/api/live/source?" + urlencode({"start": DAY, "end": DAY}))


def check(role, upload=False, *, transport_dir=None, evidence_file=None, receipt=None):
    if upload and role != "fixture":
        raise ValueError("Для HTTP-фикстуры предусмотрен только отдельный экземпляр fixture")
    opener = client(role, transport_dir)
    login(opener, role, transport_dir, account="admin")
    try:
        if upload:
            body = (HERE / "day-2026-11-16.json").read_bytes()
            payload = json.loads(body)
            headers = {"Content-Type": "application/json", "Authorization": "Bearer " + credentials(role, transport_dir)["upload_token"],
                       "X-Business-Date": DAY, "X-Snapshot-Generated-At": payload["generated_at"]}
            new = source(opener, role)["coverage"]["days"][0]["status"] == "missing"
            for idx in range(2):
                receipt = get(opener, role, "/api/import/days", data=body, headers=headers)
                expected = "accepted" if idx == 0 and new else "unchanged"
                if receipt != {"status": expected, "business_date": DAY, "row_count": 2}:
                    raise RuntimeError("Неожиданная квитанция импорта")
                print(f"POST {DAY} · {receipt['status']} · 2 строки")
        snapshot = source(opener, role)
        day = snapshot["coverage"]["days"][0]
        print(f"{role}: профиль {snapshot['profile']['id']}, {DAY}, покрытие {day['status']}, строк {day['rows']}")
        last_event = next((event for event in snapshot["logs"] if event["business_date"] == DAY), None)
        reports = []
        for point in ("", TARGETS[role][3]):
            params = {"period": "custom", "start": DAY, "end": DAY}
            if point:
                params["point"] = point
            result = get(opener, role, "/api/live/sales?" + urlencode(params))
            reports.append({
                "range": result["range"], "filters": result["filters"],
                "current_complete": result["meta"]["current_complete"],
                "points": [{"id": row["id"], "name": row["name"]} for row in result["points"]
                           if row["documents"] or Decimal(row["returns"])],
                "kpis": {key: item["value"] for key, item in result["kpis"].items()},
            })
        if evidence_file is not None:
            observation = {
                "observed_at": datetime.now(timezone.utc).isoformat(), "origin": origin(role),
                "profile_id": snapshot["profile"]["id"], "day": day, "last_event": last_event,
                "last_generated_at": snapshot["last_generated_at"], "last_success_at": snapshot["last_success_at"],
                "reports": reports,
            }
            # Capture the actual readback even when a subsequent assertion fails.
            evidence_file.write_text(json.dumps(observation, ensure_ascii=False, indent=2), encoding="utf-8")
        if day["status"] != "confirmed" or day["rows"] != 2 or day["date"] != DAY:
            raise RuntimeError("Для сверки нужны две строки подтверждённого дня")
        if receipt is not None and (not last_event or last_event["code"] != receipt["status"]
                                    or last_event["rows"] != receipt["row_count"]):
            raise RuntimeError("Квитанция обработки расходится с последним событием API за день")
        for point, report in zip(("", TARGETS[role][3]), reports):
            metrics = {key: Decimal(str(value)) for key, value in report["kpis"].items() if value is not None}
            if (metrics != EXPECTED or not report["current_complete"] or report["range"] != {"start": DAY, "end": DAY}
                    or report["filters"].get("point") != (point or None)
                    or report["points"] != [{"id": TARGETS[role][3], "name": "Учебная точка"}]):
                raise RuntimeError("Контроль показателей или точки не совпал")
            print(f"{point or 'Все точки'} · продажи 10000.00 · возвраты 2000.00 · выручка 8000.00 KZT · документов продажи 1")
    finally:
        get(opener, role, "/api/auth/logout", data=b"")


def status(role):
    opener = client(role)
    login(opener, role, account="admin")
    try:
        data = source(opener, role)
        coverage = data["coverage"]
        print(f"{origin(role)} · {data['profile']['id']} · {DAY}: {coverage['days'][0]['status']}; "
              f"диапазон {coverage['first_confirmed']} … {coverage['last_confirmed']}; "
              f"последний приём {data['last_success_at']}")
    finally:
        get(opener, role, "/api/auth/logout", data=b"")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    for action in ("up", "status", "verify", "stop"):
        command = actions.add_parser(action)
        command.add_argument("target", choices=TARGETS)
        if action == "up":
            command.add_argument("--profile", type=Path)
    actions.add_parser("send-fixture")
    args = parser.parse_args()
    if args.action == "up":
        start(args.target, args.profile)
    elif args.action == "status":
        status(args.target)
    elif args.action == "verify":
        check(args.target)
    elif args.action == "send-fixture":
        check("fixture", upload=True)
    else:
        compose(args.target, "stop")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
