"""Browser acceptance test. Run against the demo started with Docker Compose.

python -m pip install playwright
python frontend/tests/browser_smoke.py --browser msedge
An installed Edge is sufficient. For Chromium, use 'playwright install chromium'
and pass '--browser chromium'.
"""

import argparse
import csv
import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Page, expect, sync_playwright


QUESTIONS = [
    "Покажи выручку по дням за последние 30 дней",
    "Где сильнее всего упала выручка за прошлую неделю?",
    "Какие 5 товаров дали больше всего выручки за этот месяц?",
    "Покажи возвраты по точкам за прошлый месяц",
]


def number(cell):
    return Decimal(re.sub(r"[\s₸]", "", cell).replace(",", ".").replace("−", "-"))


def sales_after(page, action):
    with page.expect_response(lambda r: urlsplit(r.url).path == "/api/demo/sales") as pending:
        action()
    response = pending.value
    assert response.ok, response.status
    sales = response.json()
    table = page.locator("table.points tbody tr")
    expect(table).to_have_count(len(sales["points"]))
    for index, point in enumerate(sales["points"]):
        cells = table.nth(index).get_by_role("cell")
        expect(cells.nth(0)).to_have_text(point["name"])
        assert number(cells.nth(1).inner_text()) == Decimal(point["net"])
    chart = page.get_by_role("img", name="Динамика выручки").locator(".recharts-area-curve")
    expect(chart).to_have_attribute("d", re.compile("^M"))
    return sales, chart.get_attribute("d"), table.all_text_contents()


def exported_rows(page, panel):
    with page.expect_download() as download:
        panel.get_by_role("link", name="Скачать CSV").click()
    with download.value.path().open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=";"))


def check_store_products(page, products):
    panel = page.locator("section", has=page.locator(".rank-list"))
    assert len(products) > 5
    for toggle, expected in [(None, products[:5]), (re.compile("Показать все"), products), ("Свернуть", products[:5])]:
        if toggle:
            panel.get_by_role("button", name=toggle).click()
        expect(panel.locator(".rank-row strong")).to_have_text([p["name"] for p in expected])
        sums = [number(text) for text in panel.locator(".rank-row b").all_text_contents()]
        assert sums == [Decimal(p["net"]) for p in expected]
        csv_rows = exported_rows(page, panel)
        assert [r["Товар"] for r in csv_rows] == [p["name"] for p in expected]
        assert [Decimal(r["Выручка после возвратов"]) for r in csv_rows] == sums


def check_keyboard_focus(page):
    target = page.get_by_label("Период", exact=True)
    for _ in range(30):
        page.keyboard.press("Tab")
        if target.evaluate("el => el === document.activeElement"):
            break
    expect(target).to_be_focused()
    assert target.evaluate("""el => el.matches(':focus-visible') && [el, el.parentElement].some(node => {
        const style = getComputedStyle(node);
        return style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) >= 2;
    })"""), "The focus indicator is missing on the keyboard-selected period"


