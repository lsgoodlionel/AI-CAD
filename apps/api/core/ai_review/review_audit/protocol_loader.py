"""会审协议知识资产加载器。

从 ``apps/api/data/review_protocol/*.yaml`` 加载知识资产：
- disciplines.yaml          —— 19 专业全量（concern/接口/风险触发/对象）
- question_templates.yaml    —— 问题模板 + 对象级模板
- concern_keywords.yaml      —— concern → 触发词
- location_patterns.yaml     —— 定位抽取正则
- scenario_templates.yaml    —— V2 场景模板（每专业 × 主对象 × 场景）
- question_pack_templates.yaml —— V2 问题包模板（主问题/补充问题/证据缺口）
- document_templates.yaml    —— V2 文书口径模板（纪要口径/答复口径）
- review_checklists.yaml     —— V3 SOP 逐项清单（审图目标/后果链/逐项清单/闭环规则）
- review_methodology.yaml    —— V4 方法论资产（五维审查/控制链/动作类型/责任方/闭环要素）

设计约束：
- 全部以 lru_cache 缓存，避免重复 IO/解析。
- 无 pyyaml 或文件缺失时优雅降级（返回空结构 + warning，绝不抛异常）。
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

try:
    import yaml

    _HAS_YAML = True
except ImportError:  # pragma: no cover - 环境缺依赖时降级
    _HAS_YAML = False

logger = logging.getLogger(__name__)

_PROTOCOL_DIR = Path(__file__).parents[3] / "data" / "review_protocol"

_DISCIPLINES_FILE = "disciplines.yaml"
_TEMPLATES_FILE = "question_templates.yaml"
_CONCERN_KEYWORDS_FILE = "concern_keywords.yaml"
_LOCATION_PATTERNS_FILE = "location_patterns.yaml"
_SCENARIO_TEMPLATES_FILE = "scenario_templates.yaml"
_QUESTION_PACK_TEMPLATES_FILE = "question_pack_templates.yaml"
_DOCUMENT_TEMPLATES_FILE = "document_templates.yaml"
_REVIEW_CHECKLISTS_FILE = "review_checklists.yaml"
_REVIEW_METHODOLOGY_FILE = "review_methodology.yaml"


def _load_yaml(filename: str) -> dict:
    """读取单个 yaml 文件，任何失败都返回空 dict 并记录日志。"""
    if not _HAS_YAML:
        logger.warning("pyyaml 未安装，跳过会审协议加载: %s", filename)
        return {}

    path = _PROTOCOL_DIR / filename
    if not path.exists():
        logger.warning("会审协议文件缺失（降级为空）: %s", path)
        return {}

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 防御性：任何解析异常都降级
        logger.error("加载会审协议文件 %s 失败: %s", path, exc)
        return {}

    if not isinstance(data, dict):
        logger.warning("会审协议文件 %s 顶层非 dict，降级为空", path)
        return {}
    return data


@lru_cache(maxsize=1)
def load_disciplines() -> dict[str, dict]:
    """返回 ``{discipline_code: discipline_dict}``，缺失时返回 ``{}``。"""
    raw = _load_yaml(_DISCIPLINES_FILE)
    items = raw.get("disciplines", []) if isinstance(raw, dict) else []
    result: dict[str, dict] = {}
    for item in items:
        if isinstance(item, dict) and item.get("code"):
            result[str(item["code"])] = item
    return result


@lru_cache(maxsize=1)
def load_templates() -> dict[str, dict]:
    """返回 ``{discipline_code: {problem:[...], object:{...}}}``，缺失时返回 ``{}``。"""
    raw = _load_yaml(_TEMPLATES_FILE)
    templates = raw.get("templates", {}) if isinstance(raw, dict) else {}
    return templates if isinstance(templates, dict) else {}


@lru_cache(maxsize=1)
def load_concern_keywords() -> dict[str, list[str]]:
    """返回 ``{concern_label: [触发词, ...]}``，缺失时返回 ``{}``。"""
    raw = _load_yaml(_CONCERN_KEYWORDS_FILE)
    concerns = raw.get("concerns", {}) if isinstance(raw, dict) else {}
    if not isinstance(concerns, dict):
        return {}
    return {
        str(label): [str(w) for w in (words or [])]
        for label, words in concerns.items()
    }


@lru_cache(maxsize=1)
def load_location_patterns() -> dict[str, list[str]]:
    """返回 ``{location_kind: [正则字符串, ...]}``，缺失时返回 ``{}``。"""
    raw = _load_yaml(_LOCATION_PATTERNS_FILE)
    patterns = raw.get("patterns", {}) if isinstance(raw, dict) else {}
    if not isinstance(patterns, dict):
        return {}
    return {
        str(kind): [str(p) for p in (pats or [])]
        for kind, pats in patterns.items()
    }


@lru_cache(maxsize=1)
def load_scenario_templates() -> dict[str, list[dict]]:
    """加载场景模板（V2）。

    返回 ``{discipline_code: [{object, level, 正常审图, 图间冲突, ...}, ...]}``。
    A 的 yaml 尚未就绪 / 无 pyyaml 时降级返回 ``{}``。
    """
    raw = _load_yaml(_SCENARIO_TEMPLATES_FILE)
    scenarios = raw.get("scenarios", {}) if isinstance(raw, dict) else {}
    if not isinstance(scenarios, dict):
        return {}
    result: dict[str, list[dict]] = {}
    for code, entries in scenarios.items():
        if not isinstance(entries, list):
            continue
        result[str(code)] = [e for e in entries if isinstance(e, dict)]
    return result


@lru_cache(maxsize=1)
def load_question_pack_templates() -> dict[str, dict]:
    """加载问题包模板（V2）。

    返回 ``{discipline_code: {主问题, 补充问题, 证据缺口}}``，缺失时返回 ``{}``。
    """
    raw = _load_yaml(_QUESTION_PACK_TEMPLATES_FILE)
    packs = raw.get("packs", {}) if isinstance(raw, dict) else {}
    if not isinstance(packs, dict):
        return {}
    return {str(code): pack for code, pack in packs.items() if isinstance(pack, dict)}


@lru_cache(maxsize=1)
def load_document_templates() -> dict[str, dict]:
    """加载文书口径模板（V2）。

    返回 ``{discipline_code: {纪要口径:[{type,text}], 答复口径:[{type,text}]}}``，
    缺失时返回 ``{}``。
    """
    raw = _load_yaml(_DOCUMENT_TEMPLATES_FILE)
    documents = raw.get("documents", {}) if isinstance(raw, dict) else {}
    if not isinstance(documents, dict):
        return {}
    return {str(code): doc for code, doc in documents.items() if isinstance(doc, dict)}


@lru_cache(maxsize=1)
def load_review_checklists() -> dict[str, dict]:
    """加载 SOP 逐项清单（V3，蒸馏自 05_专业审图清单SOP）。

    返回 ``{discipline_code: {name, protected_result, consequence_chain,
    checklist:[{检查项,判断依据,核查方法,常见冲突,必问问题,输出口径,升级}],
    closure_rules}}``，缺失/无 pyyaml 时降级返回 ``{}``。
    """
    raw = _load_yaml(_REVIEW_CHECKLISTS_FILE)
    checklists = raw.get("checklists", {}) if isinstance(raw, dict) else {}
    if not isinstance(checklists, dict):
        return {}
    return {str(code): cl for code, cl in checklists.items() if isinstance(cl, dict)}


@lru_cache(maxsize=1)
def load_review_methodology() -> dict:
    """加载方法论资产（V4，蒸馏自 06 方法论与AI原则）。

    返回 ``{control_chain_order, dimensions, priority_objects, action_types,
    action_outputs, action_dictionary, responsible_parties,
    closure_elements, closure_followups}``；缺失/无 pyyaml 时各 key 降级为空结构。
    """
    raw = _load_yaml(_REVIEW_METHODOLOGY_FILE)
    return {
        "control_chain_order": [str(s) for s in raw.get("control_chain_order", []) or []],
        "dimensions": [d for d in raw.get("dimensions", []) or [] if isinstance(d, dict)],
        "priority_objects": [
            o for o in raw.get("priority_objects", []) or [] if isinstance(o, dict)
        ],
        "action_types": {
            str(k): [str(w) for w in (v or [])]
            for k, v in (raw.get("action_types", {}) or {}).items()
        },
        "action_outputs": {
            str(k): str(v) for k, v in (raw.get("action_outputs", {}) or {}).items()
        },
        "action_dictionary": [
            a for a in raw.get("action_dictionary", []) or [] if isinstance(a, dict)
        ],
        "responsible_parties": [
            p for p in raw.get("responsible_parties", []) or [] if isinstance(p, dict)
        ],
        "closure_elements": {
            str(k): [str(w) for w in (v or [])]
            for k, v in (raw.get("closure_elements", {}) or {}).items()
        },
        "closure_followups": {
            str(k): str(v) for k, v in (raw.get("closure_followups", {}) or {}).items()
        },
    }
