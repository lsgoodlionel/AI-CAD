"""仓库路径定位:按标记上溯,而不是数固定层数。

**为什么需要**:e2e 测试原本用 `Path(__file__).parents[2].parent.parent` 找仓库根。
宿主上正确(`apps/api` → `apps` → 仓库根),但**容器把 `apps/api` 挂成 `/app`**,
深度少了两级,算出的根是 `/`,于是去找 `/docs` 必然失败。

后果不是「测试挂了」这么简单——它是个**假失败**:在容器里跑全量回归会稳定出现
3 个红,让人以为是既有基线问题而反复忽略(实际已误报三次)。

所以:
1. 按目录标记上溯定位仓库根,宿主与容器都对;
2. 找不到 docs(容器里确实没挂)时**明确 skip 并说明原因**,不伪装成失败。
"""
from __future__ import annotations

from pathlib import Path

#: 判定「这就是仓库根」的标记目录。两者同时存在才算,避免误命中子目录
_ROOT_MARKERS = ("docs", "apps")


def find_repo_root(start: Path | None = None) -> Path | None:
    """从 start 向上找同时含 docs/ 与 apps/ 的目录;找不到返回 None。"""
    here = (start or Path(__file__)).resolve()
    for candidate in [here, *here.parents]:
        if all((candidate / marker).is_dir() for marker in _ROOT_MARKERS):
            return candidate
    return None


def docs_dir() -> Path | None:
    """仓库 docs/ 目录;不可达返回 None。"""
    root = find_repo_root()
    return (root / "docs") if root else None


def require_docs():
    """需要 docs/ 的测试入口:不可达则 skip 并说明原因(不是 fail)。"""
    import pytest

    docs = docs_dir()
    if docs is None:
        pytest.skip("仓库 docs/ 不可达(容器只挂载了 apps/api),"
                    "文档类断言在宿主或 CI 上执行")
    return docs
