"""图层名 → 构件类型:AIA 规范支持 —— 第二工程让这条路首次有真实数据。

**背景**:大歌剧院 **0 图层**,Phase C 的 `auto_label` + `layer_class_map`
整条路建好后从未在真实数据上生效。轨道交通 **93.1% 有图层**,
且图元 **100% 带 layer 字段**(实测 51688/51689),图层名遵循
**AIA CAD Layer Guidelines**(国际通用标准,非某工程特有):

    S-BEAM-I        结构-梁
    S-COLS-HATCH    结构-柱
    C-SHET-TTLB     土建-图框-标题块

这让构件识别可以从**几何猜测**升级为**读图层**。

**两个实测缺陷**:

1. AIA 代码被 AutoCAD 的 **xref 绑定前缀**包裹(`PLAN_F01$0$0S-BEAM-I`),
   现有 prefixes 匹配全部落空 —— beam **零命中**,而该图有 4240 个梁图元;
2. **`C-SHET-TTLB`(图框)被判为 window** —— window 规则里的 `C-` 前缀
   (中文「窗」拼音)撞上 AIA 的 **C = Civil 学科代码**。
   这正是 window 命中数异常高达 49670 的原因。
"""
from __future__ import annotations

import pytest

from core.model3d.layer_conventions import classify_by_layer, normalize_layer_name


# ── xref 前缀剥离 ───────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("raw,expected", [
    ("PLAN_F01$0$0S-BEAM-I", "S-BEAM-I"),
    ("COLS_F01$0$0S-COLS-HATCH", "S-COLS-HATCH"),
    ("S-F01-L$0$0S-BEAM-TEXT", "S-BEAM-TEXT"),
    ("S-BEAM-I", "S-BEAM-I"),          # 无前缀时原样
    ("", ""),
])
def test_xref_prefix_is_stripped(raw, expected):
    """`$N$N` 是 AutoCAD **xref 绑定**的分隔符(通用约定,非工程特有)。"""
    assert normalize_layer_name(raw) == expected


@pytest.mark.unit
def test_beam_is_recognised_through_the_xref_prefix():
    """**核心用例**:实测 4240 个梁图元此前零命中。"""
    assert classify_by_layer("PLAN_F01$0$0S-BEAM-I") == "beam"
    assert classify_by_layer("S-F01-L$0$0S-BEAM-TEXT") == "beam"


@pytest.mark.unit
def test_column_is_recognised_through_the_xref_prefix():
    assert classify_by_layer("COLS_F01$0$0S-COLS-HATCH") == "column"


@pytest.mark.unit
def test_wall_is_recognised_through_the_xref_prefix():
    assert classify_by_layer("COLS_F01$0$0S-WALL-LINE") == "wall"


# ── AIA 学科代码 C = Civil,不是「窗」──────────────────────────

@pytest.mark.unit
def test_title_block_is_not_a_window():
    """**回归**:`C-SHET-TTLB` 是图框标题块,被判成 window 了。"""
    assert classify_by_layer("C-SHET-TTLB") != "window"


@pytest.mark.unit
def test_real_windows_still_match():
    """不得误伤真窗:AIA 用 `A-GLAZ`/`A-WIND`,中文用「窗」。"""
    assert classify_by_layer("A-GLAZ") == "window"
    assert classify_by_layer("A-WIND-FULL") == "window"
    assert classify_by_layer("建筑-窗") == "window"


@pytest.mark.unit
def test_empty_layer_is_safe():
    """大歌剧院 0 图层 —— 空输入必须安全返回,不能抛也不能瞎猜。"""
    assert classify_by_layer(None) is None
    assert classify_by_layer("") is None


# ── PDF 图层要真正流进几何(下游一直在等,上游没给)─────────────────

