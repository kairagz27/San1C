"""Owner acceptance with an independent CSV on an isolated HTTPS pilot instance."""

import argparse
import csv
import io
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "integrations/1c/lab/transport"))
from browser import trust_directory


def login(page, origin, password, role):
    page.goto(origin + "/#/live/source")
    page.get_by_role("combobox", name="Роль", exact=True).select_option(role)
    page.get_by_label("Пароль", exact=True).fill(password)
    page.get_by_role("button", name="Войти", exact=True).click()
    expect(page.get_by_role("button", name="Подключение и покрытие")).to_be_visible()


def number(text):
    return Decimal(re.sub(r"[\s₸]", "", text).replace("−", "-").replace(",", "."))


def source_coverage(page, rows, screenshots):
    expected_range = (min(row["date"] for row in rows), max(row["date"] for row in rows))

    def is_coverage(response):
        url = urlsplit(response.url)
        return url.path == "/api/live/source" and "start=" in url.query

    with page.expect_response(is_coverage) as requested:
        page.get_by_role("button", name="Две завершённые недели", exact=True).click()
    coverage = requested.value.json()["coverage"]
    if (coverage["start"], coverage["end"]) != expected_range:
        page.get_by_label("С", exact=True).fill(expected_range[0])
        page.get_by_label("По", exact=True).fill(expected_range[1])
        with page.expect_response(is_coverage) as requested:
            page.get_by_role("button", name="Применить", exact=True).click()
        coverage = requested.value.json()["coverage"]
    labels = {
        "confirmed": "Есть операции",
        "empty": "Без операций",
        "missing": "Нет снимка",
        "incomplete": "Неполный снимок",
    }
    assert (coverage["start"], coverage["end"]) == expected_range
    assert coverage["confirmed"] == coverage["total"]
    count = page.locator(".fact-tile").filter(has_text="Подтверждённые дни").locator("strong")
    expect(count).to_have_text(f"{coverage['confirmed']} / {coverage['total']}")
    for item in coverage["days"]:
        day = page.get_by_label(item["date"] + " " + labels[item["status"]], exact=True)
        expect(day).to_be_visible()
    expect(page.get_by_text("Подтверждённые дни без операций", exact=True)).to_be_visible()
    if screenshots:
        page.screenshot(path=str(screenshots / "source.png"), full_page=True)
    print("Source coverage:", coverage["confirmed"], "/", coverage["total"], *expected_range)


def submit_csv(page, rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream, delimiter=";", fieldnames=["date", "point_id", "sales", "returns", "net"]
    )
    writer.writeheader()
    writer.writerows(rows)
    page.get_by_label("CSV штатного отчёта").set_input_files(
        {"name": "control.csv", "mimeType": "text/csv", "buffer": stream.getvalue().encode("utf-8-sig")}
    )
    with page.expect_response(
        lambda r: urlsplit(r.url).path == "/api/live/reconciliation/preview"
    ) as pending:
        page.get_by_role("button", name="Сравнить с San1C").click()
    assert pending.value.ok, f"Preview HTTP {pending.value.status}"
    return pending.value.json()


