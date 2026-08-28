"""每个候选框裁成带上下文的小图，拼成接触表 —— 一张图判 20 个候选。

**为什么不整块送**：框在切片里约 15px，判得准需要 40px 以上，
一块切片只能占满一张图 → 100 块就是 100 次交互。
只裁框周边则 20 个一张，120 个候选 6 张搞定，且每个都看得清。

上下文取框边长的 12 倍：既能看见轴线和相邻构件，又不至于把框缩没。
"""
import glob, json, os, random, sys
from PIL import Image, ImageDraw, ImageFont

SRC = "/tmp/yolo_v5"
OUT = "/tmp/gpt_patch"
N_TARGET = int(sys.argv[1]) if len(sys.argv) > 1 else 120
COLS, ROWS = 5, 4
CELL = 380
CTX = 12.0            # 上下文 = 框边长的倍数
random.seed(20260828)

os.makedirs(OUT, exist_ok=True)
Image.MAX_IMAGE_PIXELS = None

# 收集候选：每块切片最多取 4 个，避免一张图刷屏
pool = []
for lbl in sorted(glob.glob(f"{SRC}/*/labels/*.txt")):
    boxes = [l.split() for l in open(lbl) if l.split()[:1] == ["0"]]
    if not (3 <= len(boxes) <= 20):
        continue
    img = lbl.replace("/labels/", "/images/")[:-4] + ".png"
    if not os.path.exists(img):
        continue
    name = os.path.basename(lbl)[:-4]
    for i, p in enumerate(random.sample(boxes, min(4, len(boxes))), 1):
        pool.append((name, img, i, [float(v) for v in p[1:]]))
random.shuffle(pool)
pool = pool[:N_TARGET]

try:
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 30)
    tiny = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
except Exception:
    font = tiny = ImageFont.load_default()

manifest = []
per = COLS * ROWS
sheets = (len(pool) + per - 1) // per
cache = {}
for s in range(sheets):
    chunk = pool[s * per:(s + 1) * per]
    BAR = 46
    sheet = Image.new("RGB", (CELL * COLS, BAR + CELL * ROWS), "white")
    d0 = ImageDraw.Draw(sheet)
    d0.rectangle([0, 0, sheet.width - 1, BAR - 1], fill="#111")
    sid = f"P{s+1}"
    d0.text((12, 8), f"{sid}   {len(chunk)} 个候选  编号 {sid}-01 ~ {sid}-{len(chunk):02d}",
            fill="white", font=font)
    for k, (name, img_path, bi, box) in enumerate(chunk):
        if img_path not in cache:
            cache[img_path] = Image.open(img_path).convert("RGB")
        src = cache[img_path]
        W, H = src.size
        cx, cy, bw, bh = box[0] * W, box[1] * H, box[2] * W, box[3] * H
        half = max(bw, bh, 6.0) * CTX / 2
        crop = src.crop((int(cx - half), int(cy - half),
                         int(cx + half), int(cy + half))).resize(
            (CELL, CELL), Image.LANCZOS)
        d = ImageDraw.Draw(crop)
        sc = CELL / (2 * half)
        x0 = CELL / 2 - bw * sc / 2; y0 = CELL / 2 - bh * sc / 2
        d.rectangle([x0, y0, x0 + bw * sc, y0 + bh * sc],
                    outline=(255, 0, 0), width=3)
        tag = f"{sid}-{k+1:02d}"
        d.rectangle([0, 0, 108, 30], fill=(255, 0, 0))
        d.text((5, 2), tag, fill="white", font=tiny)
        px, py = (k % COLS) * CELL, BAR + (k // COLS) * CELL
        sheet.paste(crop, (px, py))
        d0.rectangle([px, py, px + CELL - 1, py + CELL - 1],
                     outline=(90, 90, 90), width=2)
        manifest.append(f"{tag}\t{name}\t{bi}")
    sheet.save(f"{OUT}/{sid}.png")
    print(f"  {sid}.png {sheet.width}x{sheet.height}  候选 {len(chunk)}")
open(f"{OUT}/manifest.tsv", "w").write("patch\ttile\tbox_index\n" + "\n".join(manifest) + "\n")
print(f"合计 {len(pool)} 个候选 / {sheets} 张")
