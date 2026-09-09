# 运行方式（容器无挂载，需 docker cp 注入；cad_api 被多会话共用，请自建容器）：
#   docker run -d --name cad_api_recall --network cad_default \
#     -e DATABASE_URL=postgresql://cad_user:cad_pass@postgres:5432/cad_db \
#     -e MINIO_ENDPOINT=minio:9000 -e MINIO_ACCESS_KEY=cad_minio \
#     -e MINIO_SECRET_KEY=cad_minio_pass -w /app cad-api:local sleep infinity
#   docker cp core/model3d cad_api_recall:/app/core/ && docker cp <本文件> cad_api_recall:/app/
#   docker exec cad_api_recall python <本文件名>
# PyMuPDF 与 torch 不能同进程；fitz doc 用完必须 close()。
"""全项目扫：有多少张图的页宽越过 `_DXF_MODEL_SPACE_THRESHOLD`=5000pt，
   因而被当成「DXF 毫米模型空间」，比例硬钉 0.001 且**标记为非猜测**，
   从而在 `resolve_scale` 里压过 drawing_transform 的落库值。"""
import asyncio, os, collections
import databases as dbl, fitz
from core.config import settings
from core.storage import get_file_bytes

LIMIT = int(os.environ.get("LIMIT", "4000"))

async def main():
    db = dbl.Database(settings.database_url); await db.connect()
    rows = await db.fetch_all(
        "SELECT d.id::text AS id, d.title, d.file_key, t.scale_m_pt, t.confidence "
        "FROM drawings d LEFT JOIN drawing_transform t ON t.drawing_id=d.id "
        "WHERE d.file_key IS NOT NULL LIMIT :n", {"n": LIMIT})
    st = collections.Counter(); wide = []
    for r in rows:
        try:
            doc = fitz.open(stream=get_file_bytes(r["file_key"]), filetype="pdf")
            w, h = doc[0].rect.width, doc[0].rect.height
            doc.close()
        except Exception:
            st["读不出"] += 1; continue
        st["总"] += 1
        if w > 5000:
            st["页宽>5000 → 硬钉0.001"] += 1
            if r["scale_m_pt"]:
                st["  其中有落库比例（被压过）"] += 1
                wide.append((r["title"], w, float(r["scale_m_pt"]),
                             float(r["confidence"] or 0)))
    for k, v in st.items(): print(f"{v:6d}  {k}")
    print("\n被压过的图（比例倍差 = 落库/0.001）：")
    for t, w, s, c in sorted(wide, key=lambda x: -x[2])[:25]:
        print(f"  {w:7.0f}pt  落库{s:.5f}(conf {c:.2f}) 倍差{s/0.001:6.1f}x  {t[:56]}")
    await db.disconnect()
asyncio.run(main())
