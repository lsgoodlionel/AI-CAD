"""
services/drawing_info_extractor.py 单测 — 工程信息抽取编排(Phase E1-1)

纯函数层(build_info_items)离线可测:合成 DrawingGeometry + 模拟 OcrResult,
不依赖 fitz/ezdxf/OCR 后端。持久化层用 FakeDB 验证先删后插幂等。
"""
import pytest

from core.model3d.ocr.types import OcrResult, TextToken
from core.model3d.types import DrawingGeometry
from services.drawing_info_extractor import (
    build_info_items,
    persist_drawing_info,
)


# ── 合成夹具 ─────────────────────────────────────────────────────

def _geom_with_texts() -> DrawingGeometry:
    geom = DrawingGeometry(page_w=1000, page_h=800)
    # 标高 / 轴号 / 房间名 / 说明 各一条(classify_text 可识别形态)
    for x, y, content in [
        (100.0, 200.0, "-2.350"),
        (50.0, 300.0, "消防泵房"),
        (10.0, 10.0, "注:本图尺寸以毫米计"),
    ]:
        geom.texts.append((x, y, content))
        geom.text_layers.append("") if hasattr(geom, "text_layers") else None
    return geom


def _ocr_result() -> OcrResult:
    return OcrResult(
        tokens=(
            TextToken(text="+3.600", bbox=(1.0, 2.0, 30.0, 12.0),
                      confidence=0.98, kind="elevation", value=3.6),
            TextToken(text="B", bbox=(5.0, 5.0, 15.0, 15.0),
                      confidence=0.91, kind="axis"),
            TextToken(text="低置信噪声", bbox=(0.0, 0.0, 9.0, 9.0),
                      confidence=0.30, kind="note"),
        ),
        backend="rapidocr", dpi=200, page_size=(1000.0, 800.0),
    )


# ── build_info_items:矢量文字 → 分类入条 ─────────────────────────

def test_vector_texts_are_classified_into_items():
    items = build_info_items(geom=_geom_with_texts(), ocr=None, filename=None)

    by_cat = {}
    for it in items:
        by_cat.setdefault(it["category"], []).append(it)

    # 标高被解析出数值(米)
    elevs = by_cat.get("elevation", [])
    assert len(elevs) == 1
    assert elevs[0]["content"] == "-2.350"
    assert elevs[0]["value_json"] == {"elevation_m": -2.35}
    assert elevs[0]["extractor"] == "vector_text"
    assert elevs[0]["location_json"] == {"x": 100.0, "y": 200.0}

    # 房间名与说明各归其类
    assert any(it["content"] == "消防泵房" for it in by_cat.get("room_name", []))
    assert any("毫米" in it["content"] for it in by_cat.get("note", []))


def test_empty_geometry_yields_no_items():
    assert build_info_items(geom=DrawingGeometry(), ocr=None, filename=None) == []


# ── build_info_items:OCR token → 入条(置信门槛) ──────────────────

def test_ocr_tokens_become_items_with_confidence_floor():
    items = build_info_items(geom=None, ocr=_ocr_result(), filename=None)

    ocr_items = [it for it in items if it["extractor"] == "ocr"]
    texts = {it["content"] for it in ocr_items}
    assert "+3.600" in texts
    assert "B" in texts
    # 低置信(0.30 < 0.6)噪声不入库
    assert "低置信噪声" not in texts

    elev = next(it for it in ocr_items if it["category"] == "elevation")
    assert elev["value_json"] == {"elevation_m": 3.6}
    assert elev["confidence"] == pytest.approx(0.98)
    assert elev["location_json"] == {"bbox": [1.0, 2.0, 30.0, 12.0]}


def test_ocr_backend_none_is_skipped():
    empty = OcrResult(backend="none")
    assert build_info_items(geom=None, ocr=empty, filename=None) == []


# ── build_info_items:文件名 → 图签条目 ───────────────────────────

def test_filename_produces_title_block_item():
    items = build_info_items(
        geom=None, ocr=None, filename="结施-05 三层梁配筋图 B版.pdf"
    )

    tbs = [it for it in items if it["category"] == "title_block"]
    assert len(tbs) == 1
    vj = tbs[0]["value_json"]
    assert vj["drawing_no"]
    assert tbs[0]["extractor"] == "filename"


# ── 去重:同类别同文本只留高置信 ─────────────────────────────────

def test_duplicate_content_keeps_highest_confidence():
    geom = DrawingGeometry(page_w=100, page_h=100)
    geom.texts.append((1.0, 1.0, "-2.350"))
    ocr = OcrResult(
        tokens=(TextToken(text="-2.350", bbox=(1, 1, 9, 9),
                          confidence=0.7, kind="elevation", value=-2.35),),
        backend="rapidocr",
    )

    items = build_info_items(geom=geom, ocr=ocr, filename=None)

    dups = [it for it in items if it["content"] == "-2.350"]
    assert len(dups) == 1
    # 矢量文字是确定性来源(confidence=None 视为 1.0),胜过 OCR 0.7
    assert dups[0]["extractor"] == "vector_text"


# ── persist:先删后插幂等 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_persist_deletes_then_inserts(fake_db):
    items = [{
        "category": "elevation", "content": "-2.350",
        "value_json": {"elevation_m": -2.35}, "location_json": {"x": 1, "y": 2},
        "extractor": "vector_text", "confidence": None,
    }]

    written = await persist_drawing_info(
        fake_db, project_id="p-1", drawing_id="d-1", items=items, version=2
    )

    assert written == 1
    calls = [c.args[0] for c in fake_db.execute.call_args_list]
    assert any("DELETE FROM drawing_extracted_info" in sql for sql in calls)
    assert any("INSERT INTO drawing_extracted_info" in sql for sql in calls)
    # 参数含溯源与代次
    insert_params = fake_db.execute.call_args_list[-1].args[1]
    assert insert_params["drawing_id"] == "d-1"
    assert insert_params["extraction_version"] == 2