def reconcile(page, rows, title, screenshots):
    page.get_by_role("button", name="Сверка с отчётом 1С", exact=True).click()
    form = page.locator(".recon-form")
    form.get_by_label("Название штатного отчёта").fill(title)
    form.get_by_label("С", exact=True).fill(min(r["date"] for r in rows))
    form.get_by_label("По", exact=True).fill(max(r["date"] for r in rows))
    result = submit_csv(page, rows)
    expect(page.locator(".result-status")).to_have_text("Совпадает")
    totals = page.locator(".recon-totals tbody tr")
    for index, key in enumerate(("sales", "returns", "net")):
        expected = sum(Decimal(row[key].replace(",", ".")) for row in rows)
        cells = totals.nth(index).get_by_role("cell")
        assert [number(cells.nth(i).inner_text()) for i in (1, 2, 3)] == [expected, expected, 0]
    if screenshots:
        page.locator(".recon-result").screenshot(path=str(screenshots / "reconciliation.png"))
    for label, count in (
        ("По дням", len(set(row["date"] for row in rows))),
        ("По точкам", len(set(row["point_id"] for row in rows))),
        ("День и точка", len(rows)),
    ):
        page.get_by_role("button", name=label, exact=True).click()
        expect(page.locator(".recon-detail tbody tr")).to_have_count(count)

    page.get_by_role("button", name="Сохранить контрольный результат").click()
    expect(page.get_by_text("Контрольный результат сохранён", exact=True)).to_be_visible()
    page.reload()
    page.get_by_role("button", name="Сверка с отчётом 1С", exact=True).click()
    page.locator(".recon-history").get_by_role("button", name=re.compile(re.escape(title))).click()
    expect(page.locator(".result-status")).to_have_text("Совпадает")
    print("CSV and persisted reconciliation:", result["start"], result["end"], result["total"]["actual"])

    form.get_by_label("Название штатного отчёта").fill(title)
    form.get_by_label("С", exact=True).fill(min(r["date"] for r in rows))
    form.get_by_label("По", exact=True).fill(max(r["date"] for r in rows))
    adjusted = [dict(r) for r in rows]
    for key in ("sales", "net"):
        adjusted[0][key] = str(Decimal(adjusted[0][key].replace(",", ".")) + Decimal("0.01"))
    submit_csv(page, adjusted)
    expect(page.locator(".result-status")).to_have_text("Расхождение")
    page.get_by_role("button", name="Найти расхождение").click()
    broken = page.locator(".recon-detail tbody tr")
    expect(broken).to_have_count(1)
    assert number(broken.get_by_role("cell").nth(3).inner_text()) == Decimal("-0.01")
    if screenshots:
        page.locator(".recon-result").screenshot(path=str(screenshots / "difference.png"))
    page.get_by_role("button", name="По дням", exact=True).click()
    page.get_by_role("button", name="Показать строки за " + adjusted[0]["date"], exact=True).click()
    expect(page.get_by_role("combobox", name="День сверки", exact=True)).to_have_value(
        adjusted[0]["date"]
    )
    if len(rows) > 1:
        submit_csv(page, rows[1:])
        expect(page.locator(".result-status")).to_have_text("Неполные данные")
    print("Changed CSV cent and missing control row isolated in the browser")


def store_widget(page, rows):
    page.get_by_role("link", name="Продажи", exact=True).click()
    page.get_by_label("Период", exact=True).select_option("custom")
    page.get_by_label("С", exact=True).fill(min(r["date"] for r in rows))
    page.get_by_label("По", exact=True).fill(max(r["date"] for r in rows))
    page.get_by_role("button", name="Применить", exact=True).click()
    expect(page.locator("table.points tbody tr")).to_have_count(len(set(r["point_id"] for r in rows)))
    name = page.locator("table.points .point-btn").first.inner_text()
    page.locator("table.points .point-btn").first.click()
    expect(page.locator("table.points tbody tr")).to_have_count(1)
    expect(page.get_by_role("heading", name="Товары точки")).to_be_visible()
    selected = page.get_by_label("Торговая точка").input_value()
    point_net = sum(Decimal(r["net"].replace(",", ".")) for r in rows if r["point_id"] == selected)
    assert number(page.locator("article.kpi").first.locator(".kpi-value").inner_text()) == point_net
    page.get_by_role("button", name="Спросить о данных").click()
    daily = "Покажи выручку по дням за последние 30 дней"
    page.get_by_role("textbox", name="Ваш вопрос").fill(daily)
    with page.expect_response(lambda r: urlsplit(r.url).path == "/api/live/ask") as trend:
        page.get_by_role("button", name="Получить ответ").click()
    daily_result = trend.value.json()
    if not daily_result["meta"]["complete"]:
        assert "неполн" in daily_result["answer"].casefold()
        expect(page.get_by_role("heading", name=daily_result["answer"], exact=True)).to_be_visible()
        print("The partial 30-day answer explicitly labels its coverage")
    ask = "Где сильнее всего упала выручка за прошлую неделю?"
    page.get_by_role("textbox", name="Ваш вопрос").fill(ask)
    with page.expect_response(lambda r: urlsplit(r.url).path == "/api/live/ask") as answer:
        page.get_by_role("button", name="Получить ответ").click()
    report = answer.value.json()
    assert report["plan"]["filters"]["point"] == selected
    expect(page.get_by_role("heading", name=report["answer"], exact=True)).to_be_visible()
    page.get_by_role("button", name="Сохранить на дашборд").click()
    expect(page.get_by_text("Виджет сохранён", exact=True)).to_be_visible()
    page.get_by_role("button", name="Открыть виджеты").click()
    page.reload()
    expect(page.get_by_role("heading", name=report["answer"], exact=True).first).to_be_visible()
    print("Owner opened store products and persisted its weekly widget:", name.strip(), selected)


