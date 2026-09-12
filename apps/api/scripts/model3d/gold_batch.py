"""金标准批次生成器 —— 出接触表 + 判读命令 + 可追溯的 manifest。

用法（在注入了当前代码的容器目录里跑）：

    python -m scripts.model3d.gold_batch --kind columns --batch col3 \\
        --per-stratum 8 --blank 8 --seed 20260911 \\
        --host-dir /Users/lionel/Develop/CAD/gold_sheets/col3

它把三条教训焊进生成过程（纯函数与依据见 `core/model3d/gold/batch_design.py`、
`validity.py`）：

    原生分辨率   裁框按页面点定，DPI 反算到恰好填满格子，一个像素都不缩放
    分层+加权    按「工程·专业」分层抽样，记下每层的语料分量，事后加权回语料
    批内重测对   约 10% 的格子换编号、换页再出现一次，每批自带信度

旧生成器（`mk_col2.py` 等，已随探针清理移除，git 历史可取回）踩出的两条
经验原样保留：**doc 用完立刻关**（几十个文档同时开着会在 glibc 层崩），
**空白对照与被测组同形**（否则判读者凭形状就认出对照组）。

输出：S1.png…Sn.png、manifest.tsv（编号→图纸→层→裁框，**事后可复原**，
B 曾因 manifest 丢在 /tmp 而不得不重建）、meta.json（分层权重与扫描统计）、
BATCH.txt（判据由 `criteria_section` 从 CRITERIA.md 程序抽取）。
"""
from __future__ import annotations

import argparse
import asyncio
import gc
import json
import math
import random
import signal
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import databases as databases_lib
import fitz  # type: ignore[import-untyped]
from PIL import Image, ImageDraw, ImageFont

from core.config import settings
from core.model3d.element_recognizer import recognize
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.model3d.gold.batch_codes import make_codes
from core.model3d.render_budget import render_clip
from core.model3d.gold.batch_design import (
    Candidate, Mark, crop_box_pt, criteria_section, element_mark, plan_duplicates,
    render_dpi_for_crop, stratify,
)
from core.model3d.yolo_export import meters_to_page
from core.storage import get_file_bytes
from services.drawing_view_classifier import classify_view_type

CRITERIA = Path(__file__).resolve().parents[2] / "data/model3d/gold/CRITERIA.md"

#: 格子边长。4×3 张一页 = 1920×1440 —— 判读端（多模态模型）会把长边
#: 超过约 2048px 的图压缩，页面控制在这之内，裁图才真是原生分辨率。
CELL_PX, COLS, ROWS = 480, 4, 3
PER_SHEET = COLS * ROWS

#: 每层最多扫这么多张图（识别一张 5~10 秒，不设上限会扫几千张跑不完）。
SCAN_PER_STRATUM = 10

#: 单图抽取+识别的时限（秒）。配额按类分配后单图可达 6 万线 + 6 万多边形，
#: 一张病态图就能卡住整批 —— 建模流程有 `_RECOGNIZE_TIMEOUT_SEC`，这里照做。
#: 用 SIGALRM 而不是线程池：线程超时后仍在后台吃 CPU（建模那边实测过僵尸
#: 线程占满线程池），信号会在主线程里直接打断。
PER_DRAWING_TIMEOUT_SEC = 120

#: 每张图至多取几格 —— YOLO 批出过「一张图占 15 格」的集中。
PER_DRAWING = 2

#: 渲染失败按异常类型计数。**单格失败可以跳过，但不能静默** ——
#: 曾因 PyMuPDF 不收浮点 DPI，每一格都抛 TypeError 被吞掉，
#: 整批出 0 格、退出码 0。
RENDER_FAILURES: dict[str, int] = {}

#: 空白对照与被测组同形：框状画 0.6 米见方（典型柱截面），
#: 线状画 3 米横线（候选长度分布很宽，3 米不构成可辨认的特征）。
BLANK_BOX_M = 0.6
BLANK_LINE_M = 3.0

#: 批内重测对占被测格的比例。
DUP_FRACTION = 0.10

PROJECT_SHORT = {"上海大歌剧院": "歌剧院", "轨道交通工程(第二工程交叉验证)": "第二工程"}

