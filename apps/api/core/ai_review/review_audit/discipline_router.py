"""专业路由：把输入归到 19 细分专业之一。

优先级（固定执行协议第1步）：
1. 显式 discipline 是细分代码（ZH/JG/...）           → 直接采用，basis=显式
2. 显式 discipline 是现有粗专业（structure/...）      → 取该 coarse 下首选细分，basis=显式（粗专业）
3. 缺失 / 不可信                                       → 标题+正文术语反推，basis=推断（依据:...）
4. 全部无命中                                          → 回退综合协调 ZH，basis=推断（默认综合协调）
"""
from __future__ import annotations

import logging

from .protocol_loader import load_disciplines

logger = logging.getLogger(__name__)

# 现有 5 粗专业 → 当无细分线索时的兜底细分代码（与契约 coarse 映射建议一致）
_COARSE_FALLBACK = {
    "structure": "JG",
    "architecture": "JZ",
    "mep": "JDQ",
    "decoration": "ZS",
    "general": "ZH",
}

_COARSE_NAMES = {"综合协调": "general"}

_DEFAULT_CODE = "ZH"
_DEFAULT_NAME = "综合协调"


def _build_term_index() -> dict[str, list[tuple[str, str]]]:
    """为每个细分专业建立 ``术语 → [(code, 术语)]`` 反查表。

    术语来源：专业名称、priority_concerns、objects 名称。
    """
    index: dict[str, list[tuple[str, str]]] = {}
    for code, disc in load_disciplines().items():
        terms: list[str] = []
        name = str(disc.get("name_cn", "")).strip()
        if name:
            terms.append(name)
        terms.extend(str(c) for c in disc.get("priority_concerns", []) or [])
        for obj in disc.get("objects", []) or []:
            if isinstance(obj, dict) and obj.get("name"):
                terms.append(str(obj["name"]))
        for term in terms:
            term = term.strip()
            if term:
                index.setdefault(term, []).append((code, term))
    return index


def _resolve_name(code: str) -> str:
    disc = load_disciplines().get(code, {})
    return str(disc.get("name_cn", "")) or (_DEFAULT_NAME if code == _DEFAULT_CODE else code)


def _coarse_to_fine(coarse: str) -> str | None:
    """粗专业 → 该 coarse 下 disciplines.yaml 首个细分；无知识时用静态兜底。"""
    for code, disc in load_disciplines().items():
        if str(disc.get("coarse", "")) == coarse:
            return code
    return _COARSE_FALLBACK.get(coarse)


def _infer_from_text(title: str, body: str) -> tuple[str, str] | None:
    """用术语命中反推专业，返回 ``(code, 命中术语)``，无命中返回 None。"""
    text = f"{title} {body}"
    best: tuple[str, str] | None = None
    best_len = 0
    for term, hits in _build_term_index().items():
        if term and term in text and len(term) > best_len:
            best = hits[0]
            best_len = len(term)
    return best


def route(discipline: str | None, title: str, body: str) -> dict:
    """返回 ``{code, name, basis}``。"""
    title = title or ""
    body = body or ""
    disciplines = load_disciplines()
    raw = (discipline or "").strip()

    # 1. 显式细分代码（大小写宽容）
    if raw:
        upper = raw.upper()
        if upper in disciplines or (not disciplines and upper in _COARSE_FALLBACK.values()):
            return {"code": upper, "name": _resolve_name(upper), "basis": "显式"}

        # 2. 显式粗专业 / 粗专业中文名
        coarse = raw.lower()
        coarse = _COARSE_NAMES.get(raw, coarse)
        if coarse in _COARSE_FALLBACK:
            fine = _coarse_to_fine(coarse) or _DEFAULT_CODE
            return {
                "code": fine,
                "name": _resolve_name(fine),
                "basis": f"显式（粗专业 {coarse}→{fine}）",
            }

    # 3. 术语反推
    inferred = _infer_from_text(title, body)
    if inferred is not None:
        code, term = inferred
        return {"code": code, "name": _resolve_name(code), "basis": f"推断（依据:{term}）"}

    # 4. 默认综合协调
    return {
        "code": _DEFAULT_CODE,
        "name": _resolve_name(_DEFAULT_CODE),
        "basis": "推断（默认综合协调）",
    }
