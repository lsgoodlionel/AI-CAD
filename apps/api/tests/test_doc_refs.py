"""文档里的文件路径引用不得新增失效项。

起因：`CLAUDE.md` 把两个早在 `f58159d` 就删掉的前端组件列为 Phase C 的关键文件，
两个月没人发现 —— 因为没有任何机制会去核对文档里的路径。
照着文档找文件扑空是最浪费时间的错，而它完全可以被机器发现。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import check_doc_refs as cdr  # noqa: E402

#: 隔离测试环境（容器 /tmp）只注入 `apps/api`，没有 CLAUDE.md 与 docs/，
#: 本文件的检查对象整体缺席 —— 那里**跳过**，而不是判失败。
#: CI 是完整 checkout，会真跑；跳过在 pytest 输出里可见，不是静默降级。
needs_full_repo = pytest.mark.skipif(
    not (cdr.REPO / "CLAUDE.md").is_file(),
    reason=f"需要完整仓库检出（{cdr.REPO} 下没有 CLAUDE.md）——隔离测试环境只注入 apps/api",
)


@pytest.mark.unit
@needs_full_repo
def test_no_new_broken_doc_refs():
    """活文档里不得出现 baseline 之外的失效路径引用。

    baseline（`scripts/doc_refs_baseline.txt`）记的是存量，其中大部分是
    **正当写法** —— 说明「某文件已删」时必然要提到那个已不存在的文件名。
    语法上分不开这两者，所以只判「新增」。

    改文档后若这条红了：要么路径写错了，要么是新的「已删除」说明 ——
    后者确认无误后跑 `python scripts/check_doc_refs.py --update` 收进 baseline。
    """
    current = cdr.flatten(cdr.scan())
    known = set()
    if cdr.BASELINE.exists():
        known = {
            line
            for line in cdr.BASELINE.read_text(encoding="utf-8").split("\n")
            if line.strip()
        }
    new = current - known
    assert not new, "文档里出现了新的失效路径引用：\n" + "\n".join(
        f"  {item.replace(chr(9), ': ')}" for item in sorted(new)
    )


@pytest.mark.unit
@needs_full_repo
def test_scanner_actually_finds_things():
    """扫描器自身不能静默失效。

    第一版把仓库根算成了 `parents[2]`（只到 `apps/`），于是一个文档都没扫到、
    报告「0 处失效」——**一个查不出问题的检查器比没有检查器更糟**，
    因为它会让人以为已经检查过了。这条用真实的存量数兜住那种情况。
    """
    assert (cdr.REPO / "CLAUDE.md").is_file(), "仓库根定位错误"
    found = cdr.scan()
    assert found, "扫描器一处引用都没查到 —— 它自己坏了"
    # 存量在 40 上下；这里只要求「确实扫到了东西」，不锁死具体数字
    assert len(cdr.flatten(found)) >= 10


@pytest.mark.unit
@needs_full_repo
def test_history_docs_are_excluded():
    """逐次记录型文档不参与判定 —— 它们记的是当时的状态。

    把「那天我跑了 probe_seat_scan.py」改成今天的路径，是伪造记录。
    """
    assert "docs/PROGRESS.md" in cdr.HISTORY_DOCS
    assert "docs/PROGRESS.md" not in cdr.scan()
    assert "docs/PROGRESS.md" in cdr.scan(include_history=True)
