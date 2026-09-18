"""`plausibility/formulas.py` 的**取证纪律**测试。

这里测的不是公式算得对不对（那是 `geometry.py` / `rules_*.py` 的事），
而是**每条公式的出处是否可回溯**：

- 文献来源必须是「已登记的书 key + 能在缓存 `book.md` 里定位到的页/节」，
  并且带原文抽取样；
- 推导来源必须指名它从本表的哪几条推出；
- 查不到就必须写明查过哪几本、用了什么关键词 —— 否则下一个人会重查一遍。

书名**一律从 `core.knowledge.math_sources.SOURCES` 读**，不在本文件里硬编码：
清单增删一本，这里应当跟着变，而不是各写一遍等着漂移。

本文件**刻意不 import `rules_*`** —— 规则模块由别人写，
取证纪律不该因为规则模块正在改而红。
"""
from __future__ import annotations

import pathlib
import re

import pytest

from core.knowledge.math_sources import CACHE_DIRNAME, SOURCES
from core.model3d.plausibility.formulas import (
    DERIVED,
    FORMULAS,
    UNCITED,
    cite,
    formula,
    uncited_keys,
)

pytestmark = pytest.mark.unit

#: key → 登记信息。`extract_method == "epub"` 的书没有固定页，按 spine 的「节」引用。
_SOURCE_BY_KEY = {s.key: s for s in SOURCES}

#: `<key> p.283`、`<key> p.283-284`、`<key> 节19`
_LOCATOR = re.compile(r"^(?P<key>[a-z0-9-]+) (?:p\.(?P<page>\d+)(?:-\d+)?|节(?P<sec>\d+))$")

#: `UNCITED:` 后面至少要有这么多字，才算「写明了查过什么」。
#: 20 字大约是「六本检索 'xxx' 无命中」这种最短的有效说明。
_MIN_SEARCH_NOTE = 20


def _literature_items():
    return [f for f in FORMULAS.values()
            if f.source != DERIVED and not f.source.startswith(UNCITED)]


# ── 1. 出处格式 ────────────────────────────────────────────────────
@pytest.mark.parametrize("key", sorted(FORMULAS))
def test_source_is_one_of_three_legal_forms(key: str) -> None:
    # Arrange
    source = FORMULAS[key].source

    # Act
    is_derived = source == DERIVED
    is_uncited = source.startswith(f"{UNCITED}:")
    is_literature = _LOCATOR.match(source) is not None

    # Assert
    assert is_derived or is_uncited or is_literature, (
        f"{key} 的 source={source!r} 不是三种合法形式之一"
        "（'<书 key> p.<页>' / '<书 key> 节<节>' / DERIVED / 'UNCITED:…'）")


def test_literature_source_names_a_registered_book() -> None:
    # Arrange
    items = _literature_items()

    # Act
    unknown = [(f.key, _LOCATOR.match(f.source).group("key"))  # type: ignore[union-attr]
               for f in items
               if _LOCATOR.match(f.source).group("key") not in _SOURCE_BY_KEY]  # type: ignore[union-attr]

    # Assert
    assert unknown == [], f"这些出处引了未登记的书：{unknown}；已登记：{sorted(_SOURCE_BY_KEY)}"


def test_cited_page_is_within_the_registered_page_count() -> None:
    """引到第 900 页而书只有 584 页，说明页码不是从缓存里抄的。"""
    # Arrange
    overflow: list[tuple[str, str, int]] = []

    # Act
    for item in _literature_items():
        m = _LOCATOR.match(item.source)
        assert m is not None
        book = _SOURCE_BY_KEY[m.group("key")]
        located = m.group("page") or m.group("sec")
        if int(located) > book.pages:
            overflow.append((item.key, item.source, book.pages))

    # Assert
    assert overflow == [], f"页码/节号超出该书登记的篇幅：{overflow}"


def test_epub_source_is_cited_by_section_and_pdf_source_by_page() -> None:
    """EPUB 重排后页码引不住，必须按节引；PDF 反之。"""
    # Arrange
    wrong: list[tuple[str, str, str]] = []

    # Act
    for item in _literature_items():
        m = _LOCATOR.match(item.source)
        assert m is not None
        book = _SOURCE_BY_KEY[m.group("key")]
        by_section = m.group("sec") is not None
        if (book.extract_method == "epub") is not by_section:
            wrong.append((item.key, item.source, book.extract_method))

    # Assert
    assert wrong == [], f"引用粒度与该书的抽取方式不符（epub 应按节、其余按页）：{wrong}"


#: 派生全文缓存（`.gitignore`，CI 里通常没有）。有它就做最硬的那道核对：
#: **quote 的第一行必须真的出现在所引的那一页上**。
_CACHE = pathlib.Path(__file__).resolve().parents[1] / "data" / "knowledge" / CACHE_DIRNAME


