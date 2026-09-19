from __future__ import annotations

"""Collect the planned Nowcoder units through the site's UI backing APIs.

This deliberately uses only identifiers returned by the company-suggestion and
job-selector endpoints.  It never falls back to a keyword or unfiltered feed.
"""

import argparse
import json
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen



ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "september-2026"
PLAN_PATH = DATA / "collection-plan.json"
STATUS_PATH = DATA / "collection-status.json"
CANDIDATES_PATH = DATA / "candidates.json"
GATEWAY = "https://gw-c.nowcoder.com"
WWW = "https://www.nowcoder.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.nowcoder.com/interview/center",
    "Origin": "https://www.nowcoder.com",
}
CATEGORIES = {
    "软件开发 / 人工智能/算法": (11240, 2),
    "软件开发 / 后端开发": (11200, 2),
}
CHINA_TZ = timezone(timedelta(hours=8))


def normalized_selector(value: str) -> str:
    """Normalize display-only Unicode without weakening company equality.

    Nowcoder currently returns the DeepSeek legal name with a trailing U+200C.
    Unicode format controls are invisible and are not part of the legal company
    name, so remove only category Cf characters before exact comparison.
    """
    normalized = unicodedata.normalize("NFKC", value or "")
    return "".join(char for char in normalized if unicodedata.category(char) != "Cf").strip()


def get_time(record: dict[str, Any]) -> int | None:
    item = record.get("momentData") or record.get("contentData") or {}
    return item.get("createdAt") or item.get("createTime") or item.get("showTime")


def candidate(record: dict[str, Any], unit: dict[str, Any], selector: str) -> dict[str, Any] | None:
    if record.get("contentType") == 74:
        moment = record.get("momentData") or {}
        uuid = moment.get("uuid")
        title = moment.get("title") or moment.get("newTitle") or ""
        url = f"{WWW}/feed/main/detail/{uuid}" if uuid else None
    elif record.get("contentType") == 250:
        content = record.get("contentData") or {}
        content_id = content.get("entityId") or content.get("id")
        title = content.get("title") or content.get("newTitle") or ""
        url = f"{WWW}/discuss/{content_id}" if content_id else None
    else:
        return None
    if not url:
        return None
    published = get_time(record)
    published_at = datetime.fromtimestamp(published / 1000, timezone.utc).isoformat() if published else None
    return {
        "url": url,
        "title": title,
        "company": unit["company"],
        "sourceCompanySelector": selector,
        "category": unit["category"],
        "publishedAt": published_at,
        "unitId": unit["id"],
        "collectionStartInclusive": unit.get("startInclusive"),
    }