#: 每类要问的问题与答案字段。只列已用过的类，别处按需再加（YAGNI）。
#: `rel`：裁框边长 = 构件尺寸 × rel。框状取 4（看得清构件与周边）；线状取 1.6 ——
#: 旧的墙/管生成器按固定 5 米裁，**长墙会被裁到自己的裁框外面**。
KIND_SPEC = {
    "walls": {"question": "红线压的是不是一面墙？", "field": "is_wall",
              "mark": "line", "mark_word": "红线", "attr": "walls", "rel": 1.6,
              "what": ("column / door / window / stair / pipe / furniture / equipment / "
                       "dimension / axis / text / frame / single_line / nothing / other")},
    "beams": {"question": "红线压的是不是一根梁？", "field": "is_beam",
              "mark": "line", "mark_word": "红线", "attr": "beams", "rel": 1.6,
              "what": ("column / wall / slab / dimension / axis / rebar / text / frame / "
                       "single_line / nothing / other")},
    "pipes": {"question": "红线画在的位置，是不是一根机电管线／风管／桥架？",
              "field": "is_pipe", "mark": "line", "mark_word": "红线", "attr": "pipes",
              "rel": 1.6,
              "what": "wall / beam_or_grid / leader / hatch / frame / nothing / other"},
    "columns": {"question": "框住的是不是一根柱子？", "field": "is_column",
                "mark": "box", "mark_word": "红色方框", "attr": "columns", "rel": 4.0,
                "what": ("wall / beam / door / window / stair / furniture / seat / "
                         "equipment / axis / dimension / text / hatch / frame / "
                         "elevation_mark / nothing / other")},
}


@dataclass
class Cell:
    code: str
    group: str                    # kept / blank / dup
    stratum: str
    drawing_id: str
    title: str
    crop_pt: tuple
    image: Image.Image = field(repr=False)
    dup_of: str = ""
    capped: bool = False


# ── 取样本 ───────────────────────────────────────────────────────

class _DrawingTimeout(Exception):
    pass


@contextmanager
def _time_limit(seconds: int):
    """超时后 `fired[0]` 为 True。**不能只靠异常冒泡**：`recognize` 自己有
    「任何异常返回空 FloorElements」的宽泛 except，会把超时吞掉、返回空结果 ——
    col3 批里一张图就这样被当成「0 个候选」计进了分层权重，而不是被跳过。"""
    fired = [False]

    def _raise(_signum, _frame):
        fired[0] = True
        raise _DrawingTimeout(f"超过 {seconds}s")
    old = signal.signal(signal.SIGALRM, _raise)
    signal.alarm(seconds)
    try:
        yield fired
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)

async def _eligible_drawings(db) -> dict[str, list[dict]]:
    """按「工程·专业」分层的候选图（平面类，排除示范工程）。"""
    rows = await db.fetch_all(
        "SELECT d.id, d.title, d.discipline, d.drawing_no, d.file_key, p.name AS proj, "
        "       t.scale_m_pt FROM drawings d JOIN projects p ON p.id = d.project_id "
        "LEFT JOIN drawing_transform t ON t.drawing_id = d.id "
        "WHERE d.file_key ILIKE '%.pdf'")
    strata: dict[str, list[dict]] = {}
    for r in rows:
        short = PROJECT_SHORT.get(r["proj"])
        if short is None or not r["discipline"]:
            continue
        row = dict(r)
        if classify_view_type(row).view_type != "plan":
            continue
        strata.setdefault(f"{short}·{r['discipline']}", []).append(row)
    return strata


def _mark_of(fe, el) -> Mark | None:
    if not fe.scale:
        return None
    return element_mark(el, to_page=lambda x, y: meters_to_page(
        x, y, fe.scale, fe.origin_pt, fe.page_h))


def _render_cell(page, crop, mark: Mark) -> tuple[Image.Image, bool] | None:
    """按原生分辨率渲染裁框，画红框。贴到定尺寸画布上 —— 贴，不缩放。"""
    x0, y0, x1, y1 = crop
    dpi, capped = render_dpi_for_crop(x1 - x0, y1 - y0, cell_px=CELL_PX)
    try:
        pix = render_clip(page, fitz.Rect(x0, y0, x1, y1), dpi)
        im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    except Exception as exc:  # noqa: BLE001 - 单格失败不影响整批，但必须计数
        RENDER_FAILURES[type(exc).__name__] = RENDER_FAILURES.get(type(exc).__name__, 0) + 1
        return None
    canvas = Image.new("RGB", (CELL_PX, CELL_PX), "white")
    canvas.paste(im.crop((0, 0, min(im.width, CELL_PX), min(im.height, CELL_PX))), (0, 0))
    k = dpi / 72.0
    draw = ImageDraw.Draw(canvas)
    if mark.shape == "line":
        (ax, ay), (bx, by) = mark.line
        draw.line([((ax - x0) * k, (ay - y0) * k), ((bx - x0) * k, (by - y0) * k)],
                  fill=(255, 0, 0), width=4)
    else:
        bx0, by0, bx1, by1 = mark.bbox
        draw.rectangle([(bx0 - x0) * k, (by0 - y0) * k, (bx1 - x0) * k, (by1 - y0) * k],
                       outline=(255, 0, 0), width=3)
    return canvas, capped


