#!/usr/bin/env python3
"""Create a writable Nowcoder collection workspace from the bundled template."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = SKILL_ROOT / "assets" / "nowcoder_agent_mvp"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create nowcoder_agent_mvp in a writable workspace."
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path.cwd(),
        help="Workspace root that will receive nowcoder_agent_mvp (default: cwd).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    destination = args.destination.expanduser().resolve()
    target = destination / "nowcoder_agent_mvp"

    if not TEMPLATE.is_dir():
        raise SystemExit(f"Bundled template is missing: {TEMPLATE}")
    if target.exists():
        raise SystemExit(
            f"Refusing to overwrite existing workspace: {target}\n"
            "Choose another destination or use the existing workspace."
        )

    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(TEMPLATE, target)
    for relative in ("data/september-2026", "data/smoke-test", "output"):
        (target / relative).mkdir(parents=True, exist_ok=True)

    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
