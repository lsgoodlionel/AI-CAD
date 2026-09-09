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
class _GateRule:
    """非构件图层闸的一组判据（该图层画的不是建筑实体）。

    ``exempt`` 是**组内豁免**：命中它的图层，本组不再判它是非构件。
    两条实测：`钢筋混凝土墙` 之于 annotation 组的「钢筋」（那是材料名，
    GB/T 50083），`I—装饰—填充01(线状)` 之于 finish 组的「装饰」
    （填充截面就是构件本体，装修 60 张实测这一豁免保住 22.4 万图元）。

    ``match`` 是「子串（转义）+ 正则」合成的**一条**联合正则，加载时编译一次 ——
    `is_non_component_layer` 在 `_find_parallel_pairs` 里逐线调用，
    实测单图 line_layers 近 2 万条，逐词扫集合是热路径上的白烧。
    """
    group: str
    substrings: tuple[str, ...] = ()
    patterns: tuple[re.Pattern[str], ...] = ()
    exempt: tuple[re.Pattern[str], ...] = ()
    match: re.Pattern[str] | None = None

    def hits(self, text: str) -> bool:
        if self.match is None or not text:
            return False
        if any(pattern.search(text) for pattern in self.exempt):
            return False
        return bool(self.match.search(text))


def _union_regex(substrings: tuple[str, ...],
                 patterns: tuple[re.Pattern[str], ...]) -> re.Pattern[str] | None:
    """子串（转义）+ 已编译正则 → 一条大小写不敏感的联合正则。"""
    parts = [re.escape(sub) for sub in substrings if sub]
    parts += [f"(?:{pattern.pattern})" for pattern in patterns]
    return re.compile("|".join(parts), re.IGNORECASE) if parts else None


@dataclass(frozen=True)
class LayerConventions:
    """已解析、可直接匹配的图层约定集合。"""
    kind_rules: tuple[_KindRule, ...] = ()
    system_rules: tuple[_SystemRule, ...] = ()
    #: 非构件闸分组（group → 判据），**yaml ∪ 兜底、加载时合并一次**。
    #: `is_non_component_layer` 取各组的并集，`is_annotation_layer` 只问
    #: `annotation` 一组 —— 分组开关由这个字典提供，不必把语义拆进函数名。
    #: 默认值就是兜底词表 —— 于是 `LayerConventions()` 这个**降级返回值**
    #: 天然带着闸，不必在降级分支里再记得补一遍。
    gate_groups: dict[str, _GateRule] = field(
        default_factory=lambda: _default_gate_groups())


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


