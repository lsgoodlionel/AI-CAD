"""
services/drawing_archive.py 单测 — 档案生效值规则 + 人审 verified(Phase E1.5)

生效值规则:同 (drawing_id, category, key) verified 优先,否则 active auto 里
confidence 最高。纯函数 effective_values 离线可测;verify/persist 用 FakeDB。
"""
import pytest

from services.drawing_archive import (
    build_verify_params,
    effective_values,
    normalized_key,
)


def _row(**kw):
    base = {
        "id": "r1", "drawing_id": "d1", "category": "elevation",
        "content": "-2.350", "value_json": {"elevation_m": -2.35},
        "extractor": "ocr", "confidence": 0.9,
        "source_kind": "auto", "is_active": True,
    }
    base.update(kw)
    return base


# ── 生效值规则 ───────────────────────────────────────────────────

def test_verified_wins_over_auto():
    # 人工把 auto 'a'(-2.35)修正为 -2.40:verified 经 supersedes 抑制被改的 auto
    rows = [
        _row(id="a", value_json={"elevation_m": -2.35}, confidence=0.99, source_kind="auto"),
        _row(id="v", value_json={"elevation_m": -2.40}, source_kind="verified",
             confidence=None, supersedes="a"),
    ]
    eff = effective_values(rows)
    assert len(eff) == 1
    assert eff[0]["id"] == "v"
    assert eff[0]["value_json"]["elevation_m"] == -2.40


def test_verified_same_value_dedups_with_auto():
    # 人工「确认」auto(值不变):同归一化 key,verified 优先,不重复
    rows = [
        _row(id="a", value_json={"elevation_m": -2.35}, confidence=0.9, source_kind="auto"),
        _row(id="v", value_json={"elevation_m": -2.35}, source_kind="verified",
             confidence=None, supersedes="a"),
    ]
    eff = effective_values(rows)
    assert len(eff) == 1
    assert eff[0]["source_kind"] == "verified"


def test_highest_confidence_auto_wins_when_no_verified():
    rows = [
        _row(id="lo", extractor="vlm", confidence=0.7),
        _row(id="hi", extractor="ocr", confidence=0.95),
    ]
    eff = effective_values(rows)
    assert len(eff) == 1
    assert eff[0]["id"] == "hi"


def test_inactive_auto_excluded():
    rows = [
        _row(id="dead", confidence=0.99, is_active=False),
        _row(id="live", confidence=0.80, is_active=True),
    ]
    eff = effective_values(rows)
    assert [e["id"] for e in eff] == ["live"]


def test_different_categories_kept_separate():
    rows = [
        _row(id="e", category="elevation", content="-2.350"),
        _row(id="a", category="axis", content="B"),
    ]
    eff = effective_values(rows)
    assert {e["id"] for e in eff} == {"e", "a"}


def test_normalized_key_groups_same_elevation_value():
    # 同标高值不同文本表述归一到同 key（供去重择优）
    assert normalized_key("elevation", "-2.350", {"elevation_m": -2.35}) == \
           normalized_key("elevation", "-2.35", {"elevation_m": -2.35})


# ── 人审 verify 参数构造 ─────────────────────────────────────────

def test_build_verify_params_creates_verified_and_deactivates_auto():
    params = build_verify_params(
        project_id="p1", drawing_id="d1", category="elevation",
        content="-2.400", value_json={"elevation_m": -2.40},
        supersedes_id="auto-1", reviewer_id="u1",
    )
    ins = params["insert"]
    assert ins["source_kind"] == "verified"
    assert ins["supersedes"] == "auto-1"
    assert ins["reviewed_by"] == "u1"
    assert ins["is_active"] is True
    # 被推翻的 auto 行 id
    assert params["deactivate_id"] == "auto-1"


@pytest.mark.asyncio
async def test_persist_verify_writes_and_deactivates(fake_db):
    from services.drawing_archive import persist_verify

    await persist_verify(fake_db, build_verify_params(
        project_id="p1", drawing_id="d1", category="elevation",
        content="-2.400", value_json={"elevation_m": -2.40},
        supersedes_id="auto-1", reviewer_id="u1",
    ))
    sqls = [c.args[0] for c in fake_db.execute.call_args_list]
    assert any("UPDATE drawing_extracted_info" in s and "is_active" in s for s in sqls)
    assert any("INSERT INTO drawing_extracted_info" in s for s in sqls)
