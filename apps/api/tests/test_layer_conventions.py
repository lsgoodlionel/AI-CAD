"""图层约定加载器 + 分类器测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.model3d import layer_conventions as lc


@pytest.fixture(autouse=True)
def _clear_cache():
    """每个用例前后清空 lru_cache，避免降级测试污染其它用例。"""
    lc.load_conventions.cache_clear()
    yield
    lc.load_conventions.cache_clear()


# ── 加载器 ───────────────────────────────────────────────────
def test_load_conventions_parses_real_yaml():
    conv = lc.load_conventions()
    assert conv.kind_rules, "应从真实 yaml 加载出构件规则"
    assert conv.system_rules, "应从真实 yaml 加载出机电系统规则"
    kinds = {rule.kind for rule in conv.kind_rules}
    assert {"column", "beam", "slab", "wall", "door", "window", "pipe", "equipment", "axis"} <= kinds


def test_load_conventions_is_cached():
    assert lc.load_conventions() is lc.load_conventions()


# ── 精确别名（大小写不敏感）───────────────────────────────────
@pytest.mark.parametrize(
    "layer,expected",
    [
        ("S-COLU", "column"),
        ("结构柱", "column"),
        ("S-BEAM", "beam"),
        ("S-SLAB", "slab"),
        ("楼板", "slab"),
        ("S-WALL", "wall"),
        ("剪力墙", "wall"),
        ("A-WALL", "wall"),
        ("AXIS", "axis"),
        ("轴线", "axis"),
        ("DOTE", "axis"),
    ],
)
def test_classify_by_exact_alias(layer, expected):
    assert lc.classify_by_layer(layer) == expected


def test_alias_is_case_insensitive():
    assert lc.classify_by_layer("s-colu") == "column"
    assert lc.classify_by_layer("s-WaLl") == "wall"


# ── 前缀匹配 ─────────────────────────────────────────────────
@pytest.mark.parametrize(
    "layer,expected",
    [
        ("S-COLU-DIMS", "column"),
        ("S-BEAM-TEXT", "beam"),
        ("S-SLAB-PATT", "slab"),
        ("A-WALL-FULL", "wall"),
        ("S-GRID-IDEN", "axis"),
        ("M-DUCT-SUPPLY", "pipe"),
        ("E-POWR-CABLE", "equipment"),
    ],
)
def test_classify_by_prefix(layer, expected):
    assert lc.classify_by_layer(layer) == expected


# ── 子串 / 正则匹配 ──────────────────────────────────────────
@pytest.mark.parametrize(
    "layer,expected",
    [
        ("结构-框架柱-配筋", "column"),
        ("二层梁配筋图", "beam"),
        ("砌体填充墙", "wall"),
        ("给水管道平面", "pipe"),
        ("定位轴线网", "axis"),
    ],
)
def test_classify_by_substring(layer, expected):
    assert lc.classify_by_layer(layer) == expected


def test_classify_by_regex_pattern():
    # 走正则分支：STR_COL 前缀非精确别名、非 prefixes 列表，但命中 ^(?:S|STR)[-_]?COL
    assert lc.classify_by_layer("STR_COLUMN_MARK") == "column"


# ── 块名优先于图层（门窗典型场景）────────────────────────────
def test_block_name_classifies_door():
    # 图层名无法识别，靠块名 M- 前缀判定为门
    assert lc.classify_by_layer("0", block="M-1521") == "door"


def test_block_name_classifies_window():
    assert lc.classify_by_layer("A-ANNO", block="C-1815") == "window"


def test_block_substring_door_window():
    assert lc.classify_by_layer("", block="双扇门") == "door"
    assert lc.classify_by_layer("", block="推拉窗") == "window"


def test_exact_alias_layer_beats_block():
    # layer 精确别名（column）优先于 block（door）
    assert lc.classify_by_layer("S-COLU", block="M-1521") == "column"


# ── 机电系统判定 ─────────────────────────────────────────────
@pytest.mark.parametrize(
    "layer,expected_system",
    [
        ("M-DUCT-SUPPLY", "暖通"),
        ("暖通风管平面", "暖通"),
        ("P-SANR-PIPE", "给排水"),
        ("给排水系统图", "给排水"),
        ("E-POWR-CABLE", "电气"),
        ("桥架布置", "电气"),
        ("消防喷淋管", "消防"),
    ],
)
def test_classify_system(layer, expected_system):
    assert lc.classify_system(layer) == expected_system


def test_classify_system_substring_beats_prefix():
    # 图层同时含消防子串与 P- 前缀，子串（消防）应胜出
    assert lc.classify_system("P-消火栓") == "消防"


def test_classify_system_unknown_returns_none():
    assert lc.classify_system("S-COLU") is None


# ── 未知 / 空输入 ────────────────────────────────────────────
def test_unknown_layer_returns_none():
    assert lc.classify_by_layer("RANDOM-XYZ-123") is None
    assert lc.classify_by_layer("图框") is None


def test_empty_and_none_inputs_are_safe():
    assert lc.classify_by_layer("") is None
    assert lc.classify_by_layer(None) is None
    assert lc.classify_by_layer(None, block="") is None
    assert lc.classify_system("") is None
    assert lc.classify_system(None) is None


# ── 降级：yaml 缺失 / 损坏 / 无 pyyaml ───────────────────────
def test_missing_yaml_degrades_to_empty(monkeypatch):
    monkeypatch.setattr(lc, "_CONVENTIONS_FILE", Path("/nonexistent/no.yaml"))
    lc.load_conventions.cache_clear()
    conv = lc.load_conventions()
    assert conv.kind_rules == ()
    assert conv.system_rules == ()
    assert lc.classify_by_layer("S-COLU") is None
    assert lc.classify_system("M-DUCT-X") is None


def test_no_pyyaml_degrades_to_empty(monkeypatch):
    monkeypatch.setattr(lc, "_HAS_YAML", False)
    lc.load_conventions.cache_clear()
    assert lc.load_conventions().kind_rules == ()
    assert lc.classify_by_layer("结构柱") is None


def test_corrupt_yaml_degrades_to_empty(monkeypatch, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("conventions: [unterminated\n", encoding="utf-8")
    monkeypatch.setattr(lc, "_CONVENTIONS_FILE", bad)
    lc.load_conventions.cache_clear()
    assert lc.load_conventions().kind_rules == ()


def test_non_mapping_root_degrades_to_empty(monkeypatch, tmp_path):
    bad = tmp_path / "list.yaml"
    bad.write_text("- just\n- a\n- list\n", encoding="utf-8")
    monkeypatch.setattr(lc, "_CONVENTIONS_FILE", bad)
    lc.load_conventions.cache_clear()
    assert lc.load_conventions().kind_rules == ()


# --- 外部参照图层：`外参名|图层名` 是 AutoCAD 通用命名 -------------------

@pytest.mark.unit
def test_xref_attached_prefix_is_stripped_before_classifying():
    """`建筑底板|S-COLU` 是外参 `建筑底板` 里的 `S-COLU` 图层，那是柱。

    AutoCAD 用 `|` 分隔外部参照名与图层名（`|` 是保留字符，手工建的
    图层不能含它），既有实现只剥离了 `$N$` 的**绑定**形式。

    实测后果（轨道交通装修/景观图）：
      `建筑底板|S-COLU` → slab（前缀里的「底板」赢了）
      `建筑底板|A-DOOR` → slab（同上，真实身份是门）
    """
    from core.model3d.layer_conventions import classify_by_layer

    assert classify_by_layer("建筑底板|S-COLU", "") == "column"
    assert classify_by_layer("建筑底板|A-DOOR", "") == "door"
    assert classify_by_layer("01-1F平面底图|S-COLU", "") == "column"


@pytest.mark.unit
def test_bound_xref_form_is_unaffected():
    """`$N$` 绑定形式的既有行为不变。

    `墙柱纵筋` 因含「柱」被 `classify_by_layer` 判成 column，而
    `is_annotation_layer` 判出它是钢筋标注层 —— **两者是两个问题**，
    由调用方组合（`not is_annotation_layer(l) and classify_by_layer(l)`）。
    这条护栏钉住的正是这个组合契约。
    """
    from core.model3d.layer_conventions import (
        classify_by_layer, is_annotation_layer)

    layer = "S-S-WALL-1F$0$墙柱纵筋"
    assert classify_by_layer(layer, "") == "column"
    assert is_annotation_layer(layer)          # 组合后不产出柱


@pytest.mark.unit
def test_annotation_marker_in_the_xref_prefix_does_not_condemn_the_layer():
    """外参前缀里的「配筋」是**来源图纸的名字**，不是这个图层的语义。

    **实测**（大歌剧院「南区一层结构平面图（四）」）：

        S-南区-PLAN-1F - 板配筋(-3.5~0.0)$0$0S-COLS-HATCH
        └────────── 外参（来源图）名 ──────────┘└─ 真正的图层 ─┘

    `S-COLS` 是 AIA 标准的**结构柱**图层，却因为前缀里有「配筋」
    被整体判成标注层 —— 该图 **658 个柱候选全被丢弃**，金标准 12
    根柱识别成 0。

    判据是**只看图层自己的名字**：剥离后 `S-COLS-HATCH` 不含标注词，
    而 `S-S-WALL-1F$0$墙柱纵筋` 剥离后仍是「墙柱纵筋」，照旧判为标注层
    —— 后者的标注词在**子层名**里，那才是图层自己的语义。
    """
    from core.model3d.layer_conventions import is_annotation_layer

    assert not is_annotation_layer(
        "S-南区-PLAN-1F - 板配筋(-3.5~0.0)$0$0S-COLS-HATCH")
    assert is_annotation_layer("S-S-WALL-1F$0$墙柱纵筋")
    assert is_annotation_layer("立柱桩标注")          # 无前缀时不受影响


# ── 非构件图层闸：图框 / 标题块 / 会签栏 ──────────────────────
#
# 这是**第一道**非构件闸（此前只有 `is_annotation_layer`，那是另一个问题：
# 标注依附于某个构件，图框不依附任何构件 —— 见 `is_non_component_layer` 文档）。
#
# 实测（大歌剧院）：`_find_parallel_pairs` 对图层不设任何拦截，
# 图框层的双线边框被当成平行线对，直接产出假墙 ——
#     「底板换撑平面布置图」  `通用-图框C-SHET` 11 面 + `C-SHET-TTLB` 7 面
#     「8F节点大样图」        `A2|C—图框—标题块` 72 面

@pytest.mark.unit
@pytest.mark.parametrize("layer", [
    "通用-图框C-SHET",      # 实测：产出 11 面假墙
    "C-SHET-TTLB",          # 实测：产出 7 面假墙
    "A2|C—图框—标题块",     # 实测：产出 72 面假墙（`|` 前是外参名 A2）
    "图框",
    "A-SHET-TTLB",
    "G-TBLK",
    "会签栏",
    "图签",
])
def test_图框标题块图层判为非构件(layer):
    assert lc.is_non_component_layer(layer)


@pytest.mark.unit
@pytest.mark.parametrize("layer", [
    "S-COLU", "A-WALL", "S-BEAM", "M-PIPE-SUPPLY", "地下室外墙",
    "结构柱", "A-DOOR", "S-SLAB",
])
def test_真构件图层不被闸误杀(layer):
    """闸的代价不对称：放行几条图框线只是多几面假墙，
    误杀真构件层则让整层构件凭空消失（`S-COLS-HATCH` 那次 658 个柱候选归零）。"""
    assert not lc.is_non_component_layer(layer)


@pytest.mark.unit
def test_裸C前缀不被当成图框():
    """**`C-` 是 AIA 的 Civil 学科码**，不是图框专用。

    yaml 里窗的 `C-` 前缀正是因为撞上它才被移除（把 `C-SHET-TTLB`
    判成了窗）。这道闸若图省事写成裸 `C-`，会把整个 Civil 专业
    （总图/道路/管网）全部当成图框拦掉 —— 判据必须落在
    `SHET`/`TTLB` 这些**图框次级码**上，而不是学科码上。
    """
    assert not lc.is_non_component_layer("C-ROAD")
    assert not lc.is_non_component_layer("C-STRM-PIPE")
    assert not lc.is_non_component_layer("C-1")          # 窗编号 C-1
    assert lc.is_non_component_layer("C-SHET-TTLB")      # 次级码命中才拦


@pytest.mark.unit
def test_外参前缀里的图框不牵连真图层():
    """`|` 前是**来源图纸**的名字，不是这个图层的语义。

    与 `test_annotation_marker_in_the_xref_prefix_does_not_condemn_the_layer`
    同一条教训：那次前缀里的「配筋」让 658 个柱候选全被丢弃。
    """
    assert not lc.is_non_component_layer("图框A2$0$S-COLU")
    assert not lc.is_non_component_layer("A2图框|S-COLU")


@pytest.mark.unit
def test_空输入安全():
    assert not lc.is_non_component_layer(None)
    assert not lc.is_non_component_layer("")


@pytest.mark.unit
def test_gate_survives_yaml_loss(monkeypatch):
    """**YAML 丢了，闸必须还在。**

    `load_conventions()` 对缺文件/无 pyyaml/损坏一律降级为空（上面四条降级
    用例）。构件映射降级只是「少认几个构件」，而**非构件闸降级会反向放行
    假构件** —— 代价方向相反，所以这道闸不能只活在 YAML 里。
    """
    monkeypatch.setattr(lc, "_CONVENTIONS_FILE", Path("/nonexistent/no.yaml"))
    lc.load_conventions.cache_clear()
    assert lc.load_conventions().kind_rules == ()        # 确认真的降级了
    assert lc.is_non_component_layer("通用-图框C-SHET")
    assert lc.is_non_component_layer("C-SHET-TTLB")
    assert lc.is_non_component_layer("A2|C—图框—标题块")


@pytest.mark.unit
def test_兜底词表与yaml不漂移():
    """两份词表的一致性 —— YAML 必须**覆盖**兜底词表的每一条。

    HEAD 那次实测的教训：`layer_conventions.yaml`（生产唯一判据）与
    `model3d/layer_class_map.yaml`（只用于数据集标注）互相分岔，
    「灯具」在后者出现 4 次、前者 0 次，整套装修词汇到不了生产路径。
    这条断言让同一个失效模式在闸上无法重演。
    """
    conv = lc.load_conventions()
    assert conv.gate_groups, "真实 yaml 应加载出非构件闸分组"
    for group, vocab in lc._DEFAULT_GATE_VOCAB.items():
        rule = conv.gate_groups.get(group)
        assert rule is not None, f"yaml 缺少兜底词表已有的闸分组: {group}"
        assert set(vocab["substrings"]) <= set(rule.substrings), (
            f"{group} 组：yaml 的 substrings 未覆盖兜底词表")
        yaml_pats = {p.pattern for p in rule.patterns}
        assert set(vocab["patterns"]) <= yaml_pats, (
            f"{group} 组：yaml 的 patterns 未覆盖兜底词表")


@pytest.mark.unit
def test_图框闸与标注闸是两个问题():
    """两道闸互不覆盖 —— 合并成一个正则就再也分不开开关。

    `S-BEAM-TEXT` 是梁的文字标注：**依附于梁**，语义上属于 beam。
    `C-SHET-TTLB` 是图框：**不依附任何构件**，与建筑实体零关系。
    """
    assert lc.is_annotation_layer("S-BEAM-TEXT")
    assert not lc.is_non_component_layer("S-BEAM-TEXT")
    assert lc.is_non_component_layer("C-SHET-TTLB")
    assert not lc.is_annotation_layer("C-SHET-TTLB")