def _find_blank(page, fe, rng, spec) -> tuple | None:
    """找一处墨迹稀疏（非全白）的地方放**同形**标记，作空白对照。"""
    pr = page.rect
    for _ in range(15):
        cx = rng.uniform(pr.x0 + 0.15 * pr.width, pr.x0 + 0.70 * pr.width)
        cy = rng.uniform(pr.y0 + 0.15 * pr.height, pr.y0 + 0.70 * pr.height)
        if spec["mark"] == "line":
            half = BLANK_LINE_M / fe.scale / 2
            box = Mark("line", (cx - half, cy, cx + half, cy),
                       line=((cx - half, cy), (cx + half, cy)))
        else:
            half = BLANK_BOX_M / fe.scale / 2
            box = Mark("box", (cx - half, cy - half, cx + half, cy + half))
        crop = crop_box_pt(box.bbox, fe.scale, page_w=pr.width, page_h=pr.height,
                           rel=spec["rel"])
        try:
            pix = render_clip(page, fitz.Rect(*crop), 40)
            gray = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
        except Exception:  # noqa: BLE001
            continue
        ink = sum(1 for p in gray.getdata() if p < 200) / max(gray.width * gray.height, 1)
        if 0 < ink < 0.02:
            return box, crop
    return None


async def _scan(db, strata, spec, args, rng):
    """逐层扫图：收候选、记每图候选数（加权要用）、顺手取空白对照。"""
    pool: list[Candidate] = []
    scanned: dict[str, list[int]] = {}
    blanks: list[Cell] = []
    per_blank_stratum = max(1, math.ceil(args.blank / max(len(strata), 1)))
    for stratum, rows in sorted(strata.items()):
        rows = list(rows); rng.shuffle(rows)
        counts: list[int] = []
        got_blank = 0
        for row in rows[:SCAN_PER_STRATUM]:
            did = str(row["id"])
            try:
                with _time_limit(PER_DRAWING_TIMEOUT_SEC) as fired:
                    data = get_file_bytes(row["file_key"])
                    geom = extract_pdf_geometry(data)
                    fe = recognize(geom, row["discipline"], did, drawing_title=row["title"],
                                   scale_override=row["scale_m_pt"], view_type="plan")
                if fired[0]:
                    raise _DrawingTimeout("recognize 吞掉了超时")
                doc = fitz.open(stream=data, filetype="pdf")
            except _DrawingTimeout:
                # 降级必须可见：超时的图记下来，不静默跳过
                print(f"    ⏱ 超时跳过 {row['title'][:30]}（>{PER_DRAWING_TIMEOUT_SEC}s）", flush=True)
                continue
            except Exception:  # noqa: BLE001
                continue
            try:
                page = doc[0]
                elems = getattr(fe, spec["attr"])
                counts.append(len(elems))
                # **每图先抽再渲染**：配额按类分配后一张图能有上千根柱，全渲染
                # 就是上千张 480×480 图（一张图 690MB）。`stratify` 本就每图
                # 至多取 PER_DRAWING 格，先在这里抽等价，且只渲染要用的。
                marks = {i: m for i, el in enumerate(elems) if (m := _mark_of(fe, el))}
                for i in rng.sample(sorted(marks), min(PER_DRAWING, len(marks))):
                    box = marks[i]
                    crop = crop_box_pt(box.bbox, fe.scale, page_w=page.rect.width,
                                       page_h=page.rect.height, rel=spec["rel"])
                    rendered = _render_cell(page, crop, box)
                    if rendered is None:
                        continue
                    pool.append(Candidate(stratum, did, f"{did}:{i}", {
                        "box": box, "crop": crop, "title": row["title"],
                        "image": rendered[0], "capped": rendered[1]}))
                if fe.scale and got_blank < per_blank_stratum and len(blanks) < args.blank:
                    found = _find_blank(page, fe, rng, spec)
                    if found:
                        rendered = _render_cell(page, found[1], found[0])
                        if rendered:
                            blanks.append(Cell("", "blank", stratum, did, row["title"],
                                               found[1], rendered[0], capped=rendered[1]))
                            got_blank += 1
            finally:
                doc.close()
                gc.collect()
        scanned[stratum] = counts
        print(f"  [{stratum}] 扫 {len(counts)} 张 · 候选 {sum(counts)}", flush=True)
    return pool, scanned, blanks


