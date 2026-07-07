"""图纸批量上传 / ZIP 整套导入 Router 测试（蓝图 4.1）"""
import io
import json
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

PROJECT_ID = "22222222-2222-2222-2222-222222222222"

# 每张图纸落库需要两次 fetch_one：INSERT drawings、INSERT ai_review_reports
def _drawing_rows(n: int) -> list[dict]:
    rows: list[dict] = []
    for i in range(n):
        rows.append({"id": f"d-{i}"})
        rows.append({"id": f"rep-{i}"})
    return rows


def _patches(run_delay: MagicMock):
    return (
        patch("routers.drawings.upload_file", MagicMock()),
        patch("routers.drawings.run_ai_review", MagicMock(delay=run_delay)),
        patch("routers.drawings.write_audit", new=AsyncMock()),
    )


def _pdf_file(name: str) -> tuple[str, tuple[str, bytes, str]]:
    return ("files", (name, b"%PDF-1.4 fake content", "application/pdf"))


# ── POST /drawings/batch ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_batch_upload_two_files_success(client, fake_db):
    fake_db.fetch_one.side_effect = _drawing_rows(2)
    run_delay = MagicMock()
    p1, p2, p3 = _patches(run_delay)
    meta = [{"filename": "结施-01.pdf", "drawing_no": "JG-01", "discipline": "structure"}]

    with p1, p2, p3:
        resp = await client.post(
            "/api/v1/drawings/batch",
            data={"project_id": PROJECT_ID, "items_meta": json.dumps(meta)},
            files=[_pdf_file("结施-01.pdf"), _pdf_file("建施-02.pdf")],
        )

    assert resp.status_code == 201
    data = resp.json()
    assert len(data["created"]) == 2
    assert data["failed"] == []
    assert data["review_triggered"] == 2
    assert run_delay.call_count == 2
    # items_meta 优先于文件名解析
    assert data["created"][0]["drawing_no"] == "JG-01"


@pytest.mark.asyncio
async def test_batch_upload_partial_failure_does_not_block_rest(client, fake_db):
    """非法扩展名单文件失败，其余文件正常入库"""
    fake_db.fetch_one.side_effect = _drawing_rows(1)
    run_delay = MagicMock()
    p1, p2, p3 = _patches(run_delay)

    with p1, p2, p3:
        resp = await client.post(
            "/api/v1/drawings/batch",
            data={"project_id": PROJECT_ID},
            files=[
                ("files", ("说明.txt", b"not a drawing", "text/plain")),
                _pdf_file("水施-03.pdf"),
            ],
        )

    assert resp.status_code == 201
    data = resp.json()
    assert len(data["created"]) == 1
    assert len(data["failed"]) == 1
    assert "UNSUPPORTED_FILE_TYPE" in data["failed"][0]["error"]
    assert run_delay.call_count == 1


@pytest.mark.asyncio
async def test_batch_upload_invalid_items_meta_rejected(client, fake_db):
    resp = await client.post(
        "/api/v1/drawings/batch",
        data={"project_id": PROJECT_ID, "items_meta": "{not json"},
        files=[_pdf_file("a.pdf")],
    )
    assert resp.status_code == 400
    assert "INVALID_ITEMS_META" in str(resp.json())


@pytest.mark.asyncio
async def test_batch_upload_auto_review_off_skips_trigger(client, fake_db):
    fake_db.fetch_one.side_effect = _drawing_rows(1)
    run_delay = MagicMock()
    p1, p2, p3 = _patches(run_delay)

    with p1, p2, p3:
        resp = await client.post(
            "/api/v1/drawings/batch",
            data={"project_id": PROJECT_ID, "auto_review": "false"},
            files=[_pdf_file("电施-05.pdf")],
        )

    assert resp.status_code == 201
    assert resp.json()["review_triggered"] == 0
    run_delay.assert_not_called()


# ── POST /drawings/import-zip ────────────────────────────────────

def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_import_zip_creates_and_skips(client, fake_db):
    """合法图纸入库；隐藏文件与非白名单扩展名跳过"""
    fake_db.fetch_one.side_effect = _drawing_rows(1)
    run_delay = MagicMock()
    p1, p2, p3 = _patches(run_delay)
    payload = _zip_bytes({
        "图纸/结施-01.pdf": b"%PDF-1.4 fake",
        "图纸/.DS_Store": b"junk",
        "图纸/说明.docx": b"doc",
    })

    with p1, p2, p3:
        resp = await client.post(
            "/api/v1/drawings/import-zip",
            data={"project_id": PROJECT_ID},
            files=[("file", ("套图.zip", payload, "application/zip"))],
        )

    assert resp.status_code == 201
    data = resp.json()
    assert len(data["created"]) == 1
    assert data["created"][0]["filename"] == "图纸/结施-01.pdf"
    assert len(data["skipped"]) == 2
    assert data["review_triggered"] == 1


@pytest.mark.asyncio
async def test_import_zip_rejects_zip_slip(client, fake_db):
    payload = _zip_bytes({"../evil.pdf": b"%PDF-1.4"})
    resp = await client.post(
        "/api/v1/drawings/import-zip",
        data={"project_id": PROJECT_ID},
        files=[("file", ("bad.zip", payload, "application/zip"))],
    )
    assert resp.status_code == 400
    assert "ZIP_SLIP_DETECTED" in str(resp.json())


@pytest.mark.asyncio
async def test_import_zip_rejects_invalid_archive(client, fake_db):
    resp = await client.post(
        "/api/v1/drawings/import-zip",
        data={"project_id": PROJECT_ID},
        files=[("file", ("bad.zip", b"not a zip at all", "application/zip"))],
    )
    assert resp.status_code == 400
    assert "INVALID_ZIP_FILE" in str(resp.json())


# ── ZIP 中文文件名编码修复（cp437 乱码还原）─────────────────────

@pytest.mark.unit
def test_fix_zip_filename_restores_utf8_from_cp437():
    """未设 UTF-8 标志位的中文条目：cp437 乱码还原为原始 UTF-8 名称"""
    from routers.drawings import _fix_zip_filename

    original = "给排水-竣工图-P-60-02-屋面雨水图.pdf"
    info = zipfile.ZipInfo(original)
    # 模拟老工具打包：字节按 cp437 解读、UTF-8 标志位未设
    info.filename = original.encode("utf-8").decode("cp437")
    info.flag_bits = 0
    assert _fix_zip_filename(info) == original


@pytest.mark.unit
def test_fix_zip_filename_keeps_utf8_flagged_entries():
    from routers.drawings import _fix_zip_filename, _ZIP_UTF8_FLAG

    info = zipfile.ZipInfo("结构图.pdf")
    info.flag_bits = _ZIP_UTF8_FLAG
    assert _fix_zip_filename(info) == "结构图.pdf"


@pytest.mark.unit
def test_fix_zip_filename_ascii_passthrough():
    from routers.drawings import _fix_zip_filename

    info = zipfile.ZipInfo("S-0-11-103C.pdf")
    info.flag_bits = 0
    assert _fix_zip_filename(info) == "S-0-11-103C.pdf"
