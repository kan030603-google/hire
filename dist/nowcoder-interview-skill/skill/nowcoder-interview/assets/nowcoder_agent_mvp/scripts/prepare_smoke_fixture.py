from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "data" / "september-2026" / "posts.jsonl"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "smoke-test"

SIGNALS = (
    "agent",
    "rag",
    "mcp",
    "大模型",
    "llm",
    "智能体",
    "langchain",
    "langgraph",
    "prompt",
    "ai应用",
    "ai 应用",
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
        if row.get("body") and not row.get("fetchError"):
            rows.append(row)
    return rows


def relevance_score(row: dict[str, Any]) -> tuple[int, int]:
    text = f"{row.get('title', '')}\n{row.get('pageTitle', '')}\n{row.get('body', '')}".casefold()
    score = sum(text.count(signal.casefold()) for signal in SIGNALS)
    return score, len(str(row.get("body", "")))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create isolated, relevant input for the skill smoke test."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()

    if args.limit < 1:
        raise SystemExit("--limit must be positive")

    rows = load_jsonl(args.source)
    ranked = sorted(rows, key=relevance_score, reverse=True)
    selected = [row for row in ranked if relevance_score(row)[0] > 0][: args.limit]
    if len(selected) < args.limit:
        raise SystemExit(
            f"only {len(selected)} relevant successful rows available; need {args.limit}"
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    posts_path = args.output_dir / "posts.jsonl"
    candidates_path = args.output_dir / "candidates.json"
    state_path = args.output_dir / "run-state.json"
    now = datetime.now().astimezone().isoformat(timespec="seconds")

    posts_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in selected),
        encoding="utf-8",
    )
    candidates_path.write_text(
        json.dumps(
            {
                "run": {
                    "mode": "smoke",
                    "source": str(args.source),
                    "startedAt": now,
                    "updatedAt": now,
                },
                "candidates": [
                    {
                        "url": row["url"],
                        "title": row.get("pageTitle") or row.get("title") or "",
                        "company": row.get("company"),
                        "sourceCompanySelector": row.get("sourceCompanySelector"),
                        "category": row.get("category"),
                        "publishedAt": row.get("createdAt"),
                        "unitId": row.get("unitId"),
                    }
                    for row in selected
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    state_path.write_text(
        json.dumps(
            {
                "mode": "smoke",
                "status": "fixture-ready",
                "updatedAt": now,
                "stages": {
                    "fixture": {"status": "complete", "count": len(selected)},
                    "extraction": {"status": "pending", "model": "gpt-5.6-luna"},
                    "organization": {"status": "pending", "model": "gpt-5.6-terra"},
                    "review": {"status": "pending", "model": "gpt-5.6-sol"},
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"prepared {len(selected)} posts in {args.output_dir}")


if __name__ == "__main__":
    main()