# ── 排版与输出 ────────────────────────────────────────────────────

def _weights(strata, scanned) -> dict[str, float]:
    """每层在语料里的分量 ≈ 该层合格图数 × 扫到的图平均候选数。"""
    out = {}
    for s, rows in strata.items():
        counts = scanned.get(s) or []
        if counts:
            out[s] = len(rows) * (sum(counts) / len(counts))
    return out


def _layout(cells: list[Cell], kept_codes: list[str], seed: int, codes_iter) -> list[list[Cell]]:
    """排页：先给重测对副本预留位置，再把副本放到与原格不同的页上。"""
    n_dup = max(1, round(len(kept_codes) * DUP_FRACTION)) if kept_codes else 0
    n_sheets = max(1, math.ceil((len(cells) + n_dup) / PER_SHEET))
    reserve = math.ceil(n_dup / n_sheets) if n_dup else 0
    # 预留位按页取整会多占：23 格 + 1 副本 → 2 页 × (12−1) = 22 < 23，
    # 下面的 while 就永不退出。加页直到装得下。
    while n_sheets * (PER_SHEET - reserve) < len(cells):
        n_sheets += 1
        reserve = math.ceil(n_dup / n_sheets) if n_dup else 0
    sheets: list[list[Cell]] = [[] for _ in range(n_sheets)]
    si = 0
    for cell in cells:
        while len(sheets[si]) >= PER_SHEET - reserve:
            si = (si + 1) % n_sheets
        sheets[si].append(cell)
        si = (si + 1) % n_sheets
    sheet_of = {c.code: i for i, sh in enumerate(sheets) for c in sh}
    by_code = {c.code: c for c in cells}
    for orig, target in plan_duplicates(kept_codes, sheet_of=sheet_of,
                                        fraction=DUP_FRACTION, n_sheets=n_sheets, seed=seed):
        src = by_code[orig]
        dup = Cell(next(codes_iter), "dup", src.stratum, src.drawing_id, src.title,
                   src.crop_pt, src.image, dup_of=orig, capped=src.capped)
        order = [target] + [i for i in range(n_sheets) if i not in (target, sheet_of[orig])]
        for i in order:
            if len(sheets[i]) < PER_SHEET:
                sheets[i].insert(random.Random(seed + i).randrange(len(sheets[i]) + 1), dup)
                break
    return sheets