def check_sales(page: Page, base_url):
    data, curve, table = sales_after(page, lambda: page.goto(base_url + "/#/demo/sales"))
    assert len(data["daily"]) == 30 and len(table) >= 2
    expect(page.get_by_text("Демо-данные", exact=True).first).to_be_visible()
    expect(page.locator(".kpi-value")).to_have_count(4)
    check_keyboard_focus(page)
    point = data["points"][0]
    category = data["options"]["categories"][0]
    actions = [
        lambda: page.get_by_label("Период", exact=True).select_option("last_week"),
        lambda: page.get_by_role("button", name=point["name"], exact=True).click(),
        lambda: page.get_by_label("Категория", exact=True).select_option(category["id"]),
    ]
    for action in actions:
        data, new_curve, new_table = sales_after(page, action)
        assert new_curve != curve, "A filter left the plotted revenue series unchanged"
        assert new_table != table, "A filter left the sales table unchanged"
        curve, table = new_curve, new_table
        if data["filters"] == {"point": point["id"], "category": None}:
            check_store_products(page, data["products"])
    assert len(data["daily"]) == 7 and len(data["points"]) == 1
    assert data["filters"] == {"point": point["id"], "category": category["id"]}
    expect(page.get_by_label("Торговая точка", exact=True)).to_have_value(point["id"])
    visible_products = page.locator(".rank-row strong")
    expect(visible_products).to_have_text([p["name"] for p in data["products"][:5]])

    panel = page.locator("section", has=page.get_by_role("heading", name="Продажи по точкам"))
    rows = exported_rows(page, panel)
    assert len(rows) == 1 and rows[0]["Точка"] == point["name"]
    assert Decimal(rows[0]["Выручка после возвратов"]) == Decimal(data["kpis"]["net"]["value"])
    page.get_by_role("button", name="Таблица", exact=True).click()
    daily_rows = page.locator(".day-table tbody tr")
    expect(daily_rows).to_have_count(7)
    assert [number(row.get_by_role("cell").nth(3).inner_text()) for row in daily_rows.all()] == [
        Decimal(day["net"]) for day in data["daily"]
    ]
    page.get_by_role("button", name="Сбросить", exact=True).click()
    print("Sales: keyboard focus, 3 filters, chart, daily and point tables, full product breakdown, displayed CSV rows")


def ask(page, question, *, typed=False):
    if typed:
        page.get_by_role("textbox", name="Ваш вопрос").fill(question)
        submit = page.get_by_role("button", name="Получить ответ")
    else:
        submit = page.get_by_role("button", name=re.compile(re.escape(question)))
    with page.expect_response(lambda r: urlsplit(r.url).path == "/api/demo/ask") as pending:
        submit.click()
    assert pending.value.ok
    report = pending.value.json()
    expect(page.get_by_role("heading", name=report["answer"], exact=True)).to_be_visible()
    expect(page.locator(".report-table tbody tr")).to_have_count(len(report["rows"]))
    return report


def check_questions_and_widget(page):
    page.get_by_role("button", name="Спросить о данных").click()
    daily, week, top, returns = [ask(page, q) for q in QUESTIONS]
    assert daily["plan"]["group_by"] == "day" and len(daily["rows"]) == 30
    assert top["plan"]["group_by"] == "product" and len(top["rows"]) == 5
    assert returns["plan"]["metric"] == "returns" and returns["plan"]["group_by"] == "point"
    assert week["meta"]["complete"] and week["plan"]["comparison"] == "previous_week"
    current_start = date.fromisoformat(week["range"]["start"])
    assert current_start.weekday() == 0
    assert date.fromisoformat(week["range"]["end"]) == current_start + timedelta(days=6)
    assert date.fromisoformat(week["previous_range"]["end"]) == current_start - timedelta(days=1)
    deltas = [Decimal(row["value"]) - Decimal(row["previous"]) for row in week["rows"]]
    assert deltas == [Decimal(row["delta"]) for row in week["rows"]]
    assert min(deltas) < 0 and week["rows"][deltas.index(min(deltas))]["name"] in week["answer"]
    repeated = ask(page, QUESTIONS[1], typed=True)
    assert repeated["plan"] == week["plan"] and repeated["rows"] == week["rows"]
    assert repeated["answer"] == week["answer"]
    page.get_by_role("button", name="Сохранить на дашборд").click()
    expect(page.get_by_text("Виджет сохранён", exact=True)).to_be_visible()
    page.get_by_role("button", name="Открыть виджеты").click()
    expect(page.get_by_role("heading", name=week["answer"])).to_be_visible()
    page.reload()
    expect(page.get_by_role("heading", name=week["answer"])).to_be_visible()
    expect(page.locator(".widget > summary")).to_have_text(QUESTIONS[1])
    page.get_by_role("link", name="Продажи", exact=True).click()
    page.locator(".widget > summary").click()
    expect(page.get_by_role("heading", name=week["answer"])).to_be_visible()
    print("Questions: four examples, reproducible typed question, calendar boundaries, saved widget after reload and on sales")