def initialize_status(plan: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "planName": plan["name"],
        "generatedAt": now,
        "sourceAccess": "Nowcoder UI-backed API; pending per-unit verification",
        "updatedAt": now,
        "units": [
            {
                "id": unit["id"],
                "company": unit["company"],
                "category": unit["category"],
                "sort": unit["sort"],
                "startInclusive": unit.get("startInclusive"),
                "sourceCompanySelectionRequired": True,
                "status": "pending",
                "reason": None,
                "attempts": 0,
                "updatedAt": None,
            }
            for unit in plan["units"]
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--refresh-completed",
        action="store_true",
        help="revisit completed units to collect posts published since the previous run",
    )
    parser.add_argument(
        "--unit-id",
        action="append",
        default=[],
        help="limit collection to one or more plan unit IDs",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="start a new retry budget for failed units after the blocking cause was fixed",
    )
    args = parser.parse_args()

    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    configured_end = datetime.fromisoformat(plan["targetWindow"]["endInclusive"]).date()
    collectable_through = min(configured_end, datetime.now(CHINA_TZ).date())
    end_ms = int(datetime.combine(collectable_through, datetime.max.time(), CHINA_TZ).timestamp() * 1000)
    selected_units = set(args.unit_id)
    status_doc = (
        json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        if STATUS_PATH.exists()
        else initialize_status(plan)
    )
    status_by_id = {row["id"]: row for row in status_doc["units"]}
    for unit in plan["units"]:
        if unit["id"] not in status_by_id:
            row = initialize_status({"name": plan["name"], "units": [unit]})["units"][0]
            status_doc["units"].append(row)
            status_by_id[unit["id"]] = row
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(status_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    existing = json.loads(CANDIDATES_PATH.read_text(encoding="utf-8")) if CANDIDATES_PATH.exists() else {"run": {}, "candidates": []}
    rows_by_url = {row["url"]: row for row in existing.get("candidates", [])}
    selector_cache: dict[str, tuple[list[int], str]] = {}

    def request_json(url: str, *, params: dict[str, Any] | None = None, body: dict[str, Any] | None = None) -> dict[str, Any]:
        if params:
            url = f"{url}?{urlencode(params)}"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        headers = dict(HEADERS)
        if data is not None:
            headers["Content-Type"] = "application/json"
        with urlopen(Request(url, data=data, headers=headers, method="POST" if data is not None else "GET"), timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def company_ids(unit: dict[str, Any]) -> tuple[list[int], str]:
        key = "\u0000".join(unit["sourceCompanySelectors"])
        if key in selector_cache:
            return selector_cache[key]
        ids: list[int] = []
        matched: list[str] = []
        for selector in unit["sourceCompanySelectors"]:
            payload = request_json(f"{GATEWAY}/api/sparta/job-experience/company/name/suggest", params={"name": selector})
            options = payload.get("data", {}).get("result", [])
            exact = [
                option
                for option in options
                if normalized_selector(option.get("companyName", "")) == normalized_selector(selector)
            ]
            if exact:
                ids.extend(int(option["companyId"]) for option in exact)
                matched.extend(option["companyName"] for option in exact)
        ids = list(dict.fromkeys(ids))
        if not ids:
            raise RuntimeError("no exact source company selector resolved")
        evidence = f"company/name/suggest exact selectors={matched}; companyIds={ids}"
        selector_cache[key] = (ids, evidence)
        return selector_cache[key]

    for unit in plan["units"]:
        if selected_units and unit["id"] not in selected_units:
            continue
        state = status_by_id[unit["id"]]
        if state.get("status") == "completed" and not args.refresh_completed:
            continue
        attempts = int(state.get("attempts", 0))
        if attempts >= 3 and not args.retry_failed:
            continue
        if attempts >= 3:
            attempts = 0
        now = datetime.now(timezone.utc).isoformat()
        try:
            start_date = datetime.fromisoformat(
                unit.get("startInclusive")
                or unit.get("collectUntilPublishedBefore")
                or plan["targetWindow"]["startInclusive"]
            ).date()
            start_ms = int(
                datetime.combine(start_date, datetime.min.time(), CHINA_TZ).timestamp()
                * 1000
            )
            job_id, level = CATEGORIES[unit["category"]]
            ids, selector_evidence = company_ids(unit)
            collected = 0
            crossed_window = False
            page = 1
            selected = unit["sourceCompanySelectors"][0]
            while collected < 30 and page <= 100:
                body = {"companyList": ids, "jobId": job_id, "level": level, "order": 3, "page": page, "isNewJob": True}
                payload = request_json(f"{GATEWAY}/api/sparta/job-experience/experience/job/list", body=body)
                if payload.get("code") != 0:
                    raise RuntimeError(f"list API rejected query: {payload.get('msg')}")
                records = payload.get("data", {}).get("records", [])
                if not records:
                    crossed_window = True
                    break
                # The server-side query is the same body used by the UI.  Preserve it
                # as audit evidence; no title-based company filtering is used here.
                for record in records:
                    published = get_time(record)
                    if published is not None and published < start_ms:
                        crossed_window = True
                        break
                    if published is None or published > end_ms:
                        continue
                    item = candidate(record, unit, selected)
                    if item:
                        rows_by_url.setdefault(item["url"], item)
                        collected += 1
                        if collected >= 30:
                            break
                if crossed_window or collected >= 30 or page >= int(payload.get("data", {}).get("totalPage", page)):
                    break
                page += 1
                time.sleep(0.2)
            state.update({
                "startInclusive": start_date.isoformat(),
                "status": "completed",
                "reason": f"verified source-filter query: {selector_evidence}; jobId={job_id}; level={level}; order=3 (最新); pages={page}; crossedBefore{start_date.isoformat()}={crossed_window}; collectableThrough={collectable_through.isoformat()}; candidatesInWindow={collected}",
                "attempts": 0,
                "updatedAt": now,
            })
        except Exception as exc:
            state.update({"status": "failed", "reason": f"filtered collection failed: {type(exc).__name__}: {exc}", "attempts": attempts + 1, "updatedAt": now})
        existing["run"] = {"mode": "full", "updatedAt": now}
        existing["candidates"] = list(rows_by_url.values())
        CANDIDATES_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        status_doc["sourceAccess"] = (
            "verified Nowcoder UI-backed API; exact selectors and companyIds "
            "recorded in per-unit evidence"
        )
        status_doc["updatedAt"] = now
        STATUS_PATH.write_text(json.dumps(status_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{unit['id']} {state['status']} {state['reason']}", flush=True)
        time.sleep(0.15)


if __name__ == "__main__":
    main()
