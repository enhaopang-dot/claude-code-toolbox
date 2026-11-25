#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""出站前扫描：文件里有没有不该带出去的东西。

在把东西发到公开仓库、发给客户、部署上服务器之前跑一遍。
查七类：API 密钥、密钥赋值、Bearer 令牌、内网 IP、本机绝对路径、手机号、身份证号。

用法：
  python outbound_scan.py <文件或目录> [更多路径...]
  python outbound_scan.py . --strict

退出码：0 = 干净，1 = 有命中，2 = 参数错误
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows GBK 终端防崩
except Exception:  # noqa: BLE001
    pass

# 高信号模式：命中基本就是真的，不需要人工判断
STRICT_PATTERNS = [
    ("API密钥(sk-)", re.compile(r"sk-[A-Za-z0-9_\-]{16,}")),
    ("GitHub令牌", re.compile(r"(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}")),
    ("AWS密钥", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("私钥文件头", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("Bearer令牌", re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}")),
]

# 宽松模式：会有误报，靠人看一眼
LOOSE_PATTERNS = [
    ("密钥赋值(token/key/secret/password)", re.compile(
        r"(?i)(?:token|api[_-]?key|secret|password|passwd|access[_-]?key)\s*[:=]\s*[\"']?[^\s\"']{8,}")),
    ("内网IP", re.compile(r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")),
    ("本机绝对路径", re.compile(r"(?:[A-Za-z]:[\\/]Users[\\/][^\s\"'<>,]+|/home/[^\s\"'<>,]+)")),
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("身份证号", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
]

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
TEXT_EXT = {".md", ".txt", ".html", ".htm", ".css", ".js", ".ts", ".json", ".yaml",
            ".yml", ".py", ".sh", ".sql", ".csv", ".xml", ".conf", ".ini", ".env",
            ".vue", ".jsx", ".tsx", ".toml", ".cfg", ""}
MAX_SIZE = 5 * 1024 * 1024


def scan_file(path: Path, patterns) -> list[tuple[int, str, str]]:
    try:
        if path.stat().st_size > MAX_SIZE:
            return []
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for name, pat in patterns:
            for m in pat.finditer(line):
                hits.append((lineno, name, m.group(0)[:60]))
    return hits


def iter_files(target: Path):
    if target.is_file():
        yield target
        return
    for p in target.rglob("*"):
        if p.is_file() and not (set(p.parts) & SKIP_DIRS) and p.suffix.lower() in TEXT_EXT:
            yield p


def main() -> int:
    ap = argparse.ArgumentParser(
        description="出站前扫描：查密钥、令牌、内网 IP、本机路径、手机号、身份证号")
    ap.add_argument("paths", nargs="+", help="要扫的文件或目录")
    ap.add_argument("--strict", action="store_true",
                    help="只报高信号模式（密钥/令牌/私钥），误报更少")
    ap.add_argument("--quiet", action="store_true", help="只输出结论，不列命中明细")
    args = ap.parse_args()

    patterns = STRICT_PATTERNS if args.strict else STRICT_PATTERNS + LOOSE_PATTERNS

    total = 0
    for arg in args.paths:
        target = Path(arg)
        if not target.exists():
            print(f"[跳过] 不存在: {arg}")
            continue
        for f in iter_files(target):
            hits = scan_file(f, patterns)
            if hits:
                total += len(hits)
                if not args.quiet:
                    print(f"\n[命中] {f}")
                    for lineno, name, snippet in hits:
                        print(f"  L{lineno} [{name}] {snippet}")

    if total:
        print(f"\n[FAIL] 共 {total} 处命中，清理后再对外交付")
        return 1
    print("[OK] 扫描通过，未发现密钥/隐私泄露")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
