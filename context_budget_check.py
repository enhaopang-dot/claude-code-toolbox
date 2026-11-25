#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""固定注入开销检查。

Claude Code 每次新开会话，都会把这些东西塞进上下文：
  - CLAUDE.md（全局规则）
  - rules/*.md（常驻规则）
  - MEMORY.md（记忆索引）
  - 每个已安装 skill 的 name + description

它们是固定成本，不管你这次要干什么都得付。装了几十个 skill 之后，
这块会悄悄吃掉几万 token。这个脚本把账算清楚。

用法：
  python context_budget_check.py                    # 扫 ~/.claude
  python context_budget_check.py --root /path/to/.claude
  python context_budget_check.py --budget 30000 --verbose

输出 JSON，方便接进其他脚本或面板。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
from pathlib import Path

try:
    import tiktoken
    _ENC = tiktoken.get_encoding("cl100k_base")

    def count_tokens(text: str) -> int:
        return len(_ENC.encode(text))
except Exception:  # noqa: BLE001  tiktoken 不可用时回退启发式
    _ENC = None

    def count_tokens(text: str) -> int:
        cjk = len(re.findall(r"[一-鿿　-〿＀-￯]", text))
        other = len(text) - cjk
        return round(cjk + other / 4)


def est_tokens(path: Path) -> int:
    try:
        return count_tokens(path.read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        return 0


def skill_meta(path: Path) -> tuple[str | None, str]:
    """从 SKILL.md 的 frontmatter 取 (name, description)。"""
    try:
        txt = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None, ""
    m = re.search(r"^---\s*\n(.*?)\n---", txt, re.S)
    if not m:
        return None, ""
    fm = m.group(1)
    nm = re.search(r"^name:\s*(.+)$", fm, re.M)
    dm = re.search(r"^description:\s*(.*?)(?=\n[a-zA-Z_-]+:|\Z)", fm, re.S | re.M)
    name = nm.group(1).strip().strip("\"'") if nm else None
    desc = dm.group(1).strip().strip("\"'") if dm else ""
    return name, desc


def collect_skills(root: Path) -> list[tuple[str, int, str]]:
    """收集会被注入的 skill 元数据，按 name 去重。

    只统计真正注入的层级；examples/、benchmarks/ 这类素材目录不计入。
    """
    skills_dir = root / "skills"
    paths: list[Path] = []
    paths += sorted(skills_dir.glob("*/SKILL.md"))
    paths += sorted(skills_dir.glob("*/.agents/skills/*/SKILL.md"))
    paths += sorted(skills_dir.glob("*/skills/*/SKILL.md"))
    paths += sorted(root.glob("plugins/cache/*/*/*/skills/*/SKILL.md"))

    seen: dict[str, tuple[str, str]] = {}
    for p in paths:
        name, desc = skill_meta(p)
        if not name:
            name = p.parent.name
        if name in seen:
            continue
        seen[name] = (desc, str(p))
    rows = [(n, count_tokens(n + ": " + d), p) for n, (d, p) in seen.items()]
    return sorted(rows, key=lambda x: -x[1])


def find_memory_files(root: Path) -> list[Path]:
    """MEMORY.md 通常在 projects/<项目slug>/memory/ 下。

    每个项目各有一份，但一次会话只会注入当前目录对应的那一份。
    默认取最大的那份（通常就是主工作区的），要精确指定用 --memory。
    """
    found = sorted(root.glob("projects/*/memory/MEMORY.md"))
    if not found:
        fallback = root / "memory" / "MEMORY.md"
        if fallback.exists():
            found = [fallback]
    if len(found) > 1:
        found = [max(found, key=lambda p: p.stat().st_size)]
    return found


def main() -> int:
    ap = argparse.ArgumentParser(
        description="统计 Claude Code 的固定注入开销（CLAUDE.md + rules + MEMORY.md + skills）")
    ap.add_argument("--root", default=str(Path.home() / ".claude"),
                    help="Claude 配置目录，默认 ~/.claude")
    ap.add_argument("--budget", type=int, default=50000,
                    help="告警阈值（token），默认 50000")
    ap.add_argument("--memory", action="append", default=None,
                    help="手动指定 MEMORY.md 路径，可重复；不填则自动发现")
    ap.add_argument("--verbose", action="store_true", help="额外打印前 20 个最占空间的 skill")
    args = ap.parse_args()

    root = Path(args.root).expanduser()
    if not root.exists():
        print(f"[错误] 目录不存在: {root}")
        return 2

    memory_files = [Path(p) for p in args.memory] if args.memory else find_memory_files(root)
    rules = sorted((root / "rules").glob("*.md")) if (root / "rules").exists() else []

    breakdown = {
        "CLAUDE.md": est_tokens(root / "CLAUDE.md"),
        "MEMORY.md": sum(est_tokens(p) for p in memory_files),
        "rules": sum(est_tokens(p) for p in rules),
        "skills": sum(t for _, t, _ in collect_skills(root)),
    }
    total = sum(breakdown.values())

    skills = collect_skills(root)
    result = {
        "root": str(root),
        "total_tokens": total,
        "budget": args.budget,
        "over_budget": total > args.budget,
        "breakdown": breakdown,
        "skills": {
            "count": len(skills),
            "top": [{"name": n, "tokens": t} for n, t, _ in skills[:20 if args.verbose else 10]],
        },
        "meter": "tiktoken" if _ENC else "heuristic",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["over_budget"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
