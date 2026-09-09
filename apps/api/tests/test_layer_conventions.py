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
    # 三组都必须活着（annotation 组此前是 .py 硬编码正则，挪进词表后同理）
    assert lc.is_non_component_layer("立柱桩标注")
    assert lc.is_non_component_layer("TEXT")
    assert lc.is_annotation_layer("S-COLU-DIMS")
    assert lc.is_non_component_layer("I—平面—墙面材料")
    assert lc.is_non_component_layer("I—隔墙—地面阴影")
    # 组内豁免也得跟着活 —— 否则降级时把构件填充截面一起清掉
    assert not lc.is_non_component_layer("I—隔墙—填充01(线状)")
    assert not lc.is_non_component_layer("钢筋混凝土墙")


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
def test_图框与标注语义不同但对识别器是同一个答案():
    """两组**语义不同**，却都不该产出构件 —— 所以闸取并集、开关按组。

    `S-BEAM-TEXT` 是梁的文字标注：**依附于梁**，语义上属于 beam
    （`classify_by_layer` 照旧答 beam —— 那个问题的答案没变）。
    `C-SHET-TTLB` 是图框：**不依附任何构件**，与建筑实体零关系。

    **但识别器问的不是这个**，它问「这条线是不是构件几何」，
    两组的答案都是「不是」，所以 `is_non_component_layer` 取并集；
    要分别开关就问 `gate_groups` / `is_annotation_layer`。
    并集的依据是实测：墙的 16 格误检里标注占 4 格（25%，见 09-20 批），
    梁的 13 格误检里标注线 4 + 钢筋线 1 + 图框 3（62%，见 09-23 批）——
    分开关掉哪一半，另一半都还在造假墙假梁。
    """
    # 两组语义分得开
    assert lc.is_annotation_layer("S-BEAM-TEXT")
    assert not lc.is_annotation_layer("C-SHET-TTLB")
    assert lc.is_gate_group_hit("C-SHET-TTLB", "sheet")
    assert not lc.is_gate_group_hit("S-BEAM-TEXT", "sheet")
    assert lc.classify_by_layer("S-BEAM-TEXT") == "beam"   # 类别问题的答案不变
    # 对识别器是同一个答案
    assert lc.is_non_component_layer("S-BEAM-TEXT")
    assert lc.is_non_component_layer("C-SHET-TTLB")


# ── finish 组：装修饰面被判成结构墙的那批 ─────────────────────
#
# 全库普查（4109 图 / 27905 个图层名）实测的**反向错**：装修饰面层被 wall 的
# 通用子串「墙」判成结构墙。四个最大的（合计约 3.8 万 path，占 wall 命中 0.5%）：
#     I—平面—墙面材料 16721 / I—隔墙—地面阴影 10875
#     I—平面—外墙装饰面层线 9855 / I—墙面—风口 715（实为暖通风口，见 PROGRESS）
_FINISH_LAYERS = ("I—平面—墙面材料", "I—平面—外墙装饰面层线", "I—隔墙—地面阴影")


@pytest.mark.unit
@pytest.mark.parametrize("layer", _FINISH_LAYERS)
def test_饰面图层判为非构件(layer):
    """饰面画的是**贴在构件表面的材料表达**，不是构件本体。"""
    assert lc.is_non_component_layer(layer)
    assert lc.is_gate_group_hit(layer, "finish")


@pytest.mark.unit
@pytest.mark.parametrize("layer", _FINISH_LAYERS)
def test_饰面图层照旧被分类为墙(layer):
    """**分类器答 wall 是故意保留的**。

    让 `classify_by_layer` 对它们返回 None 会**更糟**：element_recognizer 里
    设备与柱的判据是 `_kind is not None and _kind != "equipment"`，
    None 是**放行**，会掉进「按尺寸猜」的路径。真正拦得住的是这道闸。
    """
    assert lc.classify_by_layer(layer) == "wall"


@pytest.mark.unit
@pytest.mark.parametrize("layer", [
    "I—隔墙—填充01(线状)",   # 50.2 万 path —— 填充截面**就是构件本身**
    "I—装饰—填充01(线状)",   # 含「装饰」正落在 finish 词表上，靠豁免保住
    "S-COLS-HATCH",          # 同理：`_find_columns` 明确依赖柱填充
    "I—平面—填充图案",       # 「图案」是饰面词，但「填充」豁免它
])
def test_填充图层仍是构件(layer):
    """**填充不是饰面**。装修 60 张实测：含填充的图层 22.4 万图元（该批 18.6%），
    其中 `I—装饰—填充01(线状)` 一层 10.2 万 —— 清掉会造成大面积构件消失。"""
    assert not lc.is_non_component_layer(layer)


@pytest.mark.unit
@pytest.mark.parametrize("layer", [
    "S-WALL", "剪力墙", "地下室外墙", "钢筋混凝土墙", "A-WALL-1F", "S-COLU",
])
def test_真构件图层不被饰面闸误杀(layer):
    """零回归对照：真构件图层一个都不能被这道闸拦下。"""
    assert not lc.is_non_component_layer(layer)


@pytest.mark.unit
def test_饰面词在外参前缀里不牵连真图层():
    """外参前缀里的「装饰」是**来源图纸的名字**，不是这个图层的语义。

    与既有的「配筋前缀不定罪」「图框前缀不定罪」是同一条纪律 ——
    装修图套外参极多（实测 `4F平面$0$I—隔墙—填充01(线状)`），
    不剥前缀就会把装修图里引用的**结构墙**整片误杀。
    """
    assert not lc.is_non_component_layer("装饰施工图-2F$0$S-WALL")
    assert not lc.is_non_component_layer("室内装饰平面|A-WALL")
    assert lc.is_non_component_layer("A-1F$0$I—平面—墙面材料")   # 词在子层名里才算


@pytest.mark.unit
def test_yaml只能给闸加词挖不掉地板(monkeypatch, tmp_path):
    """闸的词表**单一真相源在 yaml**，但与 `.py` 兜底取并集。

    与构件映射相反：那边 yaml 说了算（判不出只是少认几个构件），
    这边 yaml 挖不掉地板（拦不住会**反向放行假构件**）。
    """
    custom = tmp_path / "conv.yaml"
    custom.write_text(
        "conventions: []\n"
        "non_component:\n"
        "  - group: finish\n"
        "    substrings: ['某个自定义饰面词']\n",
        encoding="utf-8")
    monkeypatch.setattr(lc, "_CONVENTIONS_FILE", custom)
    lc.load_conventions.cache_clear()

    assert lc.is_non_component_layer("I—平面—某个自定义饰面词")   # yaml 加的词生效
    assert lc.is_non_component_layer("I—平面—墙面材料")          # 兜底地板还在
    assert not lc.is_non_component_layer("I—隔墙—填充01(线状)")  # 豁免也还在