@pytest.mark.unit
def test_pdf_paths_carry_their_layer_into_geometry():
    """**核心用例**:`DrawingGeometry` 早有 `*_layers` 并行列表,
    下游 `_find_slabs(polys, poly_layers, ...)` 也一直在接收 ——
    但 PDF 提取处写着「PDF 无图层/块概念」,统一填空串。

    那个假设对大歌剧院成立(0 图层),对第二工程不成立:
    实测图元 **100% 带 layer**(51688/51689)。
    """
    from core.model3d.geometry_extractor import _collect_pdf_drawings
    from core.model3d.types import DrawingGeometry

    class _P:
        def __init__(self, x, y):
            self.x, self.y = x, y

    class _Rect:
        # 真实 fitz.Rect 有 x1/y1（旋转变换按角点算，需要它们）
        x0, y0, x1, y1 = 0.0, 0.0, 10.0, 10.0
        width, height = 10.0, 10.0

    drawings = [
        {"fill": None, "layer": "PLAN_F01$0$0S-BEAM-I",
         "items": [("l", _P(0, 0), _P(10, 0))]},
        {"fill": (0, 0, 0), "layer": "COLS_F01$0$0S-COLS-HATCH",
         "items": [("re", _Rect())]},
    ]
    class _Page:
        rotation = 0          # 真实 page 必有此属性

        def get_drawings(self):
            return drawings

    geom = DrawingGeometry()
    _collect_pdf_drawings(_Page(), geom)

    assert geom.line_layers == ["PLAN_F01$0$0S-BEAM-I"]
    assert geom.rect_layers == ["COLS_F01$0$0S-COLS-HATCH"]


@pytest.mark.unit
def test_pdf_without_layers_still_fills_empty_strings():
    """**无图层 PDF 不得退化**:大歌剧院走的就是这条路,
    并行列表必须仍与几何等长(下游依赖该契约)。"""
    from core.model3d.geometry_extractor import _collect_pdf_drawings
    from core.model3d.types import DrawingGeometry

    class _P:
        def __init__(self, x, y):
            self.x, self.y = x, y

    class _Page:
        rotation = 0

        def get_drawings(self):
            return [{"fill": None, "items": [("l", _P(0, 0), _P(1, 1))]}]

    geom = DrawingGeometry()
    _collect_pdf_drawings(_Page(), geom)
    assert geom.line_layers == [""]
    assert len(geom.line_layers) == len(geom.lines)


@pytest.mark.unit
def test_filled_path_polygon_also_carries_the_layer():
    """填充路径合成的多边形(柱识别依赖它)同样要带图层。"""
    from core.model3d.geometry_extractor import _collect_pdf_drawings
    from core.model3d.types import DrawingGeometry

    class _P:
        def __init__(self, x, y):
            self.x, self.y = x, y

    class _Page:
        rotation = 0

        def get_drawings(self):
            return [{
                "fill": (0, 0, 0), "layer": "COLS_F01$0$0S-COLS-I",
                "items": [("l", _P(0, 0), _P(1, 0)),
                          ("l", _P(1, 0), _P(1, 1))],
            }]

    geom = DrawingGeometry()
    _collect_pdf_drawings(_Page(), geom)
    assert geom.poly_layers and geom.poly_layers[-1] == "COLS_F01$0$0S-COLS-I"


# ── 图种判断:图层是强证据,图内文字是弱证据 ──────────────────────

@pytest.mark.unit
def test_beam_drawing_is_decided_by_layers_when_available():
    """**核心用例**:图层里 7809 条梁线,却因图内文字判不出而产出 0 根梁。

    实测那张「首层框架梁平面整体配筋图」只有 **4 条可信文字**
    (全是水印签章),其余是坏 CMap 乱码 —— 被 `text_integrity` 正确拦截,
    于是 `_is_beam_drawing` 拿不到「梁」字。

    图层是设计师**明确标注**的,比图内文字可靠得多。
    """
    from core.model3d.element_recognizer import _is_beam_drawing

    layers = ["PLAN_F01$0$0S-BEAM-I"] * 200
    assert _is_beam_drawing("", line_layers=layers)


@pytest.mark.unit
def test_text_evidence_still_works_without_layers():
    """**无图层不得退化**:大歌剧院走的正是文本这条路。"""
    from core.model3d.element_recognizer import _is_beam_drawing

    assert _is_beam_drawing("三层梁配筋图")
    assert _is_beam_drawing("三层梁配筋图", line_layers=[""] * 100)
    assert not _is_beam_drawing("柱平面布置图")


