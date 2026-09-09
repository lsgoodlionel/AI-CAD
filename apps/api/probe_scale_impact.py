# 运行方式（容器无挂载，需 docker cp 注入；cad_api 被多会话共用，请自建容器）：
#   docker run -d --name cad_api_recall --network cad_default \
#     -e DATABASE_URL=postgresql://cad_user:cad_pass@postgres:5432/cad_db \
#     -e MINIO_ENDPOINT=minio:9000 -e MINIO_ACCESS_KEY=cad_minio \
#     -e MINIO_SECRET_KEY=cad_minio_pass -w /app cad-api:local sleep infinity
#   docker cp core/model3d cad_api_recall:/app/core/ && docker cp <本文件> cad_api_recall:/app/
#   docker exec cad_api_recall python <本文件名>
# PyMuPDF 与 torch 不能同进程；fitz doc 用完必须 close()。
"""页宽>5000 那 56 张（有落库比例可对照的）：当前 vs 绕开 DXF 分支，柱各多少。"""
import asyncio
import databases as dbl
from core.config import settings
import core.model3d.element_recognizer as er
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.storage import get_file_bytes

async def main():
    db = dbl.Database(settings.database_url); await db.connect()
    rows = await db.fetch_all(
        "SELECT d.id::text AS id, d.title, d.discipline, d.file_key, t.scale_m_pt "
        "FROM drawings d JOIN drawing_transform t ON t.drawing_id=d.id "
        "WHERE d.file_key IS NOT NULL AND t.scale_m_pt IS NOT NULL "
        "AND (d.title LIKE '%南区%' OR d.title LIKE '%D1区%')")
    a = b = n = 0
    print(f"{'当前':>6} {'改比例后':>8}  比例(落库)  图名")
    for r in rows:
        try:
            geom = extract_pdf_geometry(get_file_bytes(r["file_key"]))
        except Exception:
            continue
        if geom.page_w <= er._DXF_MODEL_SPACE_THRESHOLD:
            continue
        stored = float(r["scale_m_pt"])
        cur = er.recognize(geom, r["discipline"], r["id"], drawing_title=r["title"],
                           scale_override=stored)
        _o = er._detect_scale
        er._detect_scale = lambda *x, **k: (stored, False)
        try:
            fix = er.recognize(geom, r["discipline"], r["id"], drawing_title=r["title"],
                               scale_override=stored)
        finally:
            er._detect_scale = _o
        n += 1; a += len(cur.columns); b += len(fix.columns)
        print(f"{len(cur.columns):6d} {len(fix.columns):8d}  1:{stored/0.000352778:7.0f}  {str(r['title'])[:44]}")
    print(f"\n{n} 张图：当前柱 {a} → 用落库比例 {b}（+{b-a}）")
    await db.disconnect()
asyncio.run(main())
