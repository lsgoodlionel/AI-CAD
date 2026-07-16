"""档案 OCR 文字 → 构件类型标签(Phase E 路径C-下一步)。

纯 PDF 项目无图层、矢量文字取不到,构件类型(幕墙/钢构/围护桩/外墙)只能靠
档案 OCR 的短标签反哺。做法:把带位置的构件短标签经 A1 坐标变换转米,就近
关联到已识别几何构件,附 type_label(不新增顶层类别,只加语义标签)。

反噪声纪律:只收「短标签」(≤ MAX_LABEL_LEN)且命中关键词,排除 note/title
长句(如"钢筋连接应满足规范"含'钢'但非钢构件)。
"""
from __future__ import annotations

# 构件类型关键词(短标签命中即归类;顺序=优先级,先具体后宽泛)
_TYPE_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("curtain_wall", ("幕墙", "玻璃幕", "石材幕", "MQ")),
    ("steel", ("钢立柱", "钢柱", "型钢柱", "劲性柱", "钢梁", "钢支撑",
               "桁架", "网架", "H型钢", "工字钢", "格构柱")),
    ("pile", ("围护桩", "灌注桩", "工程桩", "立柱桩", "抗拔桩", "支护桩",
              "钻孔桩", "咬合桩")),
    ("diaphragm_wall", ("地连墙", "地下连续墙", "连续墙")),
    ("retaining_wall", ("挡土墙", "人防墙", "护壁")),
    ("exterior_wall", ("外墙", "围护墙", "填充外墙")),
]

# 只对短标签分类(构件标签短;长句是说明/标题,含关键词也不算构件)
MAX_LABEL_LEN = 8
# 就近关联容差(米):标签中心到构件质心
DEFAULT_ATTACH_TOL_M = 2.0
# 不参与分类的类别(说明/标题/尺寸/标高/轴号本身)
_EXCLUDED_CATEGORIES = {"note", "title", "dimension", "elevation", "axis", "title_block"}


# 交叉引用/说明前缀:"详幕墙…"/"见…"/"参…"是指引注释,非构件标签
_CROSS_REF_MARKERS = ("详", "见", "参", "如", "按", "注")


def _label_type(text: str) -> str | None:
    """短标签 → 类型;非构件标签(超长/交叉引用/含引导词)返回 None。"""
    s = (text or "").strip()
    if not s or len(s) > MAX_LABEL_LEN:
        return None
    # 交叉引用注释(详幕墙深化/见结施…)不是构件标签
    if s[0] in _CROSS_REF_MARKERS or any(m in s for m in ("图", "说明", "要求", "详图")):
        return None
    for type_name, keywords in _TYPE_KEYWORDS:
        if any(kw in s for kw in keywords):
            return type_name
    return None


def _bbox_center_pt(loc: dict) -> tuple[float, float] | None:
    if not loc:
        return None
    bbox = loc.get("bbox")
    if bbox and len(bbox) >= 4:
        return (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    if "x" in loc and "y" in loc:
        return float(loc["x"]), float(loc["y"])
    return None


def classify_component_labels(archive_items: list[dict], transform) -> list[dict]:
    """档案条目 → [{type, label, x, y(米)}];过滤噪声、经变换转米。"""
    from services.drawing_transform import pt_to_meter

    out: list[dict] = []
    for item in archive_items:
        if item.get("category") in _EXCLUDED_CATEGORIES:
            continue
        type_name = _label_type(str(item.get("content") or ""))
        if type_name is None:
            continue
        center = _bbox_center_pt(item.get("location_json") or {})
        if center is None:
            continue
        x_m, y_m = pt_to_meter(center[0], center[1], transform)
        out.append({
            "type": type_name,
            "label": str(item["content"]).strip(),
            "x": x_m,
            "y": y_m,
        })
    return out


def _element_centroid(el: dict) -> tuple[float, float] | None:
    pts = el.get("outline") or el.get("path") or []
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def attach_type_labels(
    elements: dict[str, list], labels: list[dict], tol_m: float = DEFAULT_ATTACH_TOL_M,
) -> dict[str, list]:
    """为每个几何构件附最近的类型标签(容差内);返回新 elements(不改原对象)。"""
    if not labels:
        return elements
    out: dict[str, list] = {}
    for kind, items in elements.items():
        new_items = []
        for el in items:
            centroid = _element_centroid(el)
            if centroid is None:
                new_items.append(el)
                continue
            cx, cy = centroid
            best = None
            best_d = tol_m
            for lab in labels:
                d = ((cx - lab["x"]) ** 2 + (cy - lab["y"]) ** 2) ** 0.5
                if d < best_d:
                    best_d = d
                    best = lab
            if best is not None:
                el = {**el, "type_label": best["type"], "type_text": best["label"]}
            new_items.append(el)
        out[kind] = new_items
    return out
