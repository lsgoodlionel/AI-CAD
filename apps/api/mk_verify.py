"""切片 + **编号的候选框** + 九宫格 → 交给独立判读。

**为什么是核对而不是画框**：实测 GPT 的计数与判断可靠（人工复核 89% 正确），
但定位不可靠（分格分布多次与实测不符）。让它逐个判「是/不是」，
再按格指出漏检，正好用强项避弱项。
"""
import glob, os, random, sys
from PIL import Image, ImageDraw, ImageFont

SRC = "/tmp/yolo_v5"
OUT = "/tmp/gpt_verify"
Z = 3          # 框在切片里约 15px，3 倍后 45px 才看得清
N = int(sys.argv[1]) if len(sys.argv) > 1 else 8
random.seed(20260827)

os.makedirs(OUT, exist_ok=True)
cands = []
for lbl in glob.glob(f"{SRC}/*/labels/*.txt"):
    boxes = [l.split() for l in open(lbl) if l.split()[:1] == ["0"]]
    if not (5 <= len(boxes) <= 18):           # 太少没信息，太多标不动
        continue
    # **筛掉大片空白的块**：内容只占一角时，判读者要在空图里找东西
    img_path = lbl.replace("/labels/", "/images/")[:-4] + ".png"
    try:
        probe = Image.open(img_path).convert("L").resize((64, 64))
    except Exception:
        continue
    ink = sum(1 for v in probe.getdata() if v < 200) / (64 * 64)
    if ink < 0.06:
        continue
    cands.append((lbl, boxes))
picked = random.sample(cands, min(N, len(cands)))

try:
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 34)
    tiny = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
except Exception:
    font = tiny = ImageFont.load_default()

manifest = []
for k, (lbl, boxes) in enumerate(picked, 1):
    name = os.path.basename(lbl)[:-4]
    img_path = lbl.replace("/labels/", "/images/")[:-4] + ".png"
    src = Image.open(img_path).convert("RGB")
    S = src.size[0] * Z
    img = src.resize((S, S), Image.LANCZOS)
    BAR = 52
    out = Image.new("RGB", (S, S + BAR), "white")
    out.paste(img, (0, BAR))
    d = ImageDraw.Draw(out)
    d.rectangle([0, 0, S - 1, BAR - 1], fill="#111")
    tid = f"V{k:02d}"
    d.text((12, 10), f"{tid}   候选框 {len(boxes)} 个   九宫格 A1~C3",
           fill="white", font=font)
    step = S // 3
    for i in (1, 2):
        d.line([(i * step, BAR), (i * step, S + BAR)], fill=(120, 200, 255), width=2)
        d.line([(0, BAR + i * step), (S, BAR + i * step)], fill=(120, 200, 255), width=2)
    for r, rn in enumerate("ABC"):
        for c in range(3):
            x, y = c * step + 6, BAR + r * step + 4
            d.rectangle([x - 3, y - 2, x + 52, y + 32], fill=(120, 200, 255))
            d.text((x, y), f"{rn}{c+1}", fill="white", font=tiny)
    for i, p in enumerate(boxes, 1):
        cx, cy, w, h = (float(v) * S for v in p[1:])
        x0, y0, x1, y1 = cx - w / 2, cy - h / 2 + BAR, cx + w / 2, cy + h / 2 + BAR
        d.rectangle([x0, y0, x1, y1], outline=(255, 0, 0), width=3)
        # **编号放框外**：第一版把标签压在框上，框里是什么反而看不见了
        tag = str(i)
        tw = 15 * len(tag) + 8
        lx, ly = x0, max(BAR, y0 - 26)
        if ly <= BAR + 2:                     # 顶到画面上沿就改放框下
            ly = y1 + 2
        d.rectangle([lx, ly, lx + tw, ly + 24], fill=(255, 0, 0))
        d.text((lx + 4, ly + 1), tag, fill="white", font=tiny)
    out.save(f"{OUT}/{tid}.png")
    manifest.append(f"{tid}\t{name}\t{len(boxes)}")
    print(f"  {tid}.png  候选框 {len(boxes)}  ← {name}")
open(f"{OUT}/manifest.tsv", "w").write("id\ttile\tboxes\n" + "\n".join(manifest) + "\n")
