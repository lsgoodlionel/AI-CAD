"""标题栏**字段标签**不是图纸内容 —— 实测占了档案层近 3 万条。

**两个工程同构**（`other` 类别高频内容）：

| 内容 | 大歌剧院 | 轨道交通 |
|---|---:|---:|
| PROJECT | 2309 | 1454 |
| DATE | 2309 | 1507 |
| CLIENT | 2309 | 1296 |
| SCALE | 2236 | 1246 |
| DISCIPLINE | 2309 | 764 |

每张图各出现一次 —— 因为它们是**标题栏的表头**（ISO 7200
《技术产品文件 标题栏》/ GB/T 10609.1《技术制图 标题栏》的标准字段名），
是「PROJECT:」这个**标签**，不是项目名那个**值**。

标签本身零信息量，却：
- 占档案层约 2.8 万条（大歌剧院）+ 1 万条（轨道交通）
- 把 `other` 的占比抬高到 51% / 37%，掩盖了真正分类不出的内容

归入 `title_block_label` 单独一类 —— **不删除**（它们能标出标题栏
字段区域的位置，正是「图框字段区域记忆」要用的锚），只是不再混在 `other` 里。
"""
from __future__ import annotations

import pytest

from services.title_block_labels import is_title_block_label


@pytest.mark.unit
def test_iso7200_labels_are_recognised():
    """**核心用例**:ISO 7200 / GB/T 10609.1 标准字段名。"""
    for label in ("PROJECT", "CLIENT", "DATE", "SCALE", "DISCIPLINE",
                  "DRAWING TITLE", "JOB NO.", "STATUS", "DESIGN",
                  "DRAWING NO.", "REV", "SHEET", "APPROVED", "CHECKED"):
        assert is_title_block_label(label), label


@pytest.mark.unit
def test_chinese_labels_are_recognised():
    """GB/T 10609.1 的中文字段名同样是标签。"""
    for label in ("项目名称", "设计号", "图号", "比例", "日期",
                  "专业", "审核", "校对", "制图", "设计"):
        assert is_title_block_label(label), label


@pytest.mark.unit
def test_values_are_not_labels():
    """**不得误伤值** —— 标签是表头,值才是内容。"""
    for value in ("上海大歌剧院", "1:100", "2020-06-22", "A-201555010",
                  "地下一层平面图", "张三", "结构-竣工图"):
        assert not is_title_block_label(value), value


@pytest.mark.unit
def test_case_and_punctuation_insensitive():
    """`PROJECT` / `Project:` / `project ` 是同一个标签。"""
    for variant in ("PROJECT", "Project:", "project ", " PROJECT: "):
        assert is_title_block_label(variant), variant


@pytest.mark.unit
def test_empty_and_none():
    assert not is_title_block_label("")
    assert not is_title_block_label(None)


# ── 接进分类器 ────────────────────────────────────────────────

@pytest.mark.unit
def test_classifier_routes_labels_to_their_own_kind():
    """**接线用例**:标签不再落进 `other`。"""
    from core.model3d.ocr.classify import classify_text

    for label in ("PROJECT", "CLIENT", "DRAWING TITLE", "比例", "审核"):
        kind, value = classify_text(label)
        assert kind == "title_block_label", f"{label} → {kind}"
        assert value is None


@pytest.mark.unit
def test_classifier_keeps_existing_kinds():
    """**既有判定不受影响** —— 标高/轴号/尺寸/楼层名照旧。"""
    from core.model3d.ocr.classify import classify_text

    assert classify_text("±0.000")[0] == "elevation"
    assert classify_text("3600")[0] == "dimension"
    assert classify_text("一层")[0] == "level_name"
    # 「比例」是标签，但「1:100」是值
    assert classify_text("1:100")[0] != "title_block_label"