def _build_gate_rule(entry: dict) -> _GateRule | None:
    """构造一组非构件闸判据。分组名缺失即整条丢弃（宁可不拦，不可乱拦）。"""
    group = str(entry.get("group") or "").strip()
    if not group:
        return None
    return _gate_rule(
        group,
        _str_list(entry.get("substrings")),
        _compile_patterns(entry.get("patterns")),
        _compile_patterns(entry.get("exempt")),
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
    yaml_groups = {
        rule.group: rule
        for entry in data.get("non_component", []) or []
        if isinstance(entry, dict) and (rule := _build_gate_rule(entry)) is not None
    }
    # **yaml 只能加词，挖不掉地板**：与兜底逐组取并集，不是覆盖。
    return LayerConventions(
        kind_rules=tuple(sorted(kind_rules, key=_order_key)),
        system_rules=system_rules,
        gate_groups=_merge_gate_groups(yaml_groups),
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


#: AutoCAD **xref 绑定**产生的前缀分隔符：外部参照被绑定进图纸时，
#: 其图层名会变成 `原图名$N$N原图层名`（N 为绑定序号）。
#: 实测轨道交通图纸大量如此：`PLAN_F01$0$0S-BEAM-I`。
#: 不剥离的话，AIA 代码不在开头，所有 prefixes 匹配落空 ——
#: 实测该图 4240 个梁图元**零命中**。这是 AutoCAD 的通用约定，非工程特有。
_XREF_BOUND_RE = re.compile(r"^.*\$\d+\$\d*")

#: **外部参照（未绑定）图层**：AutoCAD 用 `外参名|图层名` 命名，
#: `|` 是保留字符（手工建的图层不能含它），所以剥到最后一个 `|` 是安全的。
#: 既有实现只处理了 `$N$` 的**绑定**形式，于是实测装修/景观图里
#: `建筑底板|S-COLU` 被前缀里的「底板」判成 slab、
#: `建筑底板|A-DOOR` 同样判成 slab（真实身份是门）。
_XREF_ATTACHED_RE = re.compile(r"^.*\|")


def is_annotation_layer(layer: str | None) -> bool:
    """该图层画的是**标注**而非构件本身 —— 非构件闸的 `annotation` 一组。

    **与 `classify_by_layer` 是两个问题**：后者答「这个图层属于哪个构件
    类别」（`S-BEAM-TEXT` 属于梁），本函数答「这条线是不是构件几何」
    （梁的文字标注不是梁）。我第一版把两者混为一谈，
    直接让 `classify_by_layer` 对标注层返回 None，打断了 4 个既有断言。

    **识别器里要拦构件产出的地方一律用 `is_non_component_layer`**（并集）；
    本函数留给只关心「标注」这一层语义的地方 —— 分组度量、
    以及需要把标注与图框分开统计的抽样脚本。

    词表在 yaml 的 `non_component.annotation`（此前是本文件的硬编码正则）；
    「钢筋混凝土」由该组的 `exempt` 摘掉 —— 那是材料名，不是钢筋图层。
    """
    return is_gate_group_hit(layer, "annotation")


#: 非构件闸兜底词表 —— **yaml 丢了闸也必须还在**。
#:
#: `load_conventions()` 对「文件缺失 / 无 pyyaml / 内容损坏 / 根节点非映射」
#: 一律降级为空（四条降级用例）。这对构件映射是安全的：降级只是**少认几个
#: 构件**。但对闸是**反向**的：降级会**放行假构件**。代价方向相反，
#: 所以闸不能只活在配置里。
#:
#: 与 `data/layer_conventions.yaml` 的 `non_component` 段是同一份词表，
#: 运行时取**并集**（yaml 只能加词，不能把地板挖掉），
#: 一致性由 `test_兜底词表与yaml不漂移` 钉住。
#: HEAD 那次实测的教训正是两份配置互相分岔：「灯具」在数据集那份出现 4 次、
#: 生产这份 0 次，整套装修词汇到不了生产路径。
_DEFAULT_GATE_VOCAB: dict[str, dict[str, tuple[str, ...]]] = {
    # 图框 / 标题块 / 会签栏：GB/T 50001 通用制图用语 + AIA 次级码
    # SHET(sheet) / TTLB·TBLK(title block)。
    # **判据落在次级码上，绝不能落在裸 `C-`** —— 那是 AIA 的 Civil 学科码。
    "sheet": {
        "substrings": ("图框", "标题块", "会签栏", "图签"),
        "patterns": (r'(?:^|[-_])(?:SHET|TTLB|TBLK)(?:[-_]|$)',),
    },
    # 标注 / 文字 / 钢筋 —— **此前是本文件里的硬编码正则 `_ANNOTATION_LAYER_RE`**，
    # 挪进词表后与 yaml 同源。`is_annotation_layer()` 只问这一组。
    #
    # 实测出处：「1区立柱桩及钢立柱平面布置图」贡献 3410 根「柱」，全部来自
    # 图层名「立柱桩标注」——画的是引线与文字；一张墙配筋图的 `墙柱纵筋`
    # 因含「柱」造出 711 根假柱。中文的「标注/文字/标高/说明/编号/尺寸」与
    # AIA 的 -TEXT/-DIMS/-NOTE/-IDEN/-ANNO 都是通用制图用语，非某工程命名。
    # 正则前后都留边界：裸图层名 `TEXT` 也要命中（实测造出 21 根假柱），
    # 收尾用 (?:-|$) 保证 `TEXTURE` 不被误伤。
    "annotation": {
        "substrings": ("标注", "文字", "说明", "编号", "尺寸标", "标高标", "注释",
                       "图例", "纵筋", "箍筋", "配筋", "钢筋", "拉筋", "分布筋"),
        "patterns": (r"(?:^|-)(?:TEXT|DIMS?|NOTE|IDEN|ANNO|TAG|LABEL|REBAR|REIN)(?:-|$)",),
        # 「钢筋混凝土」是材料名（GB/T 50083），不是钢筋图层。
        "exempt": (r"钢筋(?:混凝土|砼)",),
    },
    # 装修饰面 / 图案 —— 被 wall 的通用子串「墙」判成结构墙的那批
    # （`I—平面—墙面材料` / `I—平面—外墙装饰面层线` / `I—隔墙—地面阴影`）。
    "finish": {
        "substrings": ("饰面", "装饰", "面层", "材料", "阴影", "图案", "铺装",
                       "拼花", "分格", "石材"),
        "patterns": (r"(?:^|-)(?:FINH|PATT)(?:-|$)",),
        # **填充不是饰面**：填充截面就是构件本体，清掉会造成大面积构件消失。
        "exempt": (r"填充", r"HATCH"),
    },
}

def _gate_rule(group: str, substrings: tuple[str, ...],
               patterns: tuple[re.Pattern[str], ...],
               exempt: tuple[re.Pattern[str], ...]) -> _GateRule:
    """把一组词汇装配成规则（联合正则在此编译一次）。"""
    return _GateRule(group=group, substrings=substrings, patterns=patterns,
                     exempt=exempt, match=_union_regex(substrings, patterns))


def _default_gate_groups() -> dict[str, _GateRule]:
    """由 .py 内置词表构造的闸（yaml 不可用时的地板）。"""
    return {
        group: _gate_rule(
            group,
            tuple(_norm(sub) for sub in vocab.get("substrings", ()) if sub),
            tuple(re.compile(p, re.IGNORECASE) for p in vocab.get("patterns", ())),
            tuple(re.compile(p, re.IGNORECASE) for p in vocab.get("exempt", ())),
        )
        for group, vocab in _DEFAULT_GATE_VOCAB.items()
    }


def _merge_gate_groups(yaml_groups: dict[str, _GateRule]) -> dict[str, _GateRule]:
    """yaml ∪ 兜底，**逐组合并**（yaml 只能加词，挖不掉地板）。

    yaml 里独有的组原样收下；两边都有的组，词汇/正则/豁免各取并集。
    """
    merged = _default_gate_groups()
    for group, rule in yaml_groups.items():
        floor = merged.get(group)
        if floor is None:
            merged[group] = rule
            continue
        substrings = tuple(dict.fromkeys(floor.substrings + rule.substrings))
        patterns = floor.patterns + tuple(
            p for p in rule.patterns
            if p.pattern not in {q.pattern for q in floor.patterns})
        exempt = floor.exempt + tuple(
            p for p in rule.exempt
            if p.pattern not in {q.pattern for q in floor.exempt})
        merged[group] = _gate_rule(group, substrings, patterns, exempt)
    return merged


def is_non_component_layer(layer: str | None) -> bool:
    """该图层画的**不是建筑实体**，任何构件都不该从它产出 —— 各组的**并集**。

    三组，语义各不相同，但对识别器是同一个答案「别从这儿产出构件」：
      - ``sheet``      图框/标题块/会签栏：画的是「纸」不是「楼」
      - ``annotation`` 标注/文字/钢筋：依附于某个构件，但画的不是构件几何
      - ``finish``     装修饰面/图案：贴在构件表面的材料表达，不是构件本体

    **分组开关由 `conv.gate_groups` 提供**，不必把语义拆进函数名 ——
    `is_annotation_layer()` 就是「只问 annotation 一组」的那个入口。

    **不能改成让 `classify_by_layer` 返回 None**：识别器里柱与设备的判据是
    `_kind is not None and _kind != "equipment"`，None 是**放行**，
    图元会掉进「按尺寸猜」的路径，比判错类型更糟。

    **只看图层自己的名字**：`|` / `$N$` 前是**来源图纸**的名字而非图层语义 ——
    实测前缀里的「配筋」曾让某图 658 个柱候选全被丢弃。
    故 `图框A2$0$S-COLU`、`装饰施工图$0$S-WALL` 都判为 False，那是真构件层。
    """
    text = _norm(normalize_layer_name(layer))
    if not text:
        return False
    return any(rule.hits(text) for rule in load_conventions().gate_groups.values())


def is_gate_group_hit(layer: str | None, group: str) -> bool:
    """只问某一组闸（分组开关的入口；`is_annotation_layer` 即其特例）。"""
    text = _norm(normalize_layer_name(layer))
    if not text:
        return False
    rule = load_conventions().gate_groups.get(group)
    return rule.hits(text) if rule is not None else False


def normalize_layer_name(layer: str | None) -> str:
    """剥离 AutoCAD xref 绑定前缀，得到原始图层名。无前缀时原样返回。"""
    name = _XREF_ATTACHED_RE.sub("", str(layer or ""))
    return _XREF_BOUND_RE.sub("", name)


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

    # **完整名优先于剥离名**：xref 前缀里的 AIA 代码是设计师在原图里的
    # 标注，比子层名可靠。实测 `S-S-WALL-1F$0$墙柱纵筋` 剥离后只剩
    # 「墙柱纵筋」，含「柱」被判成柱 —— 而主层名 S-WALL 明说是墙。
    full_n = _norm(_XREF_ATTACHED_RE.sub("", str(layer or "")))
    layer_n = _norm(normalize_layer_name(layer))
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

    # 3. 图层前缀 —— 先试完整名（含 xref 前缀的 AIA 代码），再试剥离名
    if full_n != layer_n:
        kind = _match_prefix(full_n, conv.kind_rules)
        if kind is not None:
            return kind
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

    layer_n = _norm(normalize_layer_name(layer))
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
