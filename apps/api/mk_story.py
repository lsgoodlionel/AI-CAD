"""剖面楼层数 → 整图缩略接触表。

楼层标高是模型 z 方向的全部依据（层高错则体积全错），从未验过。
查库先得到两条**不依赖判读者**的事实：

    常量  DEFAULT_STORY_HEIGHT_M = 4.5 · DEFAULT_BASEMENT_HEIGHT_M = 4.2
    实际  歌剧院层高序列 12 个里 **10 个**正好等于默认值
          第二工程 16 个里 **11 个**
    来源  elevation_source 以 `manual`（人工录入）为主，不是从图上提取

「恰好等于默认值」不等于「就是默认值」（4.5 米本就是常见层高），
所以要用图纸本身来核对：**剖面图上能数出几个楼层**。

**对照组构造上就干净**：混入平面图 —— 平面图不可能被当成剖面，
判读者若把它们也数出楼层，说明看不清，当场就知道。
（上一批的对照组取自 component_instances，本身 32% 不是柱，两组都吵，
分不开 —— 这次不重蹈。）
"""
import asyncio, collections, os, random
import databases as databases_lib, fitz
from PIL import Image, ImageDraw, ImageFont
from core.config import settings
from core.model3d.gold.batch_codes import make_codes
from core.storage import get_file_bytes

DPI = 70
OUT = "/tmp/gpt_story"
os.makedirs(OUT, exist_ok=True)
random.seed(20260917)


async def main():
    db = databases_lib.Database(settings.database_url); await db.connect()
    picks = []
    for grp, sql in (
        # **排除地质／土方剖面**：自验第一版时发现「剖面」一组大半是
        # 「自然地面／普遍区域开挖面」这类基坑与地质剖面，上面根本没有楼层。
        # 只留建筑与结构专业，并排掉土方、基坑、围护、管沟、地质、开挖等词。
        ("section", "d.title LIKE '%剖面%' "
                    "AND d.discipline IN ('architecture','structure') "
                    "AND d.title NOT LIKE '%节点%' AND d.title NOT LIKE '%大样%' "
                    "AND d.title NOT LIKE '%详图%' AND d.title NOT LIKE '%土方%' "
                    "AND d.title NOT LIKE '%基坑%' AND d.title NOT LIKE '%围护%' "
                    "AND d.title NOT LIKE '%管沟%' AND d.title NOT LIKE '%地质%' "
                    "AND d.title NOT LIKE '%开挖%' AND d.title NOT LIKE '%换撑%' "
                    "AND d.title NOT LIKE '%加固%' AND d.title NOT LIKE '%坡道%'"),
        ("plan_control", "d.title LIKE '%平面图%' AND d.title NOT LIKE '%剖面%' "
                         "AND d.discipline IN ('architecture','structure')"),
    ):
        rows = await db.fetch_all(
            f"SELECT d.id, d.title, d.file_key, p.name proj FROM drawings d "
            f"JOIN projects p ON p.id = d.project_id "
            f"WHERE {sql} AND d.title IS NOT NULL")
        rows = list(rows); random.shuffle(rows)
        cap = 32 if grp == "section" else 16
        picks += [(grp, r) for r in rows[:cap]]
    random.shuffle(picks)
    print("抽到", len(picks), dict(collections.Counter(g for g, _ in picks)))

    codes = make_codes(len(picks) + 20, seed=20260917)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
    except Exception:
        font = ImageFont.load_default()
    CELL, COLS, ROWS = 470, 4, 3
    tiles = []
    for grp, r in picks:
        try:
            page = fitz.open(stream=get_file_bytes(r["file_key"]), filetype="pdf")[0]
            pix = page.get_pixmap(dpi=DPI)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        except Exception:
            continue
        ex = img.convert("L").getextrema()
        if ex[1] - ex[0] < 30:
            continue
        img.thumbnail((CELL, CELL), Image.LANCZOS)
        canvas = Image.new("RGB", (CELL, CELL), "white")
        canvas.paste(img, ((CELL - img.width) // 2, (CELL - img.height) // 2))
        tiles.append((codes.pop(), grp, str(r["id"]), str(r["title"]),
                      str(r["proj"]), canvas))

    man, sheets = [], []
    for si in range(0, len(tiles), COLS * ROWS):
        chunk = tiles[si:si + COLS * ROWS]
        sh = Image.new("RGB", (CELL * COLS, (CELL + 36) * ROWS), "white")
        dd = ImageDraw.Draw(sh)
        for i, (tag, grp, did, ti, proj, img) in enumerate(chunk):
            cx, cy = (i % COLS) * CELL, (i // COLS) * (CELL + 36)
            dd.text((cx + 8, cy + 6), tag, fill="black", font=font)
            sh.paste(img, (cx, cy + 36))
            dd.rectangle([cx, cy, cx + CELL - 1, cy + CELL + 35], outline="#888")
            man.append((tag, grp, did, proj, ti))
        p = f"{OUT}/T{si // (COLS * ROWS) + 1}.png"
        sh.save(p); sheets.append(p)
    with open(f"{OUT}/manifest.tsv", "w") as f:
        f.write("tag\tgroup\tdrawing_id\tproject\ttitle\n")
        for x in man:
            f.write("\t".join(x) + "\n")
    print(f"接触表 {len(sheets)} 张 / {len(man)} 格 ·",
          dict(collections.Counter(x[1] for x in man)))
    for p in sheets:
        print(" ", p)
    await db.disconnect()

asyncio.run(main())