@pytest.mark.unit
def test_a_few_beam_lines_do_not_make_it_a_beam_drawing():
    """**少量引用不算** —— 柱图里引几条梁线不该把整张图判成梁图。"""
    from core.model3d.element_recognizer import _is_beam_drawing

    layers = ["PLAN_F01$0$0S-BEAM-I"] * 5 + ["COLS_F01$0$0S-COLS-I"] * 300
    assert not _is_beam_drawing("柱平面布置图", line_layers=layers)


# ── 标注/文字图层不是构件图层(大歌剧院实测)──────────────────────

@pytest.mark.unit
def test_annotation_layers_are_not_components():
    """**实测**:`1区立柱桩及钢立柱平面布置图` 一张图贡献 **3410 根柱**,
    全部来自图层名「**立柱桩标注**」—— 那是**标注图层**,画的是
    引线与文字,不是柱本身。

    中文图层名靠子串「柱」判 column,把标注层一并吃了进来。
    AIA 规范里这类层有明确后缀(`-TEXT`/`-DIMS`/`-NOTE`/`-IDEN`),
    中文则用「标注」「文字」「标高」「说明」——**都是通用制图用语**,
    不是某工程的命名习惯。
    """
    from core.model3d.layer_conventions import classify_by_layer

    from core.model3d.layer_conventions import is_annotation_layer

    for layer in ("立柱桩标注", "柱编号文字", "梁配筋标注",
                  "S-COLS-TEXT", "S-BEAM-DIMS", "A-WALL-IDEN"):
        assert is_annotation_layer(layer), layer


@pytest.mark.unit
def test_real_component_layers_still_match():
    """**不得误伤构件层** —— 只排除标注,不排除构件。"""
    from core.model3d.layer_conventions import classify_by_layer

    from core.model3d.layer_conventions import is_annotation_layer

    assert not is_annotation_layer("立柱桩")
    assert classify_by_layer("立柱桩") == "column"
    assert classify_by_layer("PLAN_F01$0$0S-BEAM-I") == "beam"
    assert classify_by_layer("COLS_F01$0$0S-COLS-HATCH") == "column"
    assert classify_by_layer("结构-柱") == "column"


# ── 钢筋图层不是构件图层 + xref 剥离不得丢失主层名 ────────────────

@pytest.mark.unit
def test_rebar_layers_are_not_components():
    """**实测根因**:墙配筋图上 711 个「柱」来自图层
    `S-S-WALL-1F$0$墙柱纵筋` / `墙柱箍筋` / `墙柱箍筋大样` ——
    那画的是**钢筋**(纵筋/箍筋),不是构件本身。

    「纵筋/箍筋/配筋/钢筋」是通用的结构制图用语,不是某工程的命名。
    """
    from core.model3d.layer_conventions import is_annotation_layer

    for layer in ("墙柱纵筋", "墙柱箍筋", "墙柱箍筋大样",
                  "梁配筋", "板底钢筋", "S-COLS-REBAR"):
        assert is_annotation_layer(layer), layer


@pytest.mark.unit
def test_xref_prefix_carries_the_authoritative_layer_name():
    """**xref 剥离丢了更可靠的信息**:`S-S-WALL-1F$0$墙柱纵筋` 的
    主层名是 **S-WALL**(墙),剥离后只剩「墙柱纵筋」,含「柱」判成了柱。

    修法:**先用完整名匹配,失败再用剥离后的名** ——
    xref 前缀里的 AIA 代码是设计师在原图里的标注,比子层名可靠。
    """
    from core.model3d.layer_conventions import classify_by_layer

    assert classify_by_layer("S-S-WALL-1F$0$0S-WALL-HATCH边缘构件") == "wall"


@pytest.mark.unit
def test_stripping_still_works_when_prefix_has_no_code():
    """前缀里没有 AIA 代码时,剥离后的名仍要能用(第二工程的形态)。"""
    from core.model3d.layer_conventions import classify_by_layer

    assert classify_by_layer("PLAN_F01$0$0S-BEAM-I") == "beam"
    assert classify_by_layer("COLS_F01$0$0S-COLS-HATCH") == "column"
