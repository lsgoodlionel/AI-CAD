"""仓库路径定位单测。

原来的 e2e 测试按**固定层数**上溯找仓库根,宿主对、容器错(`apps/api` 被挂成
`/app`,少两级),稳定产生 3 个**假失败**——已误报三次当成「既有基线」。
"""
from pathlib import Path

from tests.repo_paths import docs_dir, find_repo_root


def test_finds_root_from_a_nested_synthetic_tree(tmp_path):
    """标记齐全时,从任意深度都能找到根。"""
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "apps" / "api" / "tests" / "e2e").mkdir(parents=True)
    deep = root / "apps" / "api" / "tests" / "e2e"
    assert find_repo_root(deep) == root.resolve()


def test_requires_both_markers(tmp_path):
    """只有 docs 没有 apps 不算根 —— 避免误命中同名子目录。"""
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "apps" / "api").mkdir(parents=True)
    fake = root / "apps" / "api" / "docs"
    fake.mkdir()
    assert find_repo_root(root / "apps" / "api") == root.resolve()


def test_returns_none_when_markers_absent(tmp_path):
    """容器里 docs/ 确实不存在 —— 返回 None 让调用方 skip,而不是伪装失败。"""
    lonely = tmp_path / "app" / "tests"
    lonely.mkdir(parents=True)
    assert find_repo_root(lonely) is None


def test_depth_independence_is_the_point(tmp_path):
    """同一份代码在两种挂载深度下都要工作 —— 这正是固定层数做不到的。"""
    for depth in (1, 3, 5):
        root = tmp_path / f"r{depth}"
        (root / "docs").mkdir(parents=True)
        nested = root / "apps"
        for i in range(depth):
            nested = nested / f"d{i}"
        nested.mkdir(parents=True)
        assert find_repo_root(nested) == root.resolve()


def test_docs_dir_is_none_or_a_real_directory():
    """在宿主上应指向真实 docs/;在容器里应为 None。两者都不许抛异常。"""
    got = docs_dir()
    assert got is None or got.is_dir()
