"""构件的**反算参数**：把场景里的米坐标还原成图纸页面点。

**为什么必须存下来**：构件坐标用的是识别器**自己算的** `scale`/`origin_pt`/
`page_h`（不是 `drawing_transform`，那张表构件坐标压根不读）。这三个值此前
用完即弃，于是要把模型叠回图纸核对，只能重跑一遍识别 —— 而识别代码一改
（2026-09-15 接入图框印刷比例就是一次），存量场景就再也对不准。
实测 v85 的自交柱反算到页面点 x=-170，整个落在纸外。

**还必须连位移一起存**：构件并入楼层前会被共识平移、成对配准、或世界坐标
摆放动过。只用 scale/origin 反算，得到的是「识别当时」的位置，不是场景里的。

**精度边界**：柱截面模数化对齐发生在这之后（见 `services/model_builder`），
所以反算精度到模数容差为止，不是逐点精确。世界坐标摆放是旋转+平移，
`placed=True` 的图**不可**用 `shift_m` 逆推 —— 本模块会拒绝，而不是给个错数。
"""
from __future__ import annotations

from dataclasses import dataclass


class FrameNotReversible(Exception):
    """这张图的构件不能只靠位移逆推回页面点。

    抛它而不是返回一个「差不多」的坐标：叠错位的框看起来和叠对了一样，
    只是指向别的东西 —— 那比没有更坏。
    """


@dataclass(frozen=True)
class ElementFrame:
    """一张图的反算参数（`scene.floors[].element_frames[drawing_id]`）。"""
    scale_m_pt: float
    origin_pt: tuple[float, float]
    page_h: float
    shift_m: tuple[float, float] = (0.0, 0.0)
    placed: bool = False

    @classmethod
    def from_dict(cls, raw: dict) -> "ElementFrame":
        origin = list(raw.get("origin_pt") or (0.0, 0.0))
        shift = list(raw.get("shift_m") or (0.0, 0.0))
        return cls(
            scale_m_pt=float(raw.get("scale_m_pt") or 0.0),
            origin_pt=(float(origin[0]), float(origin[1])),
            page_h=float(raw.get("page_h") or 0.0),
            shift_m=(float(shift[0]), float(shift[1])),
            placed=bool(raw.get("placed")),
        )

    @property
    def usable(self) -> bool:
        return self.scale_m_pt > 0 and self.page_h > 0

    def to_page(self, x_m: float, y_m: float) -> tuple[float, float]:
        """场景米坐标 → 页面点。

        与 `core.model3d.yolo_export.meters_to_page` 同一套换算，
        额外先**扣掉并入楼层时施加的位移**。
        """
        if not self.usable:
            raise FrameNotReversible(
                f"反算参数不完整（scale={self.scale_m_pt}, page_h={self.page_h}）")
        if self.placed:
            raise FrameNotReversible(
                "这张图按世界坐标摆放过（旋转+平移），位移逆推不回去；"
                "要叠图请用该图的 placement 参数")
        from core.model3d.yolo_export import meters_to_page

        return meters_to_page(x_m - self.shift_m[0], y_m - self.shift_m[1],
                              self.scale_m_pt, self.origin_pt, self.page_h)


def frame_of(floor: dict, drawing_id: str) -> ElementFrame | None:
    """取某层某图的反算参数；没有就返回 None（**不编一个默认值**）。"""
    raw = (floor.get("element_frames") or {}).get(str(drawing_id))
    return ElementFrame.from_dict(raw) if isinstance(raw, dict) else None