def _page_text(book_key: str, locator: str) -> str | None:
    """取缓存 `book.md` 里 `## p.N` / `## 节N` 那一段的正文；没有缓存返回 None。"""
    path = _CACHE / book_key / "book.md"
    if not path.is_file():
        return None
    anchor = re.compile(r"^## (p\.\d+|节\d+)\s*$")
    keep, chunk = False, []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = anchor.match(line)
        if m:
            keep = m.group(1) in {f"p.{locator}", f"节{locator}"}
            continue
        if keep:
            chunk.append(line)
    return "\n".join(chunk)


def _flat(text: str) -> str:
    """折叠所有空白 —— 原文在书里是按版面断行的，断在哪儿不该影响核对。"""
    return " ".join(text.split())


def _quote_lines(quote: str) -> list[str]:
    """逐行拆 quote。以 `—` 开头的是「同页另一处」的附注，取其冒号后的正文。"""
    out = []
    for raw in quote.splitlines():
        line = _flat(raw)
        if not line:
            continue
        out.append(_flat(line.lstrip("— ").split("：", 1)[-1]) if line.startswith("—")
                   else line)
    return out


def test_every_quoted_line_really_appears_on_the_cited_page() -> None:
    """最硬的一道：出处不是写上去的，是能在缓存里翻到的。

    逐行核对 —— 只核第一行的话，后面几行抄错了也照样绿。
    缓存（`data/knowledge/math_physics/`）是版权派生物，不进版本库，
    CI 里通常没有 —— 那就跳过，而不是假装通过。
    """
    # Arrange
    if not _CACHE.is_dir():
        pytest.skip(f"没有派生全文缓存 {_CACHE}，跳过原文核对")
    missing: list[tuple[str, str, str]] = []

    # Act
    for item in _literature_items():
        m = _LOCATOR.match(item.source)
        assert m is not None
        page = _page_text(m.group("key"), m.group("page") or m.group("sec"))
        if page is None:
            continue
        flat_page = _flat(page)
        missing += [(item.key, item.source, line[:60])
                    for line in _quote_lines(item.quote) if line not in flat_page]

    # Assert
    assert missing == [], f"这些条目的原文在所引页上找不到（页码抄错或原文不是逐字抄的）：{missing}"


# ── 2. 文献来源要有原文；推导来源要指明父条目 ──────────────────────
def test_every_literature_source_carries_a_quote() -> None:
    # Arrange / Act
    missing = [f.key for f in _literature_items() if not f.quote.strip()]

    # Assert
    assert missing == [], f"这些条目写了书和页码却没有原文抽取样：{missing}"


def test_derived_items_name_existing_parent_formulas() -> None:
    # Arrange
    derived = [f for f in FORMULAS.values() if f.source == DERIVED]

    # Act
    empty = [f.key for f in derived if not f.derived_from]
    dangling = [(f.key, parent) for f in derived
                for parent in f.derived_from if parent not in FORMULAS]

    # Assert
    assert derived, "表里一条 DERIVED 都没有，说明这条纪律没被测到"
    assert empty == [], f"标了 DERIVED 却没写从哪推出：{empty}"
    assert dangling == [], f"derived_from 指向了表里不存在的 key：{dangling}"


def test_derived_items_do_not_depend_on_themselves() -> None:
    # Arrange / Act
    self_ref = [f.key for f in FORMULAS.values() if f.key in f.derived_from]

    # Assert
    assert self_ref == [], f"derived_from 指向了自己：{self_ref}"


def test_derived_chain_bottoms_out_at_literature() -> None:
    """推导链最终必须落到一条有书有页的条目上，否则等于没出处。"""
    # Arrange
    def roots(key: str, seen: frozenset[str]) -> list[str]:
        item = FORMULAS[key]
        if item.source != DERIVED:
            return [item.source]
        return [r for parent in item.derived_from if parent not in seen
                for r in roots(parent, seen | {key})]

    # Act
    unrooted = [
        key for key, item in FORMULAS.items()
        if item.source == DERIVED
        and not any(_LOCATOR.match(r) for r in roots(key, frozenset()))
    ]

    # Assert
    assert unrooted == [], f"这些 DERIVED 条目的推导链没有落到文献上：{unrooted}"


# ── 3. 查不到的要写明查过什么 ──────────────────────────────────────
def test_uncited_items_record_what_was_searched() -> None:
    # Arrange
    uncited = [f for f in FORMULAS.values() if f.source.startswith(f"{UNCITED}:")]

    # Act
    too_short = [(f.key, f.source) for f in uncited
                 if len(f.source[len(UNCITED) + 1:].strip()) < _MIN_SEARCH_NOTE]

    # Assert
    assert uncited, "表里一条 UNCITED 都没有 —— 那这条纪律没被测到"
    assert too_short == [], (
        f"标了 UNCITED 却没写清查过哪几本、什么关键词（少于 {_MIN_SEARCH_NOTE} 字）：{too_short}")