def exercise_token(page, old_token):
    def check(token):
        return page.evaluate(
            """async token => (await fetch("/api/import/check",{credentials:"omit",headers:{Authorization:"Bearer "+token}})).status""",
            token,
        )

    page.get_by_role("button", name="Отключить интеграцию", exact=True).click()
    page.get_by_role("button", name="Подтвердить отключение").click()
    expect(page.get_by_text("Приём остановлен. Токен отозван.", exact=True)).to_be_visible()
    assert check(old_token) == 401
    page.get_by_role("button", name="Выпустить новый токен", exact=True).click()
    page.get_by_role("button", name="Подтвердить выпуск").click()
    field = page.get_by_label("Новый токен загрузки", exact=True)
    expect(field).to_have_attribute("type", "password")
    token = field.input_value()
    assert token and token != old_token and check(old_token) == 401
    # 503 is a valid authenticated check for an intentionally unconfigured profile.
    assert check(token) in (200, 503)
    page.get_by_role("button", name="Закрыть токен", exact=True).click()
    expect(field).to_have_count(0)
    print(
        "Admin disabled import and rotated its token; old rejected, new authenticated, secret dismissed."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://localhost:8450")
    parser.add_argument(
        "--state", type=Path, default=Path(os.getenv("LOCALAPPDATA", ".")) / "San1C/pilot/qa"
    )
    parser.add_argument("--control", type=Path, default=ROOT / "deploy/pilot/qa_control.csv")
    parser.add_argument("--screenshot-dir", type=Path)
    parser.add_argument(
        "--owner-only", action="store_true", help="Native labs with an owner credential only"
    )
    parser.add_argument(
        "--exercise-token",
        action="store_true",
        help="Revoke and rotate the QA ingest credential. Operator must reissue the token afterwards.",
    )
    args = parser.parse_args()
    if urlsplit(args.url).scheme != "https" or urlsplit(args.url).hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        parser.error("Use the isolated local HTTPS pilot")
    if args.exercise_token:
        instance = json.loads((args.state / "instance.json").read_text(encoding="utf-8-sig"))
        if (
            args.owner_only
            or instance["company"] != "qa"
            or args.url.rstrip("/") != f"https://{instance['host']}:{instance['port']}"
        ):
            parser.error("Token mutation is only supported on the separate QA instance")
    with args.control.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))
    assert rows, "Independent CSV required"
    if args.screenshot_dir:
        args.screenshot_dir.mkdir(parents=True, exist_ok=True)
    credentials = json.loads((args.state / "credentials.json").read_text(encoding="utf-8-sig"))
    trusted = trust_directory(args.state.resolve())
    with tempfile.TemporaryDirectory(dir=args.state) as temp, sync_playwright() as p:
        shutil.copyfile(trusted / "cert9.db", Path(temp) / "cert9.db")
        browser = p.firefox.launch_persistent_context(
            temp,
            locale="ru-RU",
            viewport={"width": 1440, "height": 950},
            firefox_user_prefs={"security.enterprise_roots.enabled": False},
        )
        try:
            page = browser.pages[0]
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            login(
                page, args.url, credentials.get("owner_password") or credentials["web_password"], "owner"
            )
            expect(page.get_by_role("heading", name="Управление загрузкой")).to_have_count(0)
            source_coverage(page, rows, args.screenshot_dir)
            title = "Контрольная сверка UI " + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
            reconcile(page, rows, title, args.screenshot_dir)
            store_widget(page, rows)
            if args.screenshot_dir:
                page.screenshot(path=str(args.screenshot_dir / "owner-widget.png"))
            if not args.owner_only:
                page.get_by_role("button", name="Режим работы").click()
                page.get_by_role("button", name="Выйти", exact=True).click()
                login(page, args.url, credentials["admin_password"], "admin")
                expect(page.get_by_role("heading", name="Управление загрузкой")).to_be_visible()
                page.get_by_role("button", name="Выпустить новый токен", exact=True).click()
                expect(page.get_by_role("button", name="Подтвердить выпуск")).to_be_visible()
                page.get_by_role("button", name="Отмена", exact=True).click()
                print(
                    "Admin source management and confirmation accessible; owner has read-only source controls"
                )
                if args.exercise_token:
                    exercise_token(page, credentials["upload_token"])
            assert not errors, errors
        finally:
            browser.close()


if __name__ == "__main__":
    main()
