"""CADTransformer 输入/输出适配器（纯函数，**不依赖 torch/dgl**）。

职责是把共享契约 ``PrimitiveDoc``（C-02/C-03 产物）翻译成 CADTransformer 能吃的
输入（SVG + 归一化图元节点序列），并把模型的逐图元预测解析回共享契约
``SymbolCandidate``。因不含任何深度学习依赖，本模块可在 CI（无 GPU/torch/dgl）下
完整单测。

CADTransformer（VITA-Group，MIT）在 FloorPlanCAD 上做**全景符号 spotting**：
对每个矢量图元预测语义类别 + 实例分组。官方类目是 FloorPlanCAD 的建筑平面图类目
（门/窗/墙/家具/洁具/楼梯 等），此处将其映射到本平台 9 类 taxonomy
（``column/beam/slab/wall/door/window/pipe/equipment/axis``），无对应者丢弃。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from core.model3d.preprocess.dxf_to_svg import doc_to_svg
from core.model3d.preprocess.normalize import NormalizeParams, normalize_doc
from core.model3d.preprocess.schema import PrimitiveDoc

from ..types import SymbolCandidate

# ---------------------------------------------------------------------------
# FloorPlanCAD 官方语义类目 → 本平台 9 类 taxonomy 映射
# 键为规范化（小写、去空格/下划线）后的类名；None 表示不属于任一 taxonomy，丢弃。
# ---------------------------------------------------------------------------
_RAW_CLASS_TO_CATEGORY: dict[str, str | None] = {
    # 门（各类门统一归 door）
    "single door": "door",
    "double door": "door",
    "sliding door": "door",
    "folding door": "door",
    "revolving door": "door",
    "rolling door": "door",
    "door": "door",
    # 窗
    "window": "window",
    "bay window": "window",
    "blind window": "window",
    "opening symbol": "window",
    # 墙体（含幕墙/栏杆归 wall）
    "wall": "wall",
    "curtain wall": "wall",
    "railing": "wall",
    # 设备/家具/洁具/垂直交通 统一归 equipment
    "sofa": "equipment",
    "bed": "equipment",
    "chair": "equipment",
    "table": "equipment",
    "tv cabinet": "equipment",
    "wardrobe": "equipment",
    "cabinet": "equipment",
    "gas stove": "equipment",
    "sink": "equipment",
    "refrigerator": "equipment",
    "airconditioner": "equipment",
    "air conditioner": "equipment",
    "bath": "equipment",
    "bath tub": "equipment",
    "washing machine": "equipment",
    "urinal": "equipment",
    "squat toilet": "equipment",
    "toilet": "equipment",
    "stairs": "equipment",
    "elevator": "equipment",
    "escalator": "equipment",
    "row chairs": "equipment",
    "parking spot": "equipment",
    # 明确背景 / 无意义类
    "background": None,
    "none": None,
}


def _norm_class_name(name: str) -> str:
    return " ".join(str(name).strip().lower().replace("_", " ").split())


def map_class_name(raw_name: str) -> str | None:
    """FloorPlanCAD 类名 → 9 类 taxonomy；未知类返回 None（丢弃）。"""
    return _RAW_CLASS_TO_CATEGORY.get(_norm_class_name(raw_name))


# ---------------------------------------------------------------------------
# 输入适配：PrimitiveDoc → CADTransformer 输入
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class NodeFeature:
    """单个图元的图节点特征（归一化域，供图卷积模型消费）。"""
    primitive_id: int
    ptype: str
    centroid: tuple[float, float]
    points: tuple[tuple[float, float], ...]
    layer: str = ""
    block: str = ""


@dataclass(frozen=True)
class CADTInput:
    """CADTransformer 推理输入：SVG（原始页面域） + 归一化节点序列 + 归一化参数。"""
    svg: str
    nodes: tuple[NodeFeature, ...]
    normalize: NormalizeParams
    page_w: float = 0.0
    page_h: float = 0.0

    @property
    def node_count(self) -> int:
        return len(self.nodes)


def _centroid(points: tuple[tuple[float, float], ...]) -> tuple[float, float]:
    if not points:
        return (0.0, 0.0)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def build_model_input(doc: PrimitiveDoc) -> CADTInput:
    """``PrimitiveDoc`` → CADTransformer 输入（纯函数）。

    - SVG 用**原始页面坐标**（``doc_to_svg``），保留可溯源的 data-layer/data-id；
    - 节点特征用**等比归一化坐标**（``normalize_doc``），对齐模型训练时的输入域。
    """
    svg = doc_to_svg(doc)
    norm_doc, params = normalize_doc(doc)
    nodes = tuple(
        NodeFeature(
            primitive_id=p.id,
            ptype=p.type,
            centroid=_centroid(p.points),
            points=p.points,
            layer=p.layer,
            block=p.block,
        )
        for p in norm_doc.primitives
    )
    return CADTInput(
        svg=svg,
        nodes=nodes,
        normalize=params,
        page_w=doc.page_w,
        page_h=doc.page_h,
    )


# ---------------------------------------------------------------------------
# 输出适配：模型逐图元预测 → SymbolCandidate
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class NodePrediction:
    """模型对单个图元的预测（全景 spotting）。

    - ``instance_id``：同一实例（一个符号）的图元共享同一 id；``< 0`` 视为背景/未成实例。
    """
    primitive_id: int
    class_name: str
    confidence: float
    instance_id: int = -1
    evidence: dict = field(default_factory=dict)


def _bbox_union(
    boxes: list[tuple[float, float, float, float]]
) -> tuple[float, float, float, float] | None:
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _bbox_of_points(
    points: tuple[tuple[float, float], ...]
) -> tuple[float, float, float, float] | None:
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _majority_class(names: list[str]) -> str:
    counts: dict[str, int] = defaultdict(int)
    for n in names:
        counts[n] += 1
    return max(counts.items(), key=lambda kv: kv[1])[0]


def parse_predictions(
    doc: PrimitiveDoc,
    predictions: list[NodePrediction],
    *,
    backend_name: str = "cadtransformer",
    weights_id: str = "",
) -> list[SymbolCandidate]:
    """逐图元预测 → 按实例聚合的 ``SymbolCandidate`` 列表（纯函数）。

    - 丢弃 ``instance_id < 0`` 或映射到 None（非 taxonomy）的预测；
    - 同一 ``instance_id`` 的图元聚为一个候选：类别取多数投票、置信取均值、
      bbox 取成员图元并集（**原始页面坐标**，取自 ``doc``）。
    """
    prim_bbox: dict[int, tuple[float, float, float, float]] = {}
    for p in doc.primitives:
        bb = _bbox_of_points(p.points)
        if bb is not None:
            prim_bbox[p.id] = bb

    groups: dict[int, list[NodePrediction]] = defaultdict(list)
    for pred in predictions:
        if pred.instance_id < 0:
            continue
        if map_class_name(pred.class_name) is None:
            continue
        groups[pred.instance_id].append(pred)

    candidates: list[SymbolCandidate] = []
    for instance_id, members in sorted(groups.items()):
        category = _majority_class([map_class_name(m.class_name) or "" for m in members])
        if not category:
            continue
        boxes = [prim_bbox[m.primitive_id] for m in members if m.primitive_id in prim_bbox]
        bbox = _bbox_union(boxes)
        if bbox is None:
            continue
        confidence = sum(m.confidence for m in members) / len(members)
        primitive_ids = tuple(sorted(m.primitive_id for m in members))
        candidates.append(
            SymbolCandidate(
                category=category,
                confidence=float(max(0.0, min(1.0, confidence))),
                bbox=bbox,
                source="model",
                primitive_ids=primitive_ids,
                evidence={
                    "backend": backend_name,
                    "model": "cadtransformer",
                    "weights_id": weights_id,
                    "instance_id": instance_id,
                    "raw_classes": sorted({m.class_name for m in members}),
                },
            )
        )
    return candidates
