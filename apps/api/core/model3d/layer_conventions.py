"""图层约定加载器 + 分类器。

把中国施工图的图层名 / 块名约定（data/layer_conventions.yaml）固化为
「图层 → 构件类型」映射，供构件识别（element_recognizer，A-16）用作强先验。

匹配优先级（由高到低）——见 ``classify_by_layer`` 文档：
    1. 精确别名（layer 或 block 整串相等）
    2. 块名模糊匹配（block 的 前缀 / 子串 / 正则）
    3. 图层前缀（layer 的 prefixes）
    4. 图层子串 / 正则（layer 的 substrings / patterns）

设计约束：
- 全部大小写不敏感（中文原样保留）。
- lru_cache 缓存加载结果，避免重复 IO / 解析。
- pyyaml 缺失 / 文件缺失 / 解析失败均优雅降级为空映射，绝不抛异常。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

try:
    import yaml

    _HAS_YAML = True
except ImportError:  # pragma: no cover - 环境缺依赖时降级
    _HAS_YAML = False

logger = logging.getLogger(__name__)

_CONVENTIONS_FILE = Path(__file__).parents[2] / "data" / "layer_conventions.yaml"

# 构件类型固定优先级（防止一个字符串命中多个类型时结果不确定）。
_KIND_ORDER = (
    "column", "beam", "slab", "wall", "door", "window", "pipe", "equipment", "axis",
)


@dataclass(frozen=True)
class _KindRule:
    """单一构件类型的匹配规则（全部大写归一，正则已预编译）。"""
    kind: str
    aliases: frozenset[str] = field(default_factory=frozenset)
    prefixes: tuple[str, ...] = ()
    substrings: tuple[str, ...] = ()
    patterns: tuple[re.Pattern[str], ...] = ()


@dataclass(frozen=True)
class _SystemRule:
    """机电系统判定规则。"""
    system: str
    prefixes: tuple[str, ...] = ()
    substrings: tuple[str, ...] = ()


@dataclass(frozen=True)
class LayerConventions:
    """已解析、可直接匹配的图层约定集合。"""
    kind_rules: tuple[_KindRule, ...] = ()
    system_rules: tuple[_SystemRule, ...] = ()


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
        except re.error as exc:  # noqa: PERF203 - 单条正则失败不影响其余
            logger.warning("[layer_conventions] 无效正则已跳过 %r: %s", item, exc)
    return tuple(compiled)


def _str_list(raw: object) -> tuple[str, ...]:
    return tuple(_norm(x) for x in raw if isinstance(x, str)) if isinstance(raw, list) else ()


def _build_kind_rule(entry: dict) -> _KindRule | None:
    kind = entry.get("kind")
    if not isinstance(kind, str) or not kind:
        return None
    return _KindRule(
        kind=kind,
        aliases=frozenset(_str_list(entry.get("aliases"))),
        prefixes=_str_list(entry.get("prefixes")),
        substrings=_str_list(entry.get("substrings")),
        patterns=_compile_patterns(entry.get("patterns")),
    )


def _build_system_rule(entry: dict) -> _SystemRule | None:
    system = entry.get("system")
    if not isinstance(system, str) or not system:
        return None
    return _SystemRule(
        system=system,
        prefixes=_str_list(entry.get("prefixes")),
        substrings=_str_list(entry.get("substrings")),
    )


def _order_key(rule: _KindRule) -> int:
    try:
        return _KIND_ORDER.index(rule.kind)
    except ValueError:
        return len(_KIND_ORDER)


@lru_cache(maxsize=1)
def load_conventions() -> LayerConventions:
    """加载并解析图层约定（缓存）。任何失败均降级为空约定，绝不抛异常。"""
    if not _HAS_YAML:
        logger.warning("[layer_conventions] pyyaml 未安装，图层约定降级为空")
        return LayerConventions()
    if not _CONVENTIONS_FILE.exists():
        logger.warning("[layer_conventions] 配置缺失（降级为空）: %s", _CONVENTIONS_FILE)
        return LayerConventions()
    try:
        data = yaml.safe_load(_CONVENTIONS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 任何解析异常都降级
        logger.error("[layer_conventions] 解析失败（降级为空）: %s", exc)
        return LayerConventions()

    if not isinstance(data, dict):
        logger.warning("[layer_conventions] 配置根节点非映射，降级为空")
        return LayerConventions()

    kind_rules = tuple(
        rule
        for entry in data.get("conventions", []) or []
        if isinstance(entry, dict) and (rule := _build_kind_rule(entry)) is not None
    )
    system_rules = tuple(
        rule
        for entry in data.get("systems", []) or []
        if isinstance(entry, dict) and (rule := _build_system_rule(entry)) is not None
    )
    return LayerConventions(
        kind_rules=tuple(sorted(kind_rules, key=_order_key)),
        system_rules=system_rules,
    )


def _match_alias(name: str, rules: tuple[_KindRule, ...]) -> str | None:
    if not name:
        return None
    for rule in rules:
        if name in rule.aliases:
            return rule.kind
    return None


def _match_prefix(name: str, rules: tuple[_KindRule, ...]) -> str | None:
    """最长前缀优先（如 ``M-DUCT`` 胜过门的 ``M-``）；等长按构件优先级。"""
    if not name:
        return None
    best: tuple[int, int, str] | None = None  # (前缀长度, -顺序, kind)
    for order, rule in enumerate(rules):
        matched = [len(p) for p in rule.prefixes if p and name.startswith(p)]
        if not matched:
            continue
        candidate = (max(matched), -order, rule.kind)
        if best is None or candidate > best:
            best = candidate
    return best[2] if best is not None else None


def _match_substring_or_pattern(name: str, rules: tuple[_KindRule, ...]) -> str | None:
    if not name:
        return None
    for rule in rules:
        if any(sub and sub in name for sub in rule.substrings):
            return rule.kind
        if any(pattern.search(name) for pattern in rule.patterns):
            return rule.kind
    return None


def classify_by_layer(layer: str | None, block: str = "") -> str | None:
    """图层名（+ 可选块名）→ 构件类型；无法判定返回 ``None``。

    返回值为下列之一（与 element_recognizer 构件体系一致）或 ``None``：
        ``column`` / ``beam`` / ``slab`` / ``wall`` /
        ``door`` / ``window`` / ``pipe`` / ``equipment`` / ``axis``

    匹配优先级（命中即返回）：
        1. 精确别名：layer 整串命中 → block 整串命中
        2. 块名模糊：block 的 前缀 / 子串 / 正则（门窗多以块命名，故先于图层）
        3. 图层前缀：layer 的 prefixes
        4. 图层子串 / 正则：layer 的 substrings / patterns

    大小写不敏感；layer / block 均可为空（空输入安全返回，不抛异常）。
    """
    conv = load_conventions()
    if not conv.kind_rules:
        return None

    layer_n = _norm(layer)
    block_n = _norm(block)

    # 1. 精确别名（先 layer 后 block）
    for name in (layer_n, block_n):
        kind = _match_alias(name, conv.kind_rules)
        if kind is not None:
            return kind

    # 2. 块名模糊匹配（前缀 → 子串/正则）
    kind = _match_prefix(block_n, conv.kind_rules)
    if kind is not None:
        return kind
    kind = _match_substring_or_pattern(block_n, conv.kind_rules)
    if kind is not None:
        return kind

    # 3. 图层前缀
    kind = _match_prefix(layer_n, conv.kind_rules)
    if kind is not None:
        return kind

    # 4. 图层子串 / 正则
    return _match_substring_or_pattern(layer_n, conv.kind_rules)


def classify_system(layer: str | None, block: str = "") -> str | None:
    """图层名 / 块名 → 机电系统；无法判定返回 ``None``。

    返回值为下列之一（与 element_recognizer._SYSTEM_KEYWORDS 一致）或 ``None``：
        ``消防`` / ``给排水`` / ``电气`` / ``暖通``

    仅对机电构件（pipe / equipment）有意义。大小写不敏感、空输入安全。
    子串优先于前缀（前缀如 ``M-``/``P-``/``E-`` 较弱，子串语义更强）。
    """
    conv = load_conventions()
    if not conv.system_rules:
        return None

    layer_n = _norm(layer)
    block_n = _norm(block)

    for name in (layer_n, block_n):
        if not name:
            continue
        for rule in conv.system_rules:
            if any(sub and sub in name for sub in rule.substrings):
                return rule.system
    for name in (layer_n, block_n):
        if not name:
            continue
        for rule in conv.system_rules:
            if any(prefix and name.startswith(prefix) for prefix in rule.prefixes):
                return rule.system
    return None
