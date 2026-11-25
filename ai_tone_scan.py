#!/usr/bin/env python3
"""ai_tone_scan.py — 交付前扫 AI 腔痕迹（可正则化的那部分）

主观项（收尾金句、铺垫式开场、意义拔高、误报判定）仍靠 humanizer-zh 逐条过。
本脚本只管机器能判的硬痕迹，用来防止"上下文一紧就跳过审稿"。

用法：
    python ai_tone_scan.py <文件或目录> [--strict] [--json] [--max-neg 3]

检测：
    ERROR  连续否定排比：同一段落出现 >=3 个否定分句（不是 A，不是 B，也不是 C）
    ERROR  中文破折号 ——
    WARN   单组对举「不是 X，而是 Y」
    WARN   碎片否定句：短句以「没有」开头并独立成句
    WARN   粗体标签列表「- **标签：** 内容」
    WARN   AI 高频词（此外、值得注意的是、赋能…）
    WARN   汇报腔收尾（综上、由此可见、总而言之）
    WARN   单个破折号 —（非数字区间）

退出码：0 干净；1 有 error；加 --strict 时有 warn 也算 1。

注意：本脚本不要扫规则文件和 skill 正文，那里面的模式是「在讨论」而不是「在使用」，
必然命中。只扫交付物。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCAN_EXTS = {".md", ".markdown", ".txt", ".html", ".htm", ""}

# 分句级否定前缀。「不是不想，是不能」这种两条的属于正常中文，靠阈值放过。
NEG_PREFIXES = (
    "不是", "也不是", "更不是", "并非", "并不",
    "不只是", "不仅仅是", "不仅是", "不再是",
    "不是为了", "不是要",
)
# 碎片否定句：短句以「没有」起头，独立成句（§2 的形态）
FRAG_NEG_PREFIX = ("没有", "不含")

AI_WORDS = (
    "此外", "值得注意的是", "至关重要", "深入探讨", "赋能", "彰显",
    "见证", "不容忽视", "综上所述", "由此可见", "总的来说", "总而言之",
)

# 只抓带「而是」「更是」的书面式对举。真人说话常说「不是不想，是不能」，
# 那属于正常中文，报了就是噪音。
CONTRAST_RE = re.compile(
    r"(?:不是|不仅|不只是|不仅是)[^。！？!?\n]{0,40}(?:而是|更是)"
)
BOLD_LABEL_RE = re.compile(r"^\s*[-*+]\s*\*\*[^*]{1,20}\*\*")
DOUBLE_DASH_RE = re.compile(r"——")
SINGLE_DASH_RE = re.compile(r"(?<!\d)—(?!\d)")
FENCE_RE = re.compile(r"^\s*```")


def strip_code_blocks(lines: list[str]) -> list[tuple[int, str]]:
    """返回 (行号, 文本)，跳过 fenced code block 与行内代码。"""
    out: list[tuple[int, str]] = []
    in_fence = False
    for i, line in enumerate(lines, 1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        out.append((i, re.sub(r"`[^`]*`", "", line)))
    return out


def split_paragraphs(numbered: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    """按空行切段落，保留每行的原始行号。"""
    paras: list[list[tuple[int, str]]] = []
    cur: list[tuple[int, str]] = []
    for line_no, text in numbered:
        if text.strip() == "":
            if cur:
                paras.append(cur)
                cur = []
        else:
            cur.append((line_no, text))
    if cur:
        paras.append(cur)
    return paras


def is_neg_clause(clause: str) -> bool:
    c = clause.strip()
    return any(c.startswith(p) for p in NEG_PREFIXES)


def is_frag_neg(sentence: str) -> bool:
    s = sentence.strip()
    if len(s) > 16:
        return False
    return any(s.startswith(p) for p in FRAG_NEG_PREFIX)


def find_neg_runs(para_text: str, max_neg: int) -> list[list[str]]:
    """找出段落内连续 max_neg 个及以上的否定分句。"""
    runs: list[list[str]] = []
    run: list[str] = []

    def flush() -> None:
        nonlocal run
        if len(run) >= max_neg:
            runs.append(run)
        run = []

    for sentence in re.split(r"[。！？!?]", para_text):
        if not sentence.strip():
            continue
        if is_frag_neg(sentence):
            run.append(sentence.strip())
            continue
        for clause in re.split(r"[，,、；;]", sentence):
            if not clause.strip():
                continue
            if is_neg_clause(clause):
                run.append(clause.strip())
            else:
                flush()
        flush()
    return runs


def scan_text(text: str, max_neg: int) -> list[dict]:
    findings: list[dict] = []
    lines = text.splitlines()
    numbered = strip_code_blocks(lines)

    for para in split_paragraphs(numbered):
        line_no = para[0][0]
        para_text = "\n".join(t for _, t in para)

        runs = find_neg_runs(para_text, max_neg)
        for run in runs:
            findings.append({
                "level": "error",
                "kind": "连续否定排比",
                "line": line_no,
                "text": "，".join(run),
            })
        if not runs:
            m = CONTRAST_RE.search(para_text)
            if m:
                findings.append({
                    "level": "warn",
                    "kind": "单组对举",
                    "line": line_no,
                    "text": m.group(0),
                })

        if DOUBLE_DASH_RE.search(para_text):
            findings.append({
                "level": "error", "kind": "中文破折号", "line": line_no,
                "text": "——",
            })
        elif SINGLE_DASH_RE.search(para_text):
            findings.append({
                "level": "warn", "kind": "单个破折号", "line": line_no,
                "text": "—",
            })

        hit_words = [w for w in AI_WORDS if w in para_text]
        if hit_words:
            findings.append({
                "level": "warn", "kind": "AI 高频词", "line": line_no,
                "text": "、".join(hit_words),
            })

    for line_no, line in numbered:
        if BOLD_LABEL_RE.match(line):
            findings.append({
                "level": "warn", "kind": "粗体标签列表", "line": line_no,
                "text": line.strip()[:40],
            })

    return findings


def iter_targets(path: Path) -> list[Path]:
    if path.is_dir():
        return sorted(
            p for p in path.rglob("*")
            if p.is_file() and p.suffix.lower() in SCAN_EXTS
        )
    return [path]


def main() -> int:
    ap = argparse.ArgumentParser(description="交付前扫 AI 腔痕迹")
    ap.add_argument("path", help="待扫文件或目录")
    ap.add_argument("--strict", action="store_true", help="warn 也算失败")
    ap.add_argument("--json", dest="as_json", action="store_true", help="输出 JSON")
    ap.add_argument("--max-neg", type=int, default=3, help="连续否定命中阈值，默认 3")
    args = ap.parse_args()

    target = Path(args.path)
    if not target.exists():
        print(f"路径不存在：{target}", file=sys.stderr)
        return 2

    all_findings: list[dict] = []
    for file in iter_targets(target):
        try:
            text = file.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            text = file.read_text(encoding="utf-8", errors="replace")
        for f in scan_text(text, args.max_neg):
            f["file"] = str(file)
            all_findings.append(f)

    errors = [f for f in all_findings if f["level"] == "error"]
    warns = [f for f in all_findings if f["level"] == "warn"]

    if args.as_json:
        print(json.dumps(
            {"findings": all_findings,
             "summary": {"error": len(errors), "warn": len(warns)}},
            ensure_ascii=False, indent=2,
        ))
    else:
        for f in sorted(all_findings, key=lambda x: (x["level"] != "error", x["line"])):
            tag = "ERROR" if f["level"] == "error" else "WARN "
            print(f"[{tag}] {f['kind']:<8} {f['file']}:{f['line']}  {f['text']}")
        if all_findings:
            print(f"\n共 {len(all_findings)} 处（error {len(errors)} / warn {len(warns)}）")
        else:
            print("干净，没扫到硬痕迹。主观项仍需 humanizer-zh 逐条过。")

    if errors or (args.strict and warns):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
