from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2] / "output" / "秋招准备材料"

PROJECT_FILES = [
    "01_项目面试_阿里_EduBot多Agent路由与任务编排.md",
    "02_项目面试_阿里_主动服务系统.md",
    "03_项目面试_高途_智能搜索对话系统.md",
    "04_项目面试_高途_用户画像与长期记忆.md",
    "05_项目面试_高途_多模态标签服务.md",
]

HANDBOOK_FILE = "06_技术栈八股与项目追问手册.md"


def shift_headings(text: str) -> str:
    return re.sub(
        r"^(#{1,5})(\s+)",
        lambda match: "#" + match.group(1) + match.group(2),
        text,
        flags=re.MULTILINE,
    )


def normalize_lists(text: str) -> str:
    return re.sub(r"^(\s*)-\s{3}", r"\1- ", text, flags=re.MULTILINE)


def normalize_project(path: Path) -> None:
    # Re-read the exact current file immediately before rewriting it. This is
    # the sole editing baseline so concurrent user changes are preserved.
    current = path.read_text(encoding="utf-8")
    if current.startswith("# "):
        return

    pattern = re.compile(
        r"^\*\*(PROJECT INTERVIEW BRIEF \d+)\*\*\n\n"
        r"([^\n]+)\n\n"
        r"([^\n]+)\n\n"
        r"([^\n]+)\n\n"
        r"> \*\*30 秒版本\*\* (.+?)\n\n"
        r"2 分钟版本 (.+?)\n\n"
        r"(?=# )",
        flags=re.DOTALL,
    )
    match = pattern.match(current)
    if match is None:
        raise RuntimeError(f"无法识别项目材料开头结构：{path.name}")

    label, title, subtitle, metadata, short_intro, long_intro = match.groups()
    body = normalize_lists(shift_headings(current[match.end() :].strip()))
    normalized = (
        f"# {title.strip()}\n\n"
        f"> {label}｜{subtitle.strip()}\n\n"
        f"{metadata.strip()}\n\n"
        "## 项目介绍口播\n\n"
        "### 30 秒版本\n\n"
        f"{short_intro.strip()}\n\n"
        "### 2 分钟版本\n\n"
        f"{long_intro.strip()}\n\n"
        f"{body}\n"
    )
    path.write_text(normalized, encoding="utf-8", newline="\n")


def normalize_handbook(path: Path) -> None:
    # Re-read the exact current file immediately before rewriting it.
    current = path.read_text(encoding="utf-8")
    if current.startswith("# "):
        return

    pattern = re.compile(
        r"^\*\*(TECHNICAL INTERVIEW HANDBOOK)\*\*\n\n"
        r"([^\n]+)\n\n"
        r"([^\n]+)\n\n"
        r"([^\n]+)\n\n"
        r"> \*\*使用方法\*\* (.+?)\n\n"
        r"(?=# )",
        flags=re.DOTALL,
    )
    match = pattern.match(current)
    if match is None:
        raise RuntimeError(f"无法识别技术手册开头结构：{path.name}")

    label, title, subtitle, metadata, usage = match.groups()
    body = normalize_lists(shift_headings(current[match.end() :].strip()))
    normalized = (
        f"# {title.strip()}\n\n"
        f"> {label}｜{subtitle.strip()}\n\n"
        f"{metadata.strip()}\n\n"
        "## 使用方法\n\n"
        f"{usage.strip()}\n\n"
        f"{body}\n"
    )
    path.write_text(normalized, encoding="utf-8", newline="\n")


def main() -> None:
    for filename in PROJECT_FILES:
        path = ROOT / filename
        normalize_project(path)
        print(f"NORMALIZED\t{filename}")

    handbook = ROOT / HANDBOOK_FILE
    normalize_handbook(handbook)
    print(f"NORMALIZED\t{HANDBOOK_FILE}")


if __name__ == "__main__":
    main()
