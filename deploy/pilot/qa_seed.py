"""Send synthetic days only to the dedicated qa instance on localhost:8450."""

import argparse
import json
from datetime import date, timedelta
from urllib.request import Request

from pilot import company_dir, read_json, web_client, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-only", action="store_true")
    args = parser.parse_args()
    folder = company_dir("qa")
    cfg = read_json(folder / "instance.json")
    if (cfg["company"], cfg["host"], cfg["port"], cfg["internal"]) != ("qa", "localhost", 8450, True):
        raise SystemExit("The fixed test target must be qa at https://localhost:8450.")
    profile_file = folder / cfg["profile"] / "profile.json"
    profile = read_json(profile_file)
    profile["pilot_points"] = [{"id": "qa-" + point, "name": "Учебная точка " + point.upper()} for point in ("a", "b")]
    write_json(profile_file, profile)
    if args.profile_only:
        return
    token = read_json(folder / "credentials.json")["upload_token"]
    if not token:
        raise SystemExit("Upload is disabled.")
    client = web_client(folder)
    for index in range(14):
        day = date(2026, 11, 9) + timedelta(days=index)
        amounts = [(10000, 500), (6000, 1000)] if index < 7 else [(7500, 500), (6500, 400)]
        rows = []
        for point, (sales, returns) in zip(["a", "b"], amounts):
            if index == 13:
                break
            for operation, amount in (("sale", sales), ("return", returns)):
                doc = f"qa-{day}-{point}-{operation}"
                rows.append({
                    "business_date": str(day), "line_id": doc + "/1", "document_id": doc,
                    "store_id": "qa-" + point, "store_name": "Учебная точка " + point.upper(),
                    "product_id": "qa-item", "product_name": "Учебный товар", "category_id": "qa-category",
                    "category_name": "Учебная категория", "operation": operation, "quantity": "1", "amount": str(amount),
                })
        stamp = str(day + timedelta(days=1)) + "T01:00:00Z"
        body = {"business_date": str(day), "generated_at": stamp, "complete": True, "row_count": len(rows), "rows": rows}
        request = Request("https://localhost:8450/api/import/days", data=json.dumps(body).encode(), headers={
            "Authorization": "Bearer " + token, "Content-Type": "application/json",
            "X-Business-Date": str(day), "X-Snapshot-Generated-At": stamp,
        })
        with client.open(request, timeout=15) as reply:
            receipt = json.load(reply)
        assert receipt["status"] in ("accepted", "unchanged") and receipt["row_count"] == len(rows)
        print(day, receipt["status"], receipt["row_count"])


if __name__ == "__main__":
    main()
