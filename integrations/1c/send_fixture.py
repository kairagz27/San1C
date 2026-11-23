"""Загрузка полного снимка на тестовый экземпляр San1C. Требуется Python 3.10+."""

import argparse
import getpass
import http.client
import json
import os
import sys
from decimal import Decimal
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener


class KeepTokenOnOrigin(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def verify_totals(client, origin, day, expected):
    password = os.environ.get("SAN1C_WEB_PASSWORD") or getpass.getpass("Пароль владельца: ")
    login = Request(
        origin + "/api/auth/login",
        data=json.dumps({"password": password}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with client.open(login, timeout=60):
        pass
    try:
        query = urlencode({"period": "custom", "start": day, "end": day})
        with client.open(origin + "/api/live/sales?" + query, timeout=60) as reply:
            metrics = json.load(reply)["kpis"]
        return all(Decimal(str(metrics[key]["value"])) == value for key, value in expected.items())
    finally:
        with client.open(Request(origin + "/api/auth/logout", method="POST", data=b""), timeout=60):
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8088")
    parser.add_argument("--fixture", type=Path, default=Path(__file__).parent / "fixtures" / "day.json")
    parser.add_argument("--verify", action="store_true", help="Войти как владелец и сверить четыре показателя из API отчёта")
    args = parser.parse_args()
    url = urlsplit(args.base_url)
    local_http = url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}
    if not url.hostname or not (url.scheme == "https" or local_http):
        parser.error("Укажите HTTPS-адрес или локальный HTTP-адрес тестового экземпляра.")
    if url.username is not None or url.password is not None or url.query or url.fragment or url.path.strip("/"):
        parser.error("Нужны только схема, хост и порт API.")

    payload = args.fixture.read_bytes()
    snapshot = json.loads(payload)
    token = os.environ.get("SAN1C_UPLOAD_TOKEN") or getpass.getpass("Токен загрузки: ")
    if not token:
        parser.error("Нужен токен загрузки.")
    origin = args.base_url.rstrip("/")
    opener = build_opener(KeepTokenOnOrigin(), HTTPCookieProcessor(CookieJar()))
    request = Request(
        origin + "/api/import/days",
        method="POST",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
            "X-Business-Date": snapshot["business_date"],
            "X-Snapshot-Generated-At": snapshot["generated_at"],
        },
    )
    try:
        with opener.open(request, timeout=60) as reply:
            result = reply.status
            receipt = json.load(reply)
        if (receipt.get("status") not in {"accepted", "unchanged"}
                or receipt.get("business_date") != snapshot["business_date"]
                or receipt.get("row_count") != snapshot["row_count"]):
            raise ValueError
    except (ValueError, AttributeError):
        print("Ответ API не содержит квитанцию для этого дня.", file=sys.stderr)
        return 1
    except HTTPError as exc:
        print(f"API отклонил снимок, HTTP {exc.code}. Проверьте журнал в разделе «Источник 1С».", file=sys.stderr)
        return 1
    except (URLError, TimeoutError, http.client.HTTPException, OSError):
        print("Нет подтверждения API. Проверьте адрес, сертификат и покрытие даты.", file=sys.stderr)
        return 1
    rows = snapshot["rows"]
    sales = sum((Decimal(row["amount"]) for row in rows if row["operation"] == "sale"), Decimal(0))
    returns = sum((Decimal(row["amount"]) for row in rows if row["operation"] == "return"), Decimal(0))
    documents = len({row["document_id"] for row in rows if row["operation"] == "sale"})
    print(f"HTTP {result}, {receipt['status']}. API подтвердил день {snapshot['business_date']}, строк {len(rows)}.")
    print(f"Контроль по файлу, KZT: продажи {sales:.2f}, возвраты {returns:.2f}, выручка {sales - returns:.2f}.")
    print(f"Документов продажи: {documents}.")
    if args.verify:
        expected = {"sales": sales, "returns": returns, "net": sales - returns, "documents": documents}
        try:
            matches = verify_totals(opener, origin, snapshot["business_date"], expected)
        except (URLError, OSError, http.client.HTTPException, ValueError, KeyError):
            print("Ошибка входа или чтения показателей из API отчёта.", file=sys.stderr)
            return 1
        if not matches:
            print("Четыре показателя отчёта расходятся с фикстурой.", file=sys.stderr)
            return 1
        print("Все четыре показателя рабочего отчёта API совпали с фикстурой.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
