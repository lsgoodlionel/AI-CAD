"""文档里的文件路径引用是否还指向真实存在的文件。

**为什么需要它.** 2026-09-09 清理仓库时偶然发现 `CLAUDE.md` 把
`DrawingAnnotationQueue.tsx` / `SemanticReviewQueue.tsx` 列为 Phase C 的关键文件，
而这两个文件早在两个月前的 `f58159d`「模型页三模式重构」就删了。
照着文档找文件扑空，是最浪费时间的一种错——而它完全可以被机器发现。

全量扫描后实测：62 份文档、978 处路径引用，**51 处指向不存在的文件**。

**三类失效，处理方式不同**（这是本脚本的主要判断）：

1. **活文档里的错误引用** —— 真问题，要改。
2. **历史日志里的引用**（`PROGRESS.md` 等）—— **不改**。它们记录的是当时的状态，
   把「那天我跑了 `probe_seat_scan.py`」改成今天的路径，反而是伪造记录。
3. **说明「某文件已删」的文字** —— 形式上是失效引用，实质是正确的说明。
   例如「原 `layer_class_map.yaml` 已并入并删除」。

第 3 类无法用语法分开，所以本脚本用 **baseline** 而不是绝对数：
存量记在 `doc_refs_baseline.txt` 里，只有**新增**的失效引用才算失败。
这样加文档时写错路径会被立刻发现，而不必先把存量清零。

用法::

    python scripts/check_doc_refs.py              # 报告 + 与 baseline 比对
    python scripts/check_doc_refs.py --update     # 刷新 baseline（改文档后）
    python scripts/check_doc_refs.py --all        # 连历史日志一起列（只看不判）
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

#: 仓库根：本文件在 apps/api/scripts/ 下，往上三层。
#: （写 parents[2] 只到 apps/，扫描会静默得出「0 处失效」—— 一个查不出问题的
#:  检查器比没有检查器更糟，所以下面用 CLAUDE.md 是否存在来自证。）
REPO = Path(__file__).resolve().parents[3]
BASELINE = Path(__file__).parent / "doc_refs_baseline.txt"

#: 逐次记录型文档 —— 记的是当时的状态，其中的路径**不该**跟着仓库变。
HISTORY_DOCS = {
    "docs/PROGRESS.md",
    "docs/METRO_DRAWINGS_PROFILE.md",
    "docs/DEV_REVIEW_2026-07.md",
}

#: 反引号里长得像文件路径的 token。只认这些扩展名，避免把普通词当路径。
REF = re.compile(r"`([A-Za-z0-9_./\-]+\.(?:py|ts|tsx|yaml|yml|sql|json|sh|mjs))`")

#: 文档常写相对路径，按这些前缀补全后再判存在。
PREFIXES = (
    "", "apps/api", "apps/web/src", "apps/api/core", "apps/api/services",
    "apps/api/routers", "apps/api/tasks", "apps/api/tests", "apps/api/data",
    "apps/api/scripts", "apps/api/migrations", "docs", "apps/web", "infra",
)

#: 明显不是真实路径的写法，直接跳过。
PLACEHOLDER = re.compile(r"0NN|xxx|\{|\*|<|>")


def _assert_repo_root() -> None:
    """路径算错会让扫描静默返回 0 处 —— 宁可响亮地失败。"""
    if not (REPO / "CLAUDE.md").is_file():
        raise SystemExit(f"仓库根定位错误：{REPO} 下没有 CLAUDE.md")


#: 遍历时跳过的目录 —— 体量大且不含文档会引用的源文件。
SKIP_DIRS = {".git", "node_modules", "dist", "build", "__pycache__",
             ".venv", "venv", ".pytest_cache", ".mypy_cache", "coverage"}


def tracked_files() -> set[str]:
    """仓库里的文件清单（仓库根的相对路径）。

    **不依赖 git**：判断的是「这个路径今天还指向一个文件吗」，直接看文件系统
    最贴题；而且部署镜像里未必装 git（实测 cad_api 镜像就没有，
    第一版用 `git ls-files` 在那里直接 FileNotFoundError）。
    """
    _assert_repo_root()
    # 用 os.walk 而不是 rglob：walk 能就地剪掉整棵子树，rglob 会**先递归进去**
    # 再逐个判断 —— 实测那样跑 node_modules 会让本函数超过两分钟。
    files: set[str] = set()
    for root, dirnames, filenames in os.walk(REPO):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel_root = Path(root).relative_to(REPO)
        for name in filenames:
            files.add((rel_root / name).as_posix())
    return files


def exists(ref: str, tracked: set[str]) -> bool:
    if PLACEHOLDER.search(ref):
        return True
    if any(f"{p}/{ref}".lstrip("/") in tracked for p in PREFIXES):
        return True
    # 再宽松一档：文件名在仓库任意位置存在即算数（文档常只写文件名）
    base = ref.rsplit("/", 1)[-1]
    return any(t.endswith("/" + base) or t == base for t in tracked)


def scan(include_history: bool = False) -> dict[str, list[str]]:
    tracked = tracked_files()
    docs = [REPO / "CLAUDE.md"] + sorted((REPO / "docs").glob("*.md"))
    found: dict[str, list[str]] = {}
    for doc in docs:
        rel = doc.relative_to(REPO).as_posix()
        if rel in HISTORY_DOCS and not include_history:
            continue
        try:
            text = doc.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        bad = sorted({r for r in REF.findall(text) if not exists(r, tracked)})
        if bad:
            found[rel] = bad
    return found


def flatten(found: dict[str, list[str]]) -> set[str]:
    return {f"{doc}\t{ref}" for doc, refs in found.items() for ref in refs}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true", help="把当前结果写成新的 baseline")
    ap.add_argument("--all", action="store_true", help="连历史日志一起列出（只报告不判定）")
    args = ap.parse_args()

    found = scan(include_history=args.all)
    current = flatten(found)

    if args.update:
        BASELINE.write_text("\n".join(sorted(current)) + "\n", encoding="utf-8")
        print(f"baseline 已更新：{len(current)} 处")
        return 0

    for doc, refs in sorted(found.items(), key=lambda kv: -len(kv[1])):
        print(f"{doc}  ({len(refs)} 处)")
        for ref in refs:
            print(f"    {ref}")

    if args.all:
        print(f"\n合计 {len(current)} 处（含历史日志，仅供查看）")
        return 0

    known = set()
    if BASELINE.exists():
        known = {ln for ln in BASELINE.read_text(encoding="utf-8").split("\n") if ln.strip()}

    new = current - known
    gone = known - current
    print(f"\n活文档失效引用 {len(current)} 处（baseline {len(known)} 处）")
    if gone:
        print(f"  已修复 {len(gone)} 处 —— 记得跑 --update 收紧 baseline")
    if new:
        print(f"\n新增 {len(new)} 处失效引用：")
        for item in sorted(new):
            doc, ref = item.split("\t")
            print(f"    {doc}: {ref}")
        print("\n改掉它们，或确认是「说明某文件已删」的正当写法后跑 --update。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
