"""B 阶段：在 A 阶段落盘的瓦片上跑 YOLO v5，取**两者分歧**的框出表。

**不导入 PyMuPDF** —— 与 torch 同进程会段错误（aarch64 老毛病，实测 A 阶段
不关 `doc` 时几十张图后必崩，加显式释放才跑满）。
"""
import collections, json, os, random
from PIL import Image, ImageDraw, ImageFont
from core.model3d.gold.batch_codes import make_codes
from ultralytics import YOLO

WEIGHTS = "/tmp/yolo_out/cad_v5/weights/best.pt"
OUT, CONF, IOU_SAME, PER_GROUP = "/tmp/gpt_yolo", 0.25, 0.30, 20
random.seed(20260919)


def _iou(a, b):
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    ua = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter
    return inter / ua if ua > 0 else 0.0


model = YOLO(WEIGHTS)
meta = json.load(open(f"{OUT}/stage_a.json"))
pool = collections.defaultdict(list)
n_rule = n_model = 0
for m in meta:
    img = Image.open(f"{OUT}/tiles/{m['tile']}").convert("RGB")
    res = model.predict(img, conf=CONF, verbose=False)[0]
    mb = [tuple(float(v) for v in b.xyxy[0].tolist())
          for b in res.boxes if int(b.cls[0]) == 0]
    rb = [tuple(v) for v in m["rule"]]
    n_rule += len(rb); n_model += len(mb)
    used = set()
    for r in rb:
        hit = [j for j, b in enumerate(mb) if _iou(r, b) >= IOU_SAME]
        used.update(hit)
        pool["both" if hit else "rule_only"].append((m, img, r))
    for j, b in enumerate(mb):
        if j not in used:
            pool["model_only"].append((m, img, b))
print(f"规则框 {n_rule} · 模型框 {n_model}")
print("分组:", {k: len(v) for k, v in pool.items()})

picks = []
for k, v in pool.items():
    random.shuffle(v); picks += [(k, *e) for e in v[:PER_GROUP]]
random.shuffle(picks)
codes = make_codes(len(picks) + 20, seed=20260919)
try:
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
except Exception:
    font = ImageFont.load_default()
CELL, COLS, ROWS, CTX = 420, 4, 3, 6.0
tiles = []
for grp, m, img, box in picks:
    bx0, by0, bx1, by1 = box
    half = max(max(bx1 - bx0, by1 - by0) * CTX, 90) / 2
    cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
    ox = max(0, min(cx - half, img.width - 2 * half))
    oy = max(0, min(cy - half, img.height - 2 * half))
    crop = img.crop((int(ox), int(oy), int(ox + 2 * half), int(oy + 2 * half)))
    if crop.width < 8 or crop.height < 8:
        continue
    d = ImageDraw.Draw(crop)
    d.rectangle([bx0 - ox, by0 - oy, bx1 - ox, by1 - oy], outline=(255, 0, 0), width=4)
    tiles.append((codes.pop(), grp, m["drawing_id"], m["title"],
                  crop.resize((CELL, CELL))))

man, sheets = [], []
for si in range(0, len(tiles), COLS * ROWS):
    chunk = tiles[si:si + COLS * ROWS]
    sh = Image.new("RGB", (CELL * COLS, (CELL + 36) * ROWS), "white")
    dd = ImageDraw.Draw(sh)
    for i, (tag, grp, did, ti, im) in enumerate(chunk):
        cx2, cy2 = (i % COLS) * CELL, (i // COLS) * (CELL + 36)
        dd.text((cx2 + 8, cy2 + 6), tag, fill="black", font=font)
        sh.paste(im, (cx2, cy2 + 36))
        dd.rectangle([cx2, cy2, cx2 + CELL - 1, cy2 + CELL + 35], outline="#888")
        man.append((tag, grp, did, ti.replace("\t", " ")))
    p = f"{OUT}/Y{si // (COLS * ROWS) + 1}.png"
    sh.save(p); sheets.append(p)
with open(f"{OUT}/manifest.tsv", "w") as f:
    f.write("tag\tgroup\tdrawing_id\ttitle\n")
    for x in man:
        f.write("\t".join(x) + "\n")
print(f"接触表 {len(sheets)} 张 / {len(man)} 格 ·",
      dict(collections.Counter(x[1] for x in man)))
for p in sheets:
    print(" ", p)
