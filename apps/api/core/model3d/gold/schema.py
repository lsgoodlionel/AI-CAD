"""金标准记录格式。

一个 **GoldUnit** = 一处图纸范围（整张图或一块切片）上，若干**对象类**
（columns / axes / title_block / notes / …）各自的真值。

四种真值方法：

| method | 用于 | 真值形态 |
|---|---|---|
| `count` | 只数个数 | 整数 |
| `instances` | 实体级 | 带轴号身份 + 尺寸的列表 |
| `text` | 图名 / 总说明 | 字符串 |
| `fields` | 图框标题栏 | 字段映射 |
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: 进入指标的最低把握度。低于此值的记录留档但不计分 ——
#: 实测把握 <0.8 的块，人工复核后有近半被判为不可判定。
MIN_CONFIDENCE = 0.8

#: 只有一方给出、且未经人工复核的，不足以当真值。
HUMAN = "human"

METHODS = ("count", "instances", "text", "fields")


@dataclass(frozen=True)
class Instance:
    """一个构件实体。`id` 是轴号（或轴号交点 `1×A`），不是坐标。

    **身份必须带分区**：实测两个工程 5.3 万条识别轴号，裸轴号的重复率
    是 31%/37%，存在重复的图占 61%/53%；改用 `(分区, 轴号)` 后**降到 0%**。
    GB/T 50001 §8.0.5 的分区编号本就规定轴号形如「分区号-轴线号」——
    轴号里已含分区前缀时（`1-A`）不必再写 `zone`。
    """
    id: str
    zone: str | None = None
    size_mm: tuple | None = None
    kind: str | None = None
    note: str = ""

    @property
    def key(self) -> str:
        """配对用的身份键。"""
        return f"{self.zone}·{self.id}" if self.zone is not None else self.id


@dataclass(frozen=True)
class ObjectClass:
    """一处范围上、某一类对象的真值。"""
    name: str
    method: str
    count: int | None = None
    instances: tuple = ()
    text: str | None = None
    fields: dict = field(default_factory=dict)
    confidence: float = 0.0
    verified_by: tuple = ()
    excluded: str | None = None
    note: str = ""

    @property
    def counts_toward_metrics(self) -> bool:
        """够不够格当真值。

        排除项一律不计；其余要么经**人工复核**，要么把握达标 ——
        两个来源独立，不能只靠机器自报的把握度。
        """
        if self.excluded:
            return False
        if HUMAN in self.verified_by:
            return True
        return self.confidence >= MIN_CONFIDENCE


@dataclass(frozen=True)
class GoldUnit:
    unit: str
    source: dict
    classes: dict


def _instance(raw: dict) -> Instance:
    size = raw.get("size_mm")
    zone = raw.get("zone")
    return Instance(
        id=str(raw["id"]),
        zone=None if zone is None else str(zone),
        size_mm=tuple(size) if size else None,
        kind=raw.get("kind"),
        note=raw.get("note", ""),
    )


def _object_class(name: str, raw: dict) -> ObjectClass:
    method = raw.get("method")
    if method not in METHODS:
        raise ValueError(f"{name}: 未知的 method {method!r}，可选 {METHODS}")

    instances = tuple(_instance(i) for i in raw.get("instances") or ())
    ids = [i.key for i in instances]
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"{name}: 实体身份重复 {dupes} —— 同一分区的同一轴号不应出现两次")

    count = raw.get("count")
    if method == "instances":
        count = len(instances)          # 实体级的计数由实体推出，不另填
    return ObjectClass(
        name=name, method=method, count=count, instances=instances,
        text=raw.get("text"), fields=dict(raw.get("fields") or {}),
        confidence=float(raw.get("confidence") or 0.0),
        verified_by=tuple(raw.get("verified_by") or ()),
        excluded=raw.get("excluded"), note=raw.get("note", ""),
    )


def parse_unit(raw: dict) -> GoldUnit:
    """dict → GoldUnit，格式不对就**明确报错**，不静默降级。"""
    if not raw.get("unit"):
        raise ValueError("缺少 unit 编号")
    classes = {name: _object_class(name, body)
               for name, body in (raw.get("classes") or {}).items()}
    return GoldUnit(unit=str(raw["unit"]),
                    source=dict(raw.get("source") or {}), classes=classes)
