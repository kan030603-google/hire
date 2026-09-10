from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COLLECTION_CONFIG = ROOT / "config" / "collection.september-2026.json"
CHINA_TZ = timezone(timedelta(hours=8))

# Product or business-unit names that commonly appear in interview titles but are
# not necessarily valid company-selector labels. Registry names are added below.
TITLE_BRAND_ALIASES = {
    "抖音": "字节跳动",
    "tiktok": "字节跳动",
    "火山引擎": "字节跳动",
    "阿里云": "阿里巴巴",
    "达摩院": "阿里巴巴",
    "腾讯云": "腾讯",
    "网易有道": "网易",
    "华为云": "华为",
    "百度云": "百度",
    "心动": "TapTap/心动",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def parse_created_at(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, CHINA_TZ)
    if isinstance(value, str):
        text_value = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text_value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=CHINA_TZ)
        return parsed.astimezone(CHINA_TZ)
    return None


def content_fingerprint(body: str) -> str:
    normalized = "".join(char for char in body.casefold() if char.isalnum())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def build_title_aliases(registry: dict[str, Any]) -> dict[str, str]:
    aliases = dict(TITLE_BRAND_ALIASES)
    for company in registry["companies"]:
        canonical = company["canonical"]
        candidates = [canonical, company.get("input", ""), *company.get("selectors", [])]
        for candidate in candidates:
            candidate = candidate.strip()
            if len(candidate) >= 2:
                aliases[candidate.casefold()] = canonical
    return aliases


def explicit_title_companies(title: str, aliases: dict[str, str]) -> set[str]:
    folded = title.casefold()
    return {
        canonical
        for alias, canonical in aliases.items()
        if alias and re.search(re.escape(alias), folded)
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rejects", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--expected-company", required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_COLLECTION_CONFIG)
    args = parser.parse_args()

    collection = json.loads(args.config.read_text(encoding="utf-8"))
    registry_path = args.config.parent / collection["source"]["companyRegistry"]
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    aliases = build_title_aliases(registry)

    start_date = datetime.fromisoformat(collection["window"]["startInclusive"]).date()
    end_date = datetime.fromisoformat(collection["window"]["endInclusive"]).date()
    start_at = datetime.combine(start_date, time.min, CHINA_TZ)
    end_at = datetime.combine(end_date, time.max, CHINA_TZ)

    rows = read_jsonl(args.input)
    fingerprints = [content_fingerprint(row.get("body", "")) for row in rows]
    fingerprint_counts = Counter(value for value in fingerprints if value)
    duplicate_groups_observed = sum(count > 1 for count in fingerprint_counts.values())

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen_fingerprints: set[str] = set()
    reason_counts: Counter[str] = Counter()

    for row, fingerprint in zip(rows, fingerprints, strict=True):
        reasons: list[str] = []
        created_at = parse_created_at(row.get("createdAt"))
        title_companies = explicit_title_companies(row.get("pageTitle", ""), aliases)

        if row.get("fetchError") or not row.get("body"):
            reasons.append("fetch-failed-or-empty")
        if created_at is None:
            reasons.append("publication-date-missing")
        elif not start_at <= created_at <= end_at:
            reasons.append("outside-date-window")
        if title_companies and args.expected_company not in title_companies:
            reasons.append("explicit-other-company-in-title")
        if fingerprint and fingerprint in seen_fingerprints:
            reasons.append("duplicate-content")

        if reasons:
            for reason in reasons:
                reason_counts[reason] += 1
            rejected.append(
                {
                    **row,
                    "contentFingerprint": fingerprint,
                    "detectedTitleCompanies": sorted(title_companies),
                    "rejectionReasons": reasons,
                }
            )
            continue

        seen_fingerprints.add(fingerprint)
        accepted.append(
            {
                **row,
                "contentFingerprint": fingerprint,
                "detectedTitleCompanies": sorted(title_companies),
                "attributionStatus": (
                    "explicit-match" if args.expected_company in title_companies else "selector-only"
                ),
            }
        )

    write_jsonl(args.output, accepted)
    write_jsonl(args.rejects, rejected)
    summary = {
        "inputCount": len(rows),
        "acceptedCount": len(accepted),
        "rejectedCount": len(rejected),
        "reasonCounts": dict(sorted(reason_counts.items())),
        "duplicateGroupsObserved": duplicate_groups_observed,
        "uniqueAcceptedFingerprints": len(seen_fingerprints),
        "expectedCompany": args.expected_company,
        "window": {
            "startInclusive": start_date.isoformat(),
            "endInclusive": end_date.isoformat(),
            "timezone": "Asia/Shanghai",
        },
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
