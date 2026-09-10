from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "collection.september-2026.json"
DEFAULT_OUTPUT = ROOT / "data" / "september-2026" / "collection-plan.json"


def parse_day(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit(f"invalid {field}: {value!r}; expected YYYY-MM-DD") from exc


def normalized_unique(values: list[str], field: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = raw.strip()
        if not value:
            raise SystemExit(f"{field} contains an empty value")
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def load_company_registry(path: Path) -> tuple[list[dict], list[dict]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    enabled: list[dict] = []
    unresolved: list[dict] = []
    canonical_seen: set[str] = set()

    for index, raw in enumerate(payload.get("companies", []), start=1):
        canonical = str(raw.get("canonical", "")).strip()
        if not canonical:
            raise SystemExit(f"company registry item {index} has no canonical name")
        canonical_key = canonical.casefold()
        if canonical_key in canonical_seen:
            raise SystemExit(f"duplicate canonical company: {canonical}")
        canonical_seen.add(canonical_key)

        status = raw.get("status", "verified")
        if status != "verified" or not raw.get("enabled", True):
            unresolved.append(raw)
            continue

        selectors = normalized_unique(
            raw.get("selectors", []), f"selectors for {canonical}"
        )
        if not selectors:
            raise SystemExit(f"verified company has no source selector: {canonical}")
        enabled.append({**raw, "canonical": canonical, "selectors": selectors})

    if not enabled:
        raise SystemExit("company registry has no enabled, verified companies")
    return enabled, unresolved


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build hard-scoped Nowcoder company/category collection units."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    source = config["source"]
    window = config["window"]

    if source.get("companyFilterMode") != "source-ui-required":
        raise SystemExit("companyFilterMode must be 'source-ui-required'")
    if not source.get("rejectUnfilteredQueries"):
        raise SystemExit("rejectUnfilteredQueries must remain true")

    registry_value = source.get("companyRegistry")
    if not registry_value:
        raise SystemExit("source.companyRegistry is required")
    registry_path = (args.config.parent / registry_value).resolve()
    companies, unresolved = load_company_registry(registry_path)
    categories = normalized_unique(source.get("categories", []), "source.categories")
    if not categories:
        raise SystemExit("category list is empty")

    start = parse_day(window["startInclusive"], "window.startInclusive")
    end = parse_day(window["endInclusive"], "window.endInclusive")
    if start > end:
        raise SystemExit("window.startInclusive must not be after endInclusive")

    today = date.today()
    effective_end = min(today, end)
    units = []
    for company in companies:
        for category in categories:
            units.append(
                {
                    "id": f"company-{len(units) + 1:03d}",
                    "company": company["canonical"],
                    "sourceCompanySelectors": company["selectors"],
                    "category": category,
                    "sort": source["sort"],
                    "sourceCompanySelectionRequired": True,
                    "collectUntilPublishedBefore": start.isoformat(),
                }
            )

    output = {
        "name": config["name"],
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "targetWindow": {
            "startInclusive": start.isoformat(),
            "endInclusive": end.isoformat(),
            "timezone": window["timezone"],
        },
        "currentlyCollectableThrough": effective_end.isoformat(),
        "monthComplete": today > end,
        "companyCount": len(companies),
        "sourceSelectorCount": sum(len(item["selectors"]) for item in companies),
        "unresolvedCompanies": [item["canonical"] for item in unresolved],
        "categoryCount": len(categories),
        "unitCount": len(units),
        "units": units,
        "deduplication": config["deduplication"],
        "llm": config["llm"],
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"built {len(units)} units for {len(companies)} company groups x "
        f"{len(categories)} categories -> {args.output}"
    )


if __name__ == "__main__":
    main()