def _draw_sheet(cells: list[Cell], font) -> Image.Image:
    sheet = Image.new("RGB", (COLS * CELL_PX, ROWS * CELL_PX), "white")
    dr = ImageDraw.Draw(sheet)
    for i, cell in enumerate(cells):
        x, y = (i % COLS) * CELL_PX, (i // COLS) * CELL_PX
        sheet.paste(cell.image, (x, y))
        dr.rectangle([x, y, x + CELL_PX - 1, y + CELL_PX - 1], outline=(120, 120, 120), width=2)
        dr.rectangle([x + 4, y + 4, x + 118, y + 42], fill=(255, 255, 255), outline=(0, 0, 0))
        dr.text((x + 12, y + 8), cell.code, fill=(0, 0, 0), font=font)
    return sheet


def _batch_text(kind: str, spec: dict, host_dir: str, sheets, n_cells: int) -> str:
    paths = "\n".join(f"{host_dir}/S{i + 1}.png" for i in range(len(sheets)))
    crit = criteria_section(CRITERIA, kind)
    return f"""请打开并逐张识别下面 {len(sheets)} 个本地文件（全部是图片，路径已给全）：

{paths}

===================== 任务 =====================

这是中国建筑施工图（平面图）的局部放大，共 {n_cells} 格。
每格左上角有一个**四位随机编号**（如 NRKK、YPMW），请照抄。

每格里有一个**{spec['mark_word']}**。请判断：**{spec['question']}**

**每格的放大倍数不同** —— 上下文按标记自身大小取；每格都是按原生分辨率
渲染的，没有经过缩放。

--------------- 判据（逐字取自 CRITERIA.md，务必照用）---------------

{crit}
--------------- 每格回答 ---------------

{spec['field']}   true / false
what        答 false 时，说明它到底是什么：
            {spec['what']}
            答 true 时填 ""
saw         一句话，你实际看到了什么
confident   true / false

--------------- 重要 ---------------

**这批格子来自几种不同的来源，我不告诉你哪格来自哪种** ——
请只按图面判断，不要试图推测。其中确实混了一些{spec['mark_word']}画在空白处的格子，
判 `nothing` 是正常且必要的结果。**有少数格子会出现不止一次**（编号不同），
请各自独立判断，不要回头对照。

判 `false` 是完全正常的结果。
**对大批格子给同一个答案要警惕** —— 连续十几格都想答同一个，请重新看图。

===================== 输出 =====================

只输出一个 JSON 数组，每格一个对象，按编号排序，不要其他文字：

[{{"id": "NRKK", "{spec['field']}": true, "what": "", "saw": "...", "confident": true}}, ...]
"""


def _write_outputs(out: Path, sheets, strata, scanned, weights, kind, spec, host_dir, args):
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    rows = ["code\tgroup\tstratum\tdrawing_id\tsheet\tdup_of\tcapped\tcrop_pt\ttitle"]
    for i, sh in enumerate(sheets):
        _draw_sheet(sh, font).save(out / f"S{i + 1}.png")
        for c in sh:
            crop = ",".join(f"{v:.1f}" for v in c.crop_pt)
            rows.append(f"{c.code}\t{c.group}\t{c.stratum}\t{c.drawing_id}\tS{i + 1}\t"
                        f"{c.dup_of}\t{int(c.capped)}\t{crop}\t{c.title}")
    (out / "manifest.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    n_cells = sum(len(s) for s in sheets)
    (out / "BATCH.txt").write_text(_batch_text(kind, spec, host_dir, sheets, n_cells),
                                   encoding="utf-8")
    meta = {"batch": args.batch, "kind": kind, "seed": args.seed,
            "answer_field": spec["field"],
            "criteria": f"CRITERIA.md#{kind}", "cell_px": CELL_PX,
            "strata_drawings": {s: len(r) for s, r in strata.items()},
            "scanned_counts": scanned, "weights": weights,
            "groups": {g: sum(1 for sh in sheets for c in sh if c.group == g)
                       for g in ("kept", "blank", "dup")}}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    return n_cells, meta


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="columns", choices=sorted(KIND_SPEC))
    ap.add_argument("--batch", required=True)
    ap.add_argument("--per-stratum", type=int, default=8)
    ap.add_argument("--blank", type=int, default=8)
    ap.add_argument("--seed", type=int, default=20260911)
    ap.add_argument("--out", default=None)
    ap.add_argument("--host-dir", required=True)
    args = ap.parse_args()
    spec = KIND_SPEC[args.kind]
    out = Path(args.out or f"/tmp/gold_{args.batch}"); out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)

    db = databases_lib.Database(settings.database_url); await db.connect()
    try:
        strata = await _eligible_drawings(db)
        print(f"合格图 {sum(len(v) for v in strata.values())} 张，{len(strata)} 层")
        pool, scanned, blanks = await _scan(db, strata, spec, args, rng)
    finally:
        await db.disconnect()

    pool = [c for c in pool if c.payload.get("image") is not None]
    picked = stratify(pool, per_stratum=args.per_stratum, per_drawing=PER_DRAWING,
                      seed=args.seed)
    codes = iter(make_codes(len(picked) + len(blanks) + 64, seed=args.seed))
    kept = [Cell(next(codes), "kept", c.stratum, c.drawing_id, c.payload["title"],
                 c.payload["crop"], c.payload["image"], capped=c.payload["capped"])
            for c in picked]
    for b in blanks:
        b.code = next(codes)
    cells = kept + blanks
    random.Random(args.seed).shuffle(cells)
    sheets = _layout(cells, [c.code for c in kept], args.seed, codes)
    weights = _weights(strata, scanned)
    n, meta = _write_outputs(out, sheets, strata, scanned, weights,
                             args.kind, spec, args.host_dir, args)
    if RENDER_FAILURES:
        print(f"  ⚠ 渲染失败（按异常类型）：{RENDER_FAILURES}")
    if not kept:
        print(f"\n✗ 被测组 0 格 —— 批次无效，不输出。渲染失败：{RENDER_FAILURES or '无'}")
        return 1
    print(f"\n出 {len(sheets)} 页 {n} 格：{meta['groups']}")
    capped = sum(1 for sh in sheets for c in sh if c.capped)
    if capped:
        print(f"  其中 {capped} 格裁框过小、按 DPI 上限放大渲染（manifest 的 capped 列）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
