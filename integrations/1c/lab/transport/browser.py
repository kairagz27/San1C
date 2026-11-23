"""Verify the four visible sales KPIs in Firefox, with trust in a private profile."""

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from lab import DAY, EXPECTED, ROOT, TARGETS, credentials, origin, state_dir


def trust_profile(role):
    return trust_directory(state_dir(role))


def trust_directory(state):
    ca = state / "ca.pem"
    fingerprint = hashlib.sha256(ca.read_bytes()).hexdigest()
    profile = state / ("firefox-" + fingerprint[:16])
    profile.mkdir(exist_ok=True)
    if (profile / "cert9.db").is_file():
        return profile
    # NSS certificate databases are SQLite. Generate the database with the
    # signed Alpine nss-tools package, then copy only cert9.db into a fresh
    # Windows Firefox profile. Neither the CA key nor credentials are mounted.
    with tempfile.TemporaryDirectory(dir=state) as temp:
        subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "sh",
             "--mount", f"type=bind,source={temp},target=/nss",
             "--mount", f"type=bind,source={ca},target=/root-ca.pem,readonly",
             "caddy:2-alpine", "-ec",
             "apk add -q --no-cache nss-tools >/dev/null\n"
             "certutil -N -d sql:/nss --empty-password\n"
             "certutil -A -d sql:/nss -n San1C-Local-Lab -t 'C,,' -i /root-ca.pem"],
            check=True,
        )
        shutil.copyfile(Path(temp) / "cert9.db", profile / "cert9.db")
    return profile


def number(text):
    return Decimal(re.sub(r"[\s₸]", "", text).replace(",", ".").replace("−", "-"))


def verify(role, screenshot):
    from playwright.sync_api import Error, expect, sync_playwright

    point = TARGETS[role][3]
    base = origin(role)
    trusted = trust_profile(role)
    with tempfile.TemporaryDirectory(dir=state_dir(role)) as fresh, sync_playwright() as p:
        shutil.copyfile(trusted / "cert9.db", Path(fresh) / "cert9.db")
        browser = p.firefox.launch_persistent_context(
            fresh, locale="ru-RU", viewport={"width": 1440, "height": 1000},
            firefox_user_prefs={"security.enterprise_roots.enabled": False},
        )
        try:
            page = browser.pages[0]
            response = page.goto(base + "/#/live/sales")
            if not response or not response.ok:
                raise RuntimeError("Не удалось открыть страницу по HTTPS")
            if not response.security_details():
                raise RuntimeError("Браузер не подтвердил TLS")
            try:
                page.get_by_label("Пароль", exact=True).fill(credentials(role)["owner_password"])
            except Error:
                raise RuntimeError("Не удалось заполнить поле входа") from None
            page.get_by_role("button", name="Войти", exact=True).click()
            expect(page.locator(".kpi-value")).to_have_count(4)
            page.get_by_label("Торговая точка", exact=True).select_option(point)
            page.get_by_label("Период", exact=True).select_option("custom")
            page.get_by_label("С", exact=True).fill(DAY)
            page.get_by_label("По", exact=True).fill(DAY)

            def filtered(response):
                url = urlsplit(response.url)
                query = parse_qs(url.query)
                return (url.path == "/api/live/sales" and query.get("start") == [DAY]
                        and query.get("end") == [DAY] and query.get("point") == [point])

            with page.expect_response(filtered) as received:
                page.get_by_role("button", name="Применить", exact=True).click()
            if not received.value.ok:
                raise RuntimeError("Ошибка получения рабочего отчёта")
            labels = {"sales": "Сумма продаж", "returns": "Возвраты", "net": "Выручка", "documents": "Документы продаж"}
            for key, value in EXPECTED.items():
                card = page.locator("article.kpi").filter(has=page.get_by_text(labels[key], exact=True))
                displayed = number(card.locator(".kpi-value").inner_text())
                if displayed != value:
                    raise RuntimeError(f"{labels[key]}: получено {displayed}, ожидалось {value}")
            expect(page.locator("table.points tbody tr")).to_have_count(1)
            expect(page.locator("table.points tbody tr").first).to_contain_text("Учебная точка")
            page.evaluate("document.fonts.ready")
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot), animations="disabled")
            page.evaluate("fetch('/api/auth/logout', {method:'POST'})")
            print(f"Firefox · HTTPS · {DAY} · Учебная точка · 4 KPI совпали · {screenshot}")
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=TARGETS)
    parser.add_argument("--prepare-only", action="store_true", help="Создать профиль для интерактивного Firefox")
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    if args.prepare_only:
        print(trust_profile(args.target))
    else:
        target_file = args.screenshot or ROOT / ".verification" / "1c-lab" / (args.target + "-sales.png")
        verify(args.target, target_file.resolve())


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
