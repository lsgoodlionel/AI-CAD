"""结构楼板厚度提取 —— 板厚不再用硬编码常量。纯函数。

## 领域依据(用户指出的三个来源)

1. **直接标注在平面图内**(常在图名下方):`板厚150`、`底板(板厚1700)`;
2. **在图纸说明内**:说明常规板厚,如 `本图未注明板厚为120mm`、
   `未注明的楼板、坡道板板厚150` —— 这是该图的**兜底默认值**,覆盖面最广;
   (也有「说明指定图例区域代表的板厚」,需图例配合,本模块先覆盖前者)
3. **剖面图标注**:`筏板厚度H=1000mm`、`h=800mm`,或由标高符号相减得出。

## 必须区分「结构板厚」与「建筑做法层」

档案中 2796 条含「厚」的文本里,绝大多数是**做法层**——`250厚级配碎石垫层`、
`80厚花岗岩石材`、`20厚DSM20预拌砂浆`。它们不是结构楼板厚度,误采会让模型板厚全错。
判据:做法层的「厚」后紧跟**材料名**;结构板厚以 `板厚` / `h=` / `筏板厚度` 引导。
"""
from __future__ import annotations

import re

#: 结构板厚合理区间(mm):普通楼板 80~500,筏板/底板可达 4000
MIN_THICKNESS_MM = 80
MAX_THICKNESS_MM = 4000

#: 建筑做法层材料词——「XX厚<材料>」形态一律排除
_MATERIAL_WORDS = (
    "垫层", "保温", "岩棉", "面层", "砂浆", "找平", "石材", "花岗岩", "沥青",
    "防水", "隔声", "隔汽", "填充", "细石", "碎石", "级配", "铺装", "涂料",
    "面砖", "地砖", "木地板", "玻璃", "钢板", "板材", "挤塑", "聚苯", "抹灰",
)

#: 「未注明…板厚…120」——图纸说明里的**默认板厚**(优先级最高,覆盖全图)
_DEFAULT_PATTERNS = (
    re.compile(r"未注明[^。;；]{0,20}?板厚[^0-9]{0,6}(\d{2,4})"),
    re.compile(r"未注明[^。;；]{0,20}?板[^0-9]{0,4}厚[^0-9]{0,4}(\d{2,4})"),
)
#: 筏板/底板厚度(基础,数值大)
_RAFT_PATTERN = re.compile(r"(?:筏板|底板)[^0-9]{0,6}[Hh]?\s*[=＝]?\s*(\d{3,4})")
#: 一般板厚标注:板厚150 / 板厚 = 150
_PLAIN_PATTERN = re.compile(r"板厚[^0-9]{0,4}(\d{2,4})")
#: h=800mm 形式(剖面/详图常用)
_H_PATTERN = re.compile(r"(?:^|[^A-Za-z])[Hh]\s*[=＝]\s*(\d{2,4})")


def _is_material_layer(text: str) -> bool:
    """是否建筑做法层(而非结构板厚)。"""
    return any(word in text for word in _MATERIAL_WORDS)


def _valid(value: int) -> bool:
    return MIN_THICKNESS_MM <= value <= MAX_THICKNESS_MM


def extract_thickness_specs(texts: list[str]) -> list[dict]:
    """图纸文本 → 板厚规格列表 [{value_mm, kind, raw}]。

    kind: `default`(说明里的未注明默认值)/ `raft`(筏板底板)/ `slab`(一般板厚标注)。
    做法层文本一律跳过。
    """
    out: list[dict] = []
    for raw in texts or []:
        text = str(raw or "")
        if not text:
            continue
        for pattern in _DEFAULT_PATTERNS:
            for m in pattern.findall(text):
                if _valid(int(m)):
                    out.append({"value_mm": int(m), "kind": "default", "raw": text[:60]})
        for m in _RAFT_PATTERN.findall(text):
            if _valid(int(m)):
                out.append({"value_mm": int(m), "kind": "raft", "raw": text[:60]})
        if _is_material_layer(text):
            continue          # 做法层:不取其一般板厚/h= 数值
        for m in _PLAIN_PATTERN.findall(text):
            if _valid(int(m)):
                out.append({"value_mm": int(m), "kind": "slab", "raw": text[:60]})
        for m in _H_PATTERN.findall(text):
            if _valid(int(m)):
                out.append({"value_mm": int(m), "kind": "slab", "raw": text[:60]})
    return out


def pick_thickness(specs: list[dict], is_raft: bool = False) -> dict | None:
    """从规格中选出该图应采用的板厚 → {value_m, kind, support, raw}。

    优先级:筏板图取 `raft`;否则 `default`(说明里的未注明值,覆盖全图)优先于
    零散 `slab` 标注;同类取**众数**(多处一致者更可信)。无有效值 → None。
    """
    if not specs:
        return None
    order = ("raft",) if is_raft else ("default", "slab", "raft")
    for kind in order:
        pool = [s for s in specs if s["kind"] == kind]
        if not pool:
            continue
        tally: dict[int, int] = {}
        for s in pool:
            tally[s["value_mm"]] = tally.get(s["value_mm"], 0) + 1
        value, support = max(tally.items(), key=lambda kv: (kv[1], -kv[0]))
        raw = next(s["raw"] for s in pool if s["value_mm"] == value)
        return {"value_m": round(value / 1000, 3), "kind": kind,
                "support": support, "raw": raw}
    return None


def apply_scene_slab_thickness(
    floors: list[dict], thickness_by_drawing: dict[str, dict],
) -> dict:
    """把按图提取的板厚写入 scene 各层 slabs(替代硬编码常量)。

    thickness_by_drawing: {drawing_id: {"value_m", "kind", "raw"}}。
    只更新**有该图板厚**的板;其余保持原值(硬编码兜底),并标 `thickness_source`
    便于追溯与人审。返回 {updated, total}。
    """
    updated = 0
    total = 0
    for floor in floors or []:
        for slab in (floor.get("elements") or {}).get("slabs") or []:
            total += 1
            spec = thickness_by_drawing.get(str(slab.get("src") or ""))
            if not spec:
                continue
            slab["thickness"] = spec["value_m"]
            slab["thickness_source"] = spec["kind"]      # default/slab/raft
            slab["thickness_raw"] = spec.get("raw", "")[:60]
            updated += 1
    return {"updated": updated, "total": total}