def check_mobile(page):
    page.set_viewport_size({"width": 390, "height": 844})
    for route, heading in [("Продажи", "Обзор продаж"), ("Спросить", "Спросить о данных"),
                           ("Мои виджеты", "Мои виджеты"), ("Источник 1С", "Источник 1С")]:
        page.get_by_role("button", name="Меню", exact=True).click()
        page.get_by_role("link", name=route, exact=True).click()
        expect(page.get_by_role("heading", name=heading, level=1)).to_be_visible()
        if route == "Продажи":
            expect(page.locator(".kpi-value")).to_have_count(4)
            for label in ("Период", "Торговая точка", "Категория"):
                expect(page.get_by_label(label, exact=True)).to_be_visible()
            expect(page.locator(".trend-panel .subline")).to_be_visible()
            for view in ("Таблица", "График"):
                switch = page.get_by_role("button", name=view, exact=True)
                expect(switch).to_be_visible()
                switch.click()
                expect(switch).to_have_attribute("aria-pressed", "true")
        elif route == "Мои виджеты":
            expect(page.locator(".widget .report-table")).to_be_visible()
        elif route == "Источник 1С":
            expect(page.get_by_role("heading", name="Покрытие по дням")).to_be_visible()
        width = page.evaluate("document.documentElement.scrollWidth")
        assert width <= 390, f"{route}: page overflows mobile viewport ({width}px)"
    print("Mobile: all four screens, accessible filters, cards, dates, no page overflow at 390px")


def check_states(browser, base_url):
    page = browser.new_page(locale="ru-RU")
    page.goto(base_url + "/#/demo/widgets")
    expect(page.get_by_role("heading", name="Пока нет сохранённых виджетов")).to_be_visible()
    page.goto(base_url + "/#/live/source")
    expect(page.get_by_role("heading", name="Вход в рабочую базу")).to_be_visible()
    assert page.request.get(base_url + "/api/live/source").status == 401

    pending = []
    page.route("**/api/demo/sales?*", lambda route: pending.append(route), times=1)
    with page.expect_request(lambda request: urlsplit(request.url).path == "/api/demo/sales"):
        page.goto(base_url + "/#/demo/sales", wait_until="domcontentloaded")
    expect(page.get_by_role("status").filter(has_text="Загружаем данные")).to_be_visible()
    pending[0].fulfill(status=503, body="{}")
    expect(page.get_by_role("alert")).to_contain_text("Не удалось получить данные")
    page.get_by_role("button", name="Повторить").click()
    expect(page.locator(".kpi-value")).to_have_count(4)

    page.get_by_label("Период", exact=True).select_option("custom")
    page.get_by_label("С", exact=True).fill("2001-01-01")
    page.get_by_label("По", exact=True).fill("2001-01-07")
    page.get_by_role("button", name="Применить").click()
    expect(page.get_by_role("status").filter(has_text="Неполное покрытие данных")).to_be_visible()
    expect(page.get_by_text("За выбранный период операций нет.", exact=True).first).to_be_visible()
    page.close()
    print("States: empty dashboard, live access denied, loading, failed request, retry, incomplete date range")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8088")
    parser.add_argument("--browser", choices=["msedge", "chromium"], default="msedge")
    parser.add_argument("--screenshots", type=Path, help="Directory for clean desktop and mobile sales screenshots")
    args = parser.parse_args()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=args.browser)
        if args.screenshots:
            args.screenshots.mkdir(parents=True, exist_ok=True)
            for label, width, height in [("desktop", 1440, 900), ("mobile", 390, 844)]:
                clean = browser.new_page(viewport={"width": width, "height": height}, locale="ru-RU")
                sales_after(clean, lambda: clean.goto(args.url.rstrip("/") + "/#/demo/sales"))
                clean.evaluate("document.fonts.ready")
                clean.screenshot(path=args.screenshots / f"{label}.png", animations="disabled")
                clean.close()
        check_states(browser, args.url.rstrip("/"))
        context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="ru-RU")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        check_sales(page, args.url.rstrip("/"))
        check_questions_and_widget(page)
        check_mobile(page)
        assert not errors, errors
        context.close()
        browser.close()


if __name__ == "__main__":
    main()
