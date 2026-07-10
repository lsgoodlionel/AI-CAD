"""C-04 自动标注引擎：CAD 图层/块属性 → 符号类别弱标签。

借鉴 ArchCAD-400K「用 CAD 内在图层/块属性自动标注」思路，把 C-03 展开后的
``PrimitiveDoc`` 逐图元打**弱标签**（噪声可接受，目标是数据冷启动；人工在 C-06 精修）。

判据链（主判据必须复用基础分类器，绝不重造）：
    1. 基础分类器 ``layer_conventions.classify_by_layer`` / ``classify_system``（主判据）。
    2. 未命中时回退补充映射表 ``LayerClassMap``（``data/model3d/layer_class_map.yaml``）。

置信度分级（诚实反映噪声，供 C-06 精修排序）：
    - 精确别名命中           → 0.9（高）   label_source = "layer"/"block"
    - 图层前缀/子串/正则命中 → 0.7（中）   label_source = "layer"
    - 块名前缀/子串/正则命中 → 0.6（低）   label_source = "block"
    - 补充映射表命中         → 0.6（低）   label_source = "layer_class_map"
    - 无法判定               → None        label_source = "none"

共享 taxonomy（强制对齐，勿自造）：
    构件类别 ∈ {column, beam, slab, wall, door, window, pipe, equipment, axis} 或 None
    机电系统 ∈ {消防, 给排水, 电气, 暖通} 或 None

设计约束：frozen dataclass、完整类型注解、不可变；任何失败优雅降级（记 log 返回空/None），
绝不跨边界抛异常（对齐 element_recognizer / layer_conventions 风格）。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

from ..layer_conventions import classify_by_layer, classify_system, load_conventions
from ..preprocess.schema import Primitive, PrimitiveDoc

try:
    import yaml

    _HAS_YAML = True
except ImportError:  # pragma: no cover - 环境缺依赖时降级
    _HAS_YAML = False

logger = logging.getLogger(__name__)

# ── 共享 taxonomy 锚点（勿新增顶层类别）───────────────────────────
VALID_CATEGORIES: frozenset[str] = frozenset(
    {"column", "beam", "slab", "wall", "door", "window", "pipe", "equipment", "axis"}
)
VALID_SYSTEMS: frozenset[str] = frozenset({"消防", "给排水", "电气", "暖通"})

# ── 置信度分级常量 ───────────────────────────────────────────────
CONF_ALIAS = 0.9   # 精确别名（高）
CONF_LAYER = 0.7   # 图层前缀/子串/正则（中）
CONF_BLOCK = 0.6   # 块名前缀/子串/正则（低）
CONF_MAP = 0.6     # 补充映射表命中（低）

LabelSource = Literal["layer", "block", "layer_class_map", "none"]

# auto_label.py 位于 core/model3d/dataset/ → parents[3] 为 apps/api 根。
_MAP_FILE = Path(__file__).parents[3] / "data" / "model3d" / "layer_class_map.yaml"


# ─────────────────────────────────────────────────────────────────
# 输出契约
# ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class LabeledPrimitive:
    """单个图元的弱标注结果（不可变）。

    - ``primitive_id`` / ``primitive_type``：溯源到 ``Primitive.id`` / ``.type``。
    - ``category``：构件弱标签（VALID_CATEGORIES 之一）或 None。
    - ``mep_system``：机电系统（VALID_SYSTEMS 之一）或 None（与 category 正交）。
    - ``confidence``：置信度 0.0–1.0；无法判定类别时为 None。
    - ``label_source``：类别标签来源，用于质量报告分层统计。
    """
    primitive_id: int
    primitive_type: str
    category: str | None
    mep_system: str | None
    confidence: float | None
    label_source: LabelSource


@dataclass(frozen=True)
class AutoLabelResult:
    """自动标注引擎产物：逐图元弱标签 + 弱标注质量报告。"""
    labeled: tuple[LabeledPrimitive, ...]
    report: dict


# ─────────────────────────────────────────────────────────────────
# 补充映射表（YAML，可维护；纯函数加载、正则预编译、无效条目跳过不抛）
# ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class _MapRule:
    """补充映射的单条类别/系统规则（全部大写归一，正则已预编译）。"""
    key: str  # 类别（category）或系统（system）名
    aliases: frozenset[str] = frozenset()
    prefixes: tuple[str, ...] = ()
    substrings: tuple[str, ...] = ()
    patterns: tuple[re.Pattern[str], ...] = ()


@dataclass(frozen=True)
class LayerClassMap:
    """已解析的补充映射表：类别规则 + 机电系统规则，均可直接匹配。"""
    category_rules: tuple[_MapRule, ...] = ()
    system_rules: tuple[_MapRule, ...] = ()

    def classify(self, layer: str | None, block: str = "") -> str | None:
        """图层/块 → 构件类别（VALID_CATEGORIES 之一）或 None。"""
        return _match_rules(_norm(layer), _norm(block), self.category_rules)

    def classify_system(self, layer: str | None, block: str = "") -> str | None:
        """图层/块 → 机电系统（VALID_SYSTEMS 之一）或 None。"""
        return _match_rules(_norm(layer), _norm(block), self.system_rules)


def _norm(text: str | None) -> str:
    """归一化：去空白 + 转大写（中文不受影响，英文大小写不敏感）。"""
    return (text or "").strip().upper()


def _compile_patterns(raw: object) -> tuple[re.Pattern[str], ...]:
    compiled: list[re.Pattern[str]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, str):
            continue
        try:
            compiled.append(re.compile(item, re.IGNORECASE))
        except re.error as exc:  # 单条无效正则跳过，不影响其余
            logger.warning("[auto_label] 无效正则已跳过 %r: %s", item, exc)
    return tuple(compiled)


def _str_list(raw: object) -> tuple[str, ...]:
    return tuple(_norm(x) for x in raw if isinstance(x, str)) if isinstance(raw, list) else ()


def _build_map_rule(entry: object, key_field: str, valid: frozenset[str]) -> _MapRule | None:
    if not isinstance(entry, dict):
        return None
    key = entry.get(key_field)
    if not isinstance(key, str) or key not in valid:
        return None  # 非法/越界的 taxonomy 条目静默跳过
    return _MapRule(
        key=key,
        aliases=frozenset(_str_list(entry.get("aliases"))),
        prefixes=_str_list(entry.get("prefixes")),
        substrings=_str_list(entry.get("substrings")),
        patterns=_compile_patterns(entry.get("patterns")),
    )


def _rule_hit(name: str, rule: _MapRule) -> bool:
    """name 是否命中该规则（别名/前缀/子串/正则任一）。"""
    if not name:
        return False
    if name in rule.aliases:
        return True
    if any(p and name.startswith(p) for p in rule.prefixes):
        return True
    if any(s and s in name for s in rule.substrings):
        return True
    return any(pat.search(name) for pat in rule.patterns)


def _match_rules(layer_n: str, block_n: str, rules: tuple[_MapRule, ...]) -> str | None:
    """先按图层名再按块名逐规则匹配，首个命中即返回其 key。"""
    for name in (layer_n, block_n):
        if not name:
            continue
        for rule in rules:
            if _rule_hit(name, rule):
                return rule.key
    return None


def load_layer_class_map(path: Path | None = None) -> LayerClassMap:
    """加载补充映射表（纯函数）。任何失败降级为空映射，绝不抛异常。"""
    target = path or _MAP_FILE
    if not _HAS_YAML:
        logger.warning("[auto_label] pyyaml 未安装，补充映射降级为空")
        return LayerClassMap()
    if not target.exists():
        logger.warning("[auto_label] 补充映射缺失（降级为空）: %s", target)
        return LayerClassMap()
    try:
        data = yaml.safe_load(target.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 任何解析异常都降级
        logger.error("[auto_label] 补充映射解析失败（降级为空）: %s", exc)
        return LayerClassMap()
    if not isinstance(data, dict):
        logger.warning("[auto_label] 补充映射根节点非映射，降级为空")
        return LayerClassMap()

    category_rules = tuple(
        rule
        for entry in data.get("conventions", []) or []
        if (rule := _build_map_rule(entry, "kind", VALID_CATEGORIES)) is not None
    )
    system_rules = tuple(
        rule
        for entry in data.get("systems", []) or []
        if (rule := _build_map_rule(entry, "system", VALID_SYSTEMS)) is not None
    )
    return LayerClassMap(category_rules=category_rules, system_rules=system_rules)


@lru_cache(maxsize=1)
def _default_map() -> LayerClassMap:
    """默认补充映射（缓存，避免每次标注重复 IO/解析）。"""
    return load_layer_class_map()


# ─────────────────────────────────────────────────────────────────
# 置信度分级：类别由基础分类器判定，本函数仅为其定位来源/分层
# ─────────────────────────────────────────────────────────────────
def _grade_base_match(layer_n: str, block_n: str, category: str) -> tuple[LabelSource, float]:
    """基础分类器命中后，定位标签来源与置信度分层（近似，用于分桶）。

    优先级对齐 layer_conventions：精确别名 > 块名模糊 > 图层前缀/子串/正则。
    找不到对应规则时保守回退为图层中置信度。
    """
    conv = load_conventions()
    rule = next((r for r in conv.kind_rules if r.kind == category), None)
    if rule is None:
        return "layer", CONF_LAYER
    if layer_n and layer_n in rule.aliases:
        return "layer", CONF_ALIAS
    if block_n and block_n in rule.aliases:
        return "block", CONF_ALIAS
    if block_n and _conv_fuzzy_hit(block_n, rule):
        return "block", CONF_BLOCK
    if layer_n and _conv_fuzzy_hit(layer_n, rule):
        return "layer", CONF_LAYER
    return "layer", CONF_LAYER


def _conv_fuzzy_hit(name: str, rule) -> bool:
    """基础约定规则的模糊命中（前缀/子串/正则），别名不计入。"""
    if any(p and name.startswith(p) for p in rule.prefixes):
        return True
    if any(s and s in name for s in rule.substrings):
        return True
    return any(pat.search(name) for pat in rule.patterns)


# ─────────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────────
def _label_one(prim: Primitive, extra_map: LayerClassMap) -> LabeledPrimitive:
    """单图元弱标注（异常自愈为 None 标签，绝不抛出）。"""
    try:
        layer = prim.layer or ""
        block = prim.block or ""
        layer_n = _norm(layer)
        block_n = _norm(block)

        # 1) 主判据：基础分类器
        category = classify_by_layer(layer, block)
        source: LabelSource = "none"
        confidence: float | None = None
        if category in VALID_CATEGORIES:
            source, confidence = _grade_base_match(layer_n, block_n, category)  # type: ignore[arg-type]
        else:
            category = None
            # 2) 回退：补充映射表
            mapped = extra_map.classify(layer, block)
            if mapped in VALID_CATEGORIES:
                category = mapped
                source, confidence = "layer_class_map", CONF_MAP

        # 机电系统（与 category 正交）：基础分类器优先，未命中回退补充映射
        system = classify_system(layer, block)
        if system not in VALID_SYSTEMS:
            system = extra_map.classify_system(layer, block)
        if system not in VALID_SYSTEMS:
            system = None

        return LabeledPrimitive(
            primitive_id=prim.id,
            primitive_type=prim.type,
            category=category,
            mep_system=system,
            confidence=confidence,
            label_source=source,
        )
    except Exception as exc:  # noqa: BLE001 - 单图元失败降级为无标签
        logger.warning("[auto_label] 图元 %s 标注失败，降级为 None: %s",
                       getattr(prim, "id", "?"), exc)
        return LabeledPrimitive(
            primitive_id=getattr(prim, "id", -1),
            primitive_type=getattr(prim, "type", ""),
            category=None,
            mep_system=None,
            confidence=None,
            label_source="none",
        )


def auto_label(
    doc: PrimitiveDoc, *, extra_map: LayerClassMap | None = None
) -> AutoLabelResult:
    """对整张图纸的图元文档逐图元打弱标签。

    - ``doc``：C-03 ``expand_blocks`` 产出的图元文档（含 layer + block 溯源字段）。
    - ``extra_map``：可选补充映射表；缺省用 ``data/model3d/layer_class_map.yaml``。

    返回 ``AutoLabelResult``（弱标签元组 + 质量报告）。任何异常优雅降级为空结果，
    绝不抛异常，保证批处理不因单图中断。
    """
    effective_map = extra_map if extra_map is not None else _default_map()
    try:
        primitives = doc.primitives if doc is not None else ()
        labeled = tuple(_label_one(p, effective_map) for p in primitives)
    except Exception as exc:  # noqa: BLE001 - 整图失败降级为空
        logger.warning("[auto_label] 整图标注失败，降级为空: %s", exc)
        labeled = ()
    return AutoLabelResult(labeled=labeled, report=weak_label_report(labeled))


def weak_label_report(labeled: tuple[LabeledPrimitive, ...]) -> dict:
    """弱标注质量报告：覆盖率、分类别计数、未标注比例、分来源计数。"""
    total = len(labeled)
    by_category: dict[str, int] = {c: 0 for c in sorted(VALID_CATEGORIES)}
    by_system: dict[str, int] = {s: 0 for s in sorted(VALID_SYSTEMS)}
    by_source: dict[str, int] = {"layer": 0, "block": 0, "layer_class_map": 0, "none": 0}

    labeled_count = 0
    for item in labeled:
        by_source[item.label_source] = by_source.get(item.label_source, 0) + 1
        if item.category is not None:
            labeled_count += 1
            by_category[item.category] = by_category.get(item.category, 0) + 1
        if item.mep_system is not None:
            by_system[item.mep_system] = by_system.get(item.mep_system, 0) + 1

    unlabeled = total - labeled_count
    return {
        "total": total,
        "labeled": labeled_count,
        "unlabeled": unlabeled,
        "coverage": (labeled_count / total) if total else 0.0,
        "unlabeled_ratio": (unlabeled / total) if total else 0.0,
        "by_category": by_category,
        "by_system": by_system,
        "by_source": by_source,
    }