def test_every_formula_states_its_conditions() -> None:
    """成立条件是最容易被忽略、也最容易出事的一栏。"""
    # Arrange / Act
    blank = [k for k, f in FORMULAS.items() if len(f.conditions.strip()) < 4]

    # Assert
    assert blank == [], f"这些条目没写成立条件：{blank}"


def test_key_matches_the_dict_key() -> None:
    # Arrange / Act
    mismatched = [(k, f.key) for k, f in FORMULAS.items() if k != f.key]

    # Assert
    assert mismatched == [], f"dict 的 key 与 Formula.key 不一致：{mismatched}"


# ── 4. 接口行为 ────────────────────────────────────────────────────
def test_formula_raises_key_error_for_unregistered_key() -> None:
    # Arrange
    unknown = "geometry.no_such_theorem"

    # Act / Assert
    with pytest.raises(KeyError):
        formula(unknown)


@pytest.mark.parametrize("key", sorted(FORMULAS))
def test_cite_includes_the_source_string(key: str) -> None:
    # Arrange
    item = FORMULAS[key]

    # Act
    line = cite(key)

    # Assert
    assert item.source in line, f"{key} 的引用串没带出处：{line}"
    assert item.name in line


def test_cited_property_rejects_a_literature_source_without_quote() -> None:
    """有书有页但没抄原文 —— 不算取证过。"""
    # Arrange
    from dataclasses import replace

    sample = _literature_items()[0]

    # Act
    stripped = replace(sample, quote="")

    # Assert
    assert sample.cited is True
    assert stripped.cited is False


# ── 5. 纪律：把取证结果钉住 ────────────────────────────────────────
#: 2026-09-16 逐条取证后仍无出处的九条。
#:
#: **带 UNCITED 的公式不得用于 `impossible` 档结论** —— 那一档的含义是
#: 「数学或物理上不可能」，凭一条找不到出处的公式说「不可能」，
#: 是把没有依据说成了最强的依据。这条纪律在 `rules_quantity.py` 里靠
#: `formulas.formula(key).cited` 降档实现；本文件**刻意不 import `rules_*`**，
#: 只把「哪些还没出处」这份名单钉住：名单一变，这里就红，
#: 逼着改动的人回来交代是补上了出处，还是又引入了一条没出处的判据。
#:
#: 这九条里六条是材料力学/结构力学（截面二次矩、回转半径、长细比、欧拉临界力、
#: qL²/8、从属面积法）。**Knight《Physics》定向 OCR 入库后它们仍然没有出处** ——
#: 入库的只有 §6.1/§12.4/§12.8/§15.6 四节（普通物理），这六条属材料力学，
#: 真正的出处在材料力学教材与荷载/混凝土规范。特别提醒：Knight §12.4 的
#: `moment of inertia` 是**质量**惯性矩（ML²/12，kg·m²），与截面的**面积**
#: 二次矩（bh³/12，m⁴）只是长得像 —— 别拿它去把上面那条从名单里划掉。
#: 另三条是计算几何算法（射线法、Sutherland–Hodgman、旋转卡壳），
#: 这批书要么不讲、要么讲的是别的算法。
EXPECTED_UNCITED = (
    "geometry.jordan_curve_ray_casting",
    "geometry.min_area_rect",
    "geometry.polygon_clipping_convex_requirement",
    "mechanics.euler_buckling",
    "mechanics.radius_of_gyration",
    "mechanics.second_moment_rectangle",
    "mechanics.simply_supported_udl_moment",
    "mechanics.slenderness_ratio",
    "mechanics.tributary_area_load",
)


def test_uncited_keys_is_exactly_the_recorded_list() -> None:
    # Arrange / Act
    actual = uncited_keys()

    # Assert
    assert actual == EXPECTED_UNCITED, (
        "没出处的公式名单变了。补上出处就把它从 EXPECTED_UNCITED 删掉；"
        "新增一条没出处的公式就要在这里显式登记 —— "
        f"多出来：{sorted(set(actual) - set(EXPECTED_UNCITED))}；"
        f"少掉了：{sorted(set(EXPECTED_UNCITED) - set(actual))}")


def test_no_mechanics_formula_except_equilibrium_is_cited() -> None:
    """力学这一族的取证边界：只有静力平衡找到了等价陈述，其余六条都没有。"""
    # Arrange
    mechanics = {k for k in FORMULAS if k.startswith("mechanics.")}

    # Act
    cited = {k for k in mechanics if FORMULAS[k].cited}

    # Assert
    assert cited == {"mechanics.static_equilibrium"}, (
        f"力学条目的取证状态与记录不符：已取证的是 {sorted(cited)}")
