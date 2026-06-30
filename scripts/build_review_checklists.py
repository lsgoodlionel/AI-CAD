#!/usr/bin/env python3
"""把 ``05_专业审图清单SOP.md`` 蒸馏为引擎可消费的 YAML 清单资产。

来源（认知蒸馏，19 专业 × 1909 条会审记录）：
    ~/work/031 图纸会审/03 整理数据库/06_认知蒸馏/05_专业审图清单SOP.md

产出：
    apps/api/data/review_protocol/review_checklists.yaml

SOP 结构高度统一，每专业含 13 个 `### ` 小节，本脚本只提取其中 4 个：
    审图目标          → protected_result（取首要目标句）
    未来实施后果链     → consequence_chain（有序后果链）
    逐项清单           → checklist（检查项/判断依据/核查方法/常见冲突/必问问题/输出口径）
    行动与闭环规则     → closure_rules

规范更新后可重跑本脚本重新蒸馏。用法：
    python scripts/build_review_checklists.py [--source <md>] [--out <yaml>]
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import yaml

# ── 默认路径 ─────────────────────────────────────────────
_DEFAULT_SOURCE = (
    Path.home()
    / "work"
    / "031 图纸会审"
    / "03 整理数据库"
    / "06_认知蒸馏"
    / "05_专业审图清单SOP.md"
)
_DEFAULT_OUT = (
    Path(__file__).resolve().parents[1]
    / "apps"
    / "api"
    / "data"
    / "review_protocol"
    / "review_checklists.yaml"
)

# 标记"可升级/高价值"清单项的检查项关键词（命中即 升级=True）
_UPGRADE_HINTS = ("施工可落地", "接口", "联合", "风险分级", "升级")

# 逐项清单子字段顺序与 key 映射
_ITEM_FIELDS = {
    "判断依据": "判断依据",
    "核查方法": "核查方法",
    "常见冲突": "常见冲突",
    "必问问题": "必问问题",
    "输出口径": "输出口径",
}

_DISC_HEADER = re.compile(r"^##\s+([A-Z]+)\s*/\s*(\S+)")
_SUB_HEADER = re.compile(r"^###\s+(\S+)")
_NUM_ITEM = re.compile(r"^\d+\.\s*(.*)$")
_CHECK_TITLE = re.compile(r"^检查项[:：]\s*(.*)$")
_FIELD_LINE = re.compile(r"^([^:：]+)[:：]\s*(.*)$")


def _strip_num(line: str) -> str:
    m = _NUM_ITEM.match(line.strip())
    return m.group(1).strip() if m else line.strip()


def _split_sections(lines: list[str]) -> dict[str, list[str]]:
    """把一个专业块按 `### 小节` 切分为 {小节名: [正文行]}。"""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in lines:
        sub = _SUB_HEADER.match(raw)
        if sub:
            current = sub.group(1)
            sections[current] = []
            continue
        if current is not None:
            sections[current].append(raw)
    return sections


def _parse_protected_result(body: list[str]) -> str:
    """审图目标 → 取『首要目标』句作为 protected_result。"""
    for raw in body:
        text = _strip_num(raw)
        if not text:
            continue
        if text.startswith("首要目标"):
            return re.sub(r"^首要目标[:：]\s*", "", text).strip()
    # 兜底：取第一条非空有序项
    for raw in body:
        text = _strip_num(raw)
        if text:
            return text
    return ""


def _parse_ordered(body: list[str], limit: int | None = None) -> list[str]:
    """提取有序列表正文（去掉序号），可选截断条数。"""
    out: list[str] = []
    for raw in body:
        if not _NUM_ITEM.match(raw.strip()):
            continue
        text = _strip_num(raw)
        if text:
            out.append(text)
        if limit is not None and len(out) >= limit:
            break
    return out


def _parse_checklist(body: list[str]) -> list[dict]:
    """逐项清单 → [{检查项,判断依据,核查方法,常见冲突,必问问题,输出口径,升级}]。"""
    items: list[dict] = []
    current: dict | None = None
    for raw in body:
        stripped = raw.strip()
        if not stripped:
            continue
        num = _NUM_ITEM.match(stripped)
        if num:
            title_m = _CHECK_TITLE.match(num.group(1).strip())
            if title_m:
                if current:
                    items.append(current)
                title = title_m.group(1).strip()
                current = {
                    "检查项": title,
                    "判断依据": "",
                    "核查方法": "",
                    "常见冲突": "",
                    "必问问题": "",
                    "输出口径": "",
                    "升级": any(h in title for h in _UPGRADE_HINTS),
                }
                continue
        if current is None:
            continue
        field_m = _FIELD_LINE.match(stripped)
        if field_m:
            key = field_m.group(1).strip()
            if key in _ITEM_FIELDS:
                current[_ITEM_FIELDS[key]] = field_m.group(2).strip()
    if current:
        items.append(current)
    return items


def _split_disciplines(text: str) -> list[tuple[str, str, list[str]]]:
    """把整个 SOP 切成 [(code, name, [行]), ...]。"""
    blocks: list[tuple[str, str, list[str]]] = []
    code = name = None
    buf: list[str] = []
    for raw in text.splitlines():
        header = _DISC_HEADER.match(raw)
        if header:
            if code is not None:
                blocks.append((code, name, buf))
            code, name = header.group(1), header.group(2)
            buf = []
            continue
        if code is not None:
            buf.append(raw)
    if code is not None:
        blocks.append((code, name, buf))
    return blocks


def build(source: Path) -> dict:
    text = source.read_text(encoding="utf-8")
    checklists: dict[str, dict] = {}
    for code, name, lines in _split_disciplines(text):
        sections = _split_sections(lines)
        checklists[code] = {
            "name": name,
            "protected_result": _parse_protected_result(sections.get("审图目标", [])),
            "consequence_chain": _parse_ordered(sections.get("未来实施后果链", [])),
            "checklist": _parse_checklist(sections.get("逐项清单", [])),
            "closure_rules": _parse_ordered(sections.get("行动与闭环规则", [])),
        }
    return {"checklists": checklists}


def main() -> None:
    parser = argparse.ArgumentParser(description="蒸馏 05 SOP → review_checklists.yaml")
    parser.add_argument("--source", type=Path, default=_DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    args = parser.parse_args()

    if not args.source.exists():
        raise SystemExit(f"源文件不存在：{args.source}")

    data = build(args.source)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# 自动生成：scripts/build_review_checklists.py\n"
        "# 来源：06_认知蒸馏/05_专业审图清单SOP.md（19 专业 × 1909 条会审记录蒸馏）\n"
        "# 请勿手改，规范更新后重跑脚本重新蒸馏。\n"
    )
    args.out.write_text(
        header
        + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=1000),
        encoding="utf-8",
    )

    disc = data["checklists"]
    total_items = sum(len(d["checklist"]) for d in disc.values())
    print(f"已写出 {args.out}")
    print(f"专业数={len(disc)}  清单项总数={total_items}")
    for code, d in disc.items():
        print(
            f"  {code}/{d['name']}: 清单 {len(d['checklist'])} 项, "
            f"后果链 {len(d['consequence_chain'])} 条, "
            f"闭环 {len(d['closure_rules'])} 条"
        )


if __name__ == "__main__":
    main()
