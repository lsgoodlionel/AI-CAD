# 运行方式（容器无挂载，需 docker cp 注入；cad_api 被多会话共用，请自建容器）：
#   docker run -d --name cad_api_recall --network cad_default \
#     -e DATABASE_URL=postgresql://cad_user:cad_pass@postgres:5432/cad_db \
#     -e MINIO_ENDPOINT=minio:9000 -e MINIO_ACCESS_KEY=cad_minio \
#     -e MINIO_SECRET_KEY=cad_minio_pass -w /app cad-api:local sleep infinity
#   docker cp core/model3d cad_api_recall:/app/core/ && docker cp <本文件> cad_api_recall:/app/
#   docker exec cad_api_recall python <本文件名>
# PyMuPDF 与 torch 不能同进程；fitz doc 用完必须 close()。
"""`is_wall_drawing` 会不会把「墙柱…」图判成墙图，从而关掉柱的猜测路径。"""
import asyncio
import databases as dbl
from core.config import settings
import core.model3d.element_recognizer as er
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.storage import get_file_bytes

PROBE = ["墙柱平面分图", "结构-竣工图--墙柱平面分图（一）", "三~四层大歌剧厅墙配筋平面图",
         "地下一层墙柱定位图", "柱平面图", "四夹~五层柱平面图", "剪力墙柱表"]

async def main():
    print("图名判定（离线，纯函数）：")
    for t in PROBE:
        print(f"  is_wall_drawing={er.is_wall_drawing(t)!s:5s}  {t}")
    db = dbl.Database(settings.database_url); await db.connect()
    rows = await db.fetch_all(
        "SELECT d.id::text AS id,d.title,d.discipline,d.file_key,t.scale_m_pt "
        "FROM drawings d LEFT JOIN drawing_transform t ON t.drawing_id=d.id "
        "WHERE d.title LIKE '%墙柱%' AND d.file_key IS NOT NULL")
    print(f"\n库里图名含「墙柱」的图 {len(rows)} 张：")
    tot_on = tot_off = 0
    for r in rows:
        wd = er.is_wall_drawing(r["title"])
        try:
            geom = extract_pdf_geometry(get_file_bytes(r["file_key"]))
        except Exception:
            print(f"  读不出 {r['title'][:40]}"); continue
        sc = float(r["scale_m_pt"]) if r["scale_m_pt"] else None
        cur = er.recognize(geom, r["discipline"], r["id"], drawing_title=r["title"], scale_override=sc)
        # 关掉图名闸：把标题换成不含「墙」的等价名
        off = er.recognize(geom, r["discipline"], r["id"], drawing_title="（去墙字）平面图", scale_override=sc)
        tot_on += len(cur.columns); tot_off += len(off.columns)
        print(f"  is_wall_drawing={wd!s:5s} 柱 {len(cur.columns):5d} → 关闸后 {len(off.columns):5d}  {str(r['title'])[:46]}")
    print(f"\n合计：当前 {tot_on} → 关掉图名闸 {tot_off}")
    await db.disconnect()
asyncio.run(main())
