"""Cancel one synthetic HTTPS upload mid-body, then replay its complete snapshot."""
import http.client
import json
import ssl
import sys
import time

from lab import DAY, HERE, TARGETS, check, client, credentials, get, login, source, state_dir


def main():
    role = "fixture"
    opener = client(role)
    body = (HERE / "day-2026-11-16.json").read_bytes()
    stamp = json.loads(body)["generated_at"]
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + credentials(role)["upload_token"],
               "X-Business-Date": DAY, "X-Snapshot-Generated-At": stamp}
    login(opener, role)
    try:
        baseline = get(opener, role, "/api/import/days", data=body, headers=headers)
        if baseline["row_count"] != 2:
            raise RuntimeError("Ожидались две строки исходной фикстуры")
        # Leading whitespace is valid JSON. Send enough data to cross the proxy's
        # write buffer before simulating the client disappearing halfway through.
        prefix = b" " * 32768 + body[:len(body) // 2]
        host, port, _, _ = TARGETS[role]
        tls = ssl.create_default_context(cafile=str(state_dir(role) / "ca.pem"))
        connection = http.client.HTTPSConnection(host, port, context=tls, timeout=10)
        try:
            connection.putrequest("POST", "/api/import/days")
            for name, value in headers.items():
                connection.putheader(name, value)
            connection.putheader("Content-Length", str(32768 + len(body)))
            connection.endheaders()
            connection.send(prefix)
        finally:
            connection.close()
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                status = source(opener, role)
                if (status["coverage"]["days"][0]["status"] == "incomplete"
                        and status["logs"][0]["code"] == "interrupted"):
                    print(f"{DAY}: прервано соединение, журнал interrupted, покрытие incomplete")
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError("Приёмник не зарегистрировал разрыв начатой передачи")
        finally:
            retry = get(opener, role, "/api/import/days", data=body, headers=headers)
        if retry["status"] != "unchanged":
            raise RuntimeError("Ожидалась исходная версия снимка")
        check(role)
    finally:
        get(opener, role, "/api/auth/logout", data=b"")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
