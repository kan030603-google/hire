from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data" / "candidates.json"
DEFAULT_OUTPUT = ROOT / "data" / "posts.jsonl"


def parse_initial_state(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    script = next(
        (
            node.string or ""
            for node in soup.find_all("script")
            if "__INITIAL_STATE__" in (node.string or "")
        ),
        "",
    )
    if not script:
        raise ValueError("page does not contain window.__INITIAL_STATE__")
    payload = script.split("=", 1)[1]
    return json.JSONDecoder().raw_decode(payload)[0]


def find_content_data(state: dict[str, Any]) -> dict[str, Any]:
    prefetch = state.get("prefetchData", {})
    for value in prefetch.values():
        if not isinstance(value, dict):
            continue
        common = value.get("ssrCommonData")
        if isinstance(common, dict) and isinstance(common.get("contentData"), dict):
            return common["contentData"]
    raise ValueError("contentData not found in initial state")


def html_to_text(fragment: str) -> str:
    soup = BeautifulSoup(fragment or "", "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    text = soup.get_text("\n", strip=True)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def load_existing(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows[row["url"]] = row
    return rows


def write_rows(path: Path, ordered_urls: list[str], rows: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for url in ordered_urls:
            if url in rows:
                handle.write(json.dumps(rows[url], ensure_ascii=False) + "\n")


def fetch_one(session: requests.Session, item: dict[str, Any], timeout: float) -> dict[str, Any]:
    response = session.get(item["url"], timeout=timeout)
    response.raise_for_status()
    state = parse_initial_state(response.text)
    content = find_content_data(state)
    return {
        **item,
        "pageTitle": content.get("title") or item.get("title") or "",
        "body": html_to_text(content.get("content") or ""),
        # Feed posts use createdAt; discuss posts currently expose createTime.
        "createdAt": (
            content.get("createdAt")
            or content.get("createTime")
            or content.get("showTime")
        ),
        "contentId": content.get("uuid") or content.get("id"),
        "httpStatus": response.status_code,
        "fetchError": None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--delay", type=float, default=0.35)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    candidates = payload["candidates"]
    ordered_urls = [item["url"] for item in candidates]
    rows = {} if args.refresh else load_existing(args.output)

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/140.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
        }
    )

    total = len(candidates)
    for index, item in enumerate(candidates, start=1):
        if item["url"] in rows and not rows[item["url"]].get("fetchError"):
            print(f"[{index:02d}/{total}] cached {item['title']}", flush=True)
            continue

        error = ""
        for attempt in range(1, args.retries + 1):
            try:
                rows[item["url"]] = fetch_one(session, item, args.timeout)
                print(
                    f"[{index:02d}/{total}] ok {len(rows[item['url']]['body']):5d} chars "
                    f"{item['title']}",
                    flush=True,
                )
                break
            except Exception as exc:  # keep a resumable failure record
                error = f"{type(exc).__name__}: {exc}"
                print(
                    f"[{index:02d}/{total}] attempt {attempt}/{args.retries} failed: {error}",
                    flush=True,
                )
                if attempt < args.retries:
                    time.sleep(max(args.delay, 1.0) * attempt)
        else:
            rows[item["url"]] = {
                **item,
                "pageTitle": item.get("title", ""),
                "body": "",
                "createdAt": None,
                "contentId": None,
                "httpStatus": None,
                "fetchError": error,
            }

        write_rows(args.output, ordered_urls, rows)
        time.sleep(args.delay)

    ok = sum(not row.get("fetchError") for row in rows.values())
    print(f"done: {ok}/{total} successful -> {args.output}")


if __name__ == "__main__":
    main()
