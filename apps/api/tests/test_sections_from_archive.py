"""截面表补读**档案层** —— 图内文字取不到时的实测通道。

**缺口**：`build_component_sections` 的正则 `_PIPE_RE` 本就认得
`DN100`/`De75`/`φ100`（实测全部识别正确），但它的**输入**来自
`_section_texts_sync`（图内矢量文字）—— 而大歌剧院的矢量文字**常取不到**
（E3-0 审计已记：「矢量文字取不到」）。

于是出现这个局面：

- 档案层（OCR 产出）里有 **4047 条**管径标注、覆盖 **303 张图**
- 建模侧却仍用硬编码 `DEFAULT_PIPE_DIAMETER_M = 0.1`

⇒ 与 E2-consume「建模 section-z 改读档案标高」同一思路：
**抽取一次、多处消费**，不让同一份信息被重复提取又用不上。
"""
from __future__ import annotations

import pytest

from services.model_component_sections import build_component_sections


@pytest.mark.unit
def test_archive_fills_only_what_the_drawing_lacks():
    """**分层而非混料**:图内取不到的项才用档案补。"""
    from services.model_component_sections import (
        MIN_ARCHIVE_SAMPLES, fill_missing_sections,
    )

    primary = build_component_sections([])            # 图内什么都没取到
    filled = fill_missing_sections(primary, ["DN150"] * MIN_ARCHIVE_SAMPLES)
    assert filled["pipe"].diameter_m == pytest.approx(0.15)
    assert not filled["pipe"].estimated


@pytest.mark.unit
def test_drawing_value_wins_over_archive():
    """**图内有值就不用档案** —— 剖面图上的标注比全图 OCR 精准。"""
    from services.model_component_sections import (
        MIN_ARCHIVE_SAMPLES, fill_missing_sections,
    )

    primary = build_component_sections(["DN200"])     # 图内取到 200
    filled = fill_missing_sections(primary, ["DN150"] * MIN_ARCHIVE_SAMPLES)
    assert filled["pipe"].diameter_m == pytest.approx(0.2)


@pytest.mark.unit
def test_mode_wins_over_outliers():
    """**取中位数** —— 一张图上管径各异,多处一致者更可信。"""
    texts = ["DN100"] * 5 + ["DN300"]
    section = build_component_sections(texts)["pipe"]
    assert section.diameter_m == pytest.approx(0.1)


@pytest.mark.unit
def test_empty_archive_keeps_the_default():
    """**没有实测值就保持默认** —— 不因为「用上了档案」而乱猜。"""
    section = build_component_sections([])["pipe"]
    assert section.estimated
    assert section.diameter_m == pytest.approx(0.1)


@pytest.mark.unit
def test_merge_helper_still_available():
    """`merge_section_texts` 保留(供其它并料场景),但截面表已改用分层。"""
    from services.model_component_sections import merge_section_texts

    merged = merge_section_texts(["DN100"], ["DN150", "DN150"])
    assert merged.count("DN150") == 2 and "DN100" in merged


@pytest.mark.unit
def test_merge_handles_none():
    from services.model_component_sections import merge_section_texts

    assert merge_section_texts(None, None) == []
    assert merge_section_texts(["a"], None) == ["a"]
    assert merge_section_texts(None, ["b"]) == ["b"]


# ── 档案补料必须按图种过滤（差点引入的严重退化）──────────────

@pytest.mark.unit
def test_archive_texts_must_be_scoped_to_section_drawings():
    """**差点引入的退化**:把全库档案文本喂进截面表,梁高众数变成 **0.2m**,
    而默认值是 **0.6m** —— 相当于把全楼的梁高砍掉 67%。

    根因:全库文本里混着门窗尺寸(`900x2100`)、砖尺寸、房间面积,
    `_BH_RE` 分不出它们与梁截面。原设计只取**剖面/详图**的文字，
    那个限定是有道理的 —— 档案补料必须沿用同一限定。
    """
    from services.model_component_sections import build_component_sections

    # 剖面图上的梁截面标注
    section_texts = ["梁300x600"] * 5
    # 全库噪声：门窗表里的洞口尺寸
    noise = ["900x2100"] * 50

    good = build_component_sections(section_texts)["beam"]
    assert good.h_m == pytest.approx(0.6)

    polluted = build_component_sections(section_texts + noise)["beam"]
    assert polluted.h_m != pytest.approx(0.6), "噪声确实会压过真值——所以必须过滤"


@pytest.mark.unit
def test_small_archive_sample_keeps_the_default():
    """**样本不足就别猜** —— 实测档案补料后墙厚由 **7 个样本**定成 0.35m,
    柱由 **30 个**定成 0.2m(200mm 柱不合工程常识)。

    一个众数要覆盖全楼同类构件,证据量必须够。不够就退回默认值 ——
    默认值至少是个有工程依据的常见值,而 7 个样本的众数什么都不是。
    """
    from services.model_component_sections import (
        MIN_ARCHIVE_SAMPLES, fill_missing_sections,
    )

    primary = build_component_sections([])
    few = fill_missing_sections(primary, ["墙厚350"] * (MIN_ARCHIVE_SAMPLES - 1))
    assert few["wall"].estimated, "档案样本不足应保持默认"

    enough = fill_missing_sections(primary, ["墙厚350"] * MIN_ARCHIVE_SAMPLES)
    assert not enough["wall"].estimated
    assert enough["wall"].thickness_m == pytest.approx(0.35)


@pytest.mark.unit
def test_highly_dispersed_values_are_not_representative():
    """**离散度太大时一个中位数没有代表性**。

    实测:档案里管径跨 DN25~DN300(**12 倍**),中位数 32mm 是**支管**的值 ——
    拿它覆盖全楼管道会让主管从 100mm 缩成 32mm。
    而梁高多在 400~600mm(1.5 倍),中位数 400mm 是有代表性的。

    判据:**四分位跨度 / 中位数** 超过阈值即不可代表全体,退回默认。
    """
    from services.model_component_sections import (
        MIN_ARCHIVE_SAMPLES, fill_missing_sections,
    )

    primary = build_component_sections([])
    # 管径跨度大：DN25 与 DN300 各半
    wide = ["DN25"] * (MIN_ARCHIVE_SAMPLES) + ["DN300"] * MIN_ARCHIVE_SAMPLES
    assert fill_missing_sections(primary, wide)["pipe"].estimated, \
        "跨度 12 倍时不该用一个值覆盖全体"

    # 集中分布：全是 DN100
    tight = ["DN100"] * (MIN_ARCHIVE_SAMPLES * 2)
    filled = fill_missing_sections(primary, tight)
    assert not filled["pipe"].estimated
    assert filled["pipe"].diameter_m == pytest.approx(0.1)
