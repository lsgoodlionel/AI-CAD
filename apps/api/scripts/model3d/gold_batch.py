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
from core.model3d.isolation_filter import find_crowded_flags
from core.model3d.geometry_extractor import extract_pdf_geometry
from core.model3d.gold.batch_codes import make_codes
from core.model3d.gold.claim_marks import anchor_mark, axis_segment, elevation_mark
from core.model3d.gold.positives import parse_positives
from core.model3d.render_budget import render_clip
from core.model3d.gold.batch_design import (
    Candidate, Mark, crop_box_pt, criteria_section, element_mark, inset_for_mark,
    plan_duplicates,
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
#: 板的空白对照：6×4 米的矩形多边形（一块小板的量级）。
BLANK_SLAB_M = (6.0, 4.0)

#: 批内重测对占被测格的比例。
DUP_FRACTION = 0.10

PROJECT_SHORT = {"上海大歌剧院": "歌剧院", "轨道交通工程(第二工程交叉验证)": "第二工程"}

#: 每类要问的问题与答案字段。只列已用过的类，别处按需再加（YAGNI）。
#: `rel`：裁框边长 = 构件尺寸 × rel。框状取 4（看得清构件与周边）；线状取 1.6 ——
#: 旧的墙/管生成器按固定 5 米裁，**长墙会被裁到自己的裁框外面**。
KIND_SPEC = {
    "elevations": {"question": "红框圈住的是不是一处标高标注，数值与右上角红字一致？",
                   "field": "is_elevation", "mark": "box", "mark_word": "红色方框",
                   "attr": None, "claims": "elevation", "rel": 8.0,
                   "what": "dimension / room_no / note / pipe_spec / axis / "
                           "value_mismatch / nothing / other"},
    "axis_lines": {"question": "红色虚线压着的位置，是不是一条定位轴线（不是墙线/尺寸线/管线/图框线）？",
                   "field": "is_axis", "mark": "dashed_line", "mark_word": "红色虚线",
                   "attr": None, "claims": "axis", "rel": 1.2,
                   "what": "wall / dimension / pipe / leader / frame / "
                           "hatch / nothing / other"},
    "world_anchors": {"question": "红十字打的是不是右上角那个轴线交点？",
                      "field": "is_anchor", "mark": "cross", "mark_word": "红十字",
                      "attr": None, "claims": "anchor", "rel": 6.0,
                      "what": "wrong_intersection / not_an_intersection / "
                              "no_axis_here / nothing / other"},
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
    "slabs": {"question": "红线圈出的是不是**一块楼板的边界**（圈大了也算错）？",
              "field": "is_slab", "mark": "poly", "mark_word": "红色多边形",
              "attr": "slabs", "rel": 1.3,
              "what": ("room / wall_or_beam / building_outline / opening / sliver / "
                       "section_view / oversized / nothing / other")},
    "equipment": {"question": "框住的是不是一台机电设备（或末端设备符号）？",
                  "field": "is_equipment", "mark": "box", "mark_word": "红色方框",
                  "attr": "equipment", "rel": 4.0,
                  "what": ("column / wall / beam / door / window / furniture / sanitary / "
                           "legend / dimension / text / frame / single_line / nothing / other")},
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
    #: 红标记本身的页面点包围盒 —— 有它才能把一格原样重渲染成正对照
    mark_pt: tuple = ()
    #: 「系统读数」——库里存的那个值（标高 / 坐标 / 轴号）。画在格子右上角，
    #: 判读者要回答的是「图上真有吗、是不是这个数」。构件类为空。
    claim: str = ""
    dup_of: str = ""
    capped: bool = False
    #: 所在图纸的候选总数 —— 层内加权要用（见 `ingest._cell_weights`）；-1 = 未知
    drawing_candidates: int = -1


#: manifest 列。`drawing_candidates` 让回收端能按候选量加权，
#: 不必像 col3 那样事后重跑识别去回填。
MANIFEST_HEADER = ("code\tgroup\tstratum\tdrawing_id\tdrawing_candidates\tsheet"
                   "\tdup_of\tcapped\tcrop_pt\tmark_pt\tclaim\ttitle")


def manifest_row(cell: "Cell", sheet_no: int) -> str:
    """一格 → manifest 的一行（`sheet_no` 从 1 起）。"""
    crop = ",".join(f"{v:.1f}" for v in cell.crop_pt)
    mark = ",".join(f"{v:.2f}" for v in cell.mark_pt) if cell.mark_pt else ""
    return (f"{cell.code}\t{cell.group}\t{cell.stratum}\t{cell.drawing_id}\t"
            f"{cell.drawing_candidates}\tS{sheet_no}\t{cell.dup_of}\t{int(cell.capped)}"
            f"\t{crop}\t{mark}\t{cell.claim}\t{cell.title}")


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


def _mark_of(fe, el, *, as_poly: bool = False) -> Mark | None:
    if not fe.scale:
        return None
    return element_mark(el, as_poly=as_poly, to_page=lambda x, y: meters_to_page(
        x, y, fe.scale, fe.origin_pt, fe.page_h))


def _render_cell(page, crop, mark: Mark) -> tuple[Image.Image, bool] | None:
    """按原生分辨率渲染裁框，画红框。贴到定尺寸画布上 —— 贴，不缩放。"""
    x0, y0, x1, y1 = crop
    dpi, capped = render_dpi_for_crop(x1 - x0, y1 - y0, cell_px=CELL_PX)
    dpi *= inset_for_mark(mark.bbox, crop)      # 贴边的大标记四周留白
    try:
        pix = render_clip(page, fitz.Rect(x0, y0, x1, y1), dpi)
        im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    except Exception as exc:  # noqa: BLE001 - 单格失败不影响整批，但必须计数
        RENDER_FAILURES[type(exc).__name__] = RENDER_FAILURES.get(type(exc).__name__, 0) + 1
        return None
    canvas = Image.new("RGB", (CELL_PX, CELL_PX), "white")
    # 居中贴：裁框可以不是正方形（大标记两向各自封顶），加上贴边留白，
    # 渲染结果常比格子小 —— 贴在中间，四周白边，不缩放
    im = im.crop((0, 0, min(im.width, CELL_PX), min(im.height, CELL_PX)))
    ox, oy = (CELL_PX - im.width) // 2, (CELL_PX - im.height) // 2
    canvas.paste(im, (ox, oy))
    k = dpi / 72.0
    draw = ImageDraw.Draw(canvas)
    x0, y0 = x0 - ox / k, y0 - oy / k           # 标记坐标随贴图偏移
    if mark.shape in ("line", "dashed_line"):
        (ax, ay), (bx, by) = mark.line
        ax, ay = (ax - x0) * k, (ay - y0) * k
        bx, by = (bx - x0) * k, (by - y0) * k
        if mark.shape == "line":
            draw.line([ax, ay, bx, by], fill=(255, 0, 0), width=4)
        else:
            # 画成红虚线，让底图的线型（点划线才是轴线）从缝里看得见
            total = math.hypot(bx - ax, by - ay) or 1.0
            step = _DASH_ON + _DASH_OFF
            pos = 0.0
            while pos < total:
                t0, t1 = pos / total, min(pos + _DASH_ON, total) / total
                draw.line([ax + (bx - ax) * t0, ay + (by - ay) * t0,
                           ax + (bx - ax) * t1, ay + (by - ay) * t1],
                          fill=(255, 0, 0), width=3)
                pos += step
    elif mark.shape == "poly":
        # 闭合折线而不是 polygon(width=)：后者的线宽参数要 Pillow ≥ 9.1
        pts = [((x - x0) * k, (y - y0) * k) for x, y in mark.poly]
        draw.line(pts + pts[:1], fill=(255, 0, 0), width=4, joint="curve")
    elif mark.shape == "cross":
        # 锚点断言的是「就是这一点」：画十字而不是框，否则判读者会去看框里有什么
        (cx, cy), _ = mark.line
        arm = (mark.bbox[2] - mark.bbox[0]) / 2 * k
        cx, cy = (cx - x0) * k, (cy - y0) * k
        draw.line([cx - arm, cy, cx + arm, cy], fill=(255, 0, 0), width=3)
        draw.line([cx, cy - arm, cx, cy + arm], fill=(255, 0, 0), width=3)
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
        elif spec["mark"] == "poly":
            hw, hh = BLANK_SLAB_M[0] / fe.scale / 2, BLANK_SLAB_M[1] / fe.scale / 2
            ring = ((cx - hw, cy - hh), (cx + hw, cy - hh), (cx + hw, cy + hh), (cx - hw, cy + hh))
            box = Mark("poly", (cx - hw, cy - hh, cx + hw, cy + hh), poly=ring)
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


#: 正对照登记表的默认位置（一类一份）。
POSITIVES_DIR = Path(__file__).resolve().parents[2] / "data/model3d/gold/positives"


def _positive_mark(want, shape: str) -> Mark:
    """登记表里的对照格 → 与本类同形的标记（框 / 线 / 十字）。"""
    x0, y0, x1, y1 = want.mark_pt
    if shape in ("line", "dashed_line"):
        return Mark(shape, want.mark_pt, line=((x0, y0), (x1, y1)))
    if shape == "cross":
        return Mark("cross", want.mark_pt, line=(((x0 + x1) / 2, (y0 + y1) / 2),) * 2)
    return Mark("box", want.mark_pt)


async def _render_positives(db, path: Path, shape: str = "box") -> list[Cell]:
    """按登记表把正对照格原样重渲染。

    只用「图纸 + 页面点」，**不跑识别** —— 对照格因此不随识别代码变动，
    永远是判读者之前看过、且已被逐格核验过的同一张图。
    某格取不到图纸就跳过并打印；对照格少了会在回收端被 `positive_control_ok`
    的格数下限拦住，不会静默地把一批没有正对照的结果当成有。
    """
    if not path.exists():
        print(f"  ⚠ 没有正对照登记表 {path} —— 本批只体检「多判」一个方向")
        return []
    wanted = parse_positives(path.read_text(encoding="utf-8"))
    ids = sorted({c.drawing_id for c in wanted})
    rows = await db.fetch_all(
        "SELECT d.id, d.title, d.file_key FROM drawings d WHERE d.id = ANY(:ids)",
        {"ids": ids})
    meta = {str(r["id"]): r for r in rows}
    cells: list[Cell] = []
    for want in wanted:
        row = meta.get(want.drawing_id)
        if row is None:
            print(f"  ⚠ 正对照 {want.source}：图纸 {want.drawing_id} 不在库里，跳过")
            continue
        try:
            doc = fitz.open(stream=get_file_bytes(row["file_key"]), filetype="pdf")
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠ 正对照 {want.source}：取图失败 {type(exc).__name__}，跳过")
            continue
        try:
            rendered = _render_cell(doc[0], want.crop_pt, _positive_mark(want, shape))
        finally:
            doc.close()
            gc.collect()
        if rendered is None:
            print(f"  ⚠ 正对照 {want.source}：渲染失败，跳过")
            continue
        cells.append(Cell("", "pos", "positive", want.drawing_id, row["title"],
                          want.crop_pt, rendered[0], capped=rendered[1],
                          mark_pt=want.mark_pt, claim=want.claim))
    print(f"  正对照 {len(cells)}/{len(wanted)} 格")
    return cells


def _gate_split(elems: list[dict], gate: float | None) -> tuple[list[int], list[int]]:
    """候选下标 → (闸保留, 闸删掉)。不给闸就全部保留 —— 旧批次的抽样逐位不变。"""
    if gate is None:
        return list(range(len(elems))), []
    flags = find_crowded_flags(elems, min_isolation=gate)
    return ([i for i, f in enumerate(flags) if not f], [i for i, f in enumerate(flags) if f])


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
                kept_idx, gated_idx = _gate_split(elems, getattr(args, "gate_isolation", None))
                # 层权重与层内加权按**闸之后**的候选量 —— 那才是用户会看到的
                counts.append(len(kept_idx))
                # **每图先抽再渲染**：配额按类分配后一张图能有上千根柱，全渲染
                # 就是上千张 480×480 图（一张图 690MB）。`stratify` 本就每图
                # 至多取 PER_DRAWING 格，先在这里抽等价，且只渲染要用的。
                as_poly = spec["mark"] == "poly"
                marks = {i: m for i, el in enumerate(elems)
                         if (m := _mark_of(fe, el, as_poly=as_poly))}
                # 闸保留组与闸删组各抽 PER_DRAWING 格；不给闸时闸删组为空、
                # 保留组的抽样与旧版逐位相同（同一 rng 调用、同一排序键）
                for group, idxs in (("kept", kept_idx), ("gated", gated_idx)):
                    usable = sorted(i for i in idxs if i in marks)
                    for i in rng.sample(usable, min(args.per_drawing, len(usable))):
                        box = marks[i]
                        crop = crop_box_pt(box.bbox, fe.scale, page_w=page.rect.width,
                                           page_h=page.rect.height, rel=spec["rel"])
                        rendered = _render_cell(page, crop, box)
                        if rendered is None:
                            continue
                        key = f"{did}:{i}" if group == "kept" else f"{did}:gated:{i}"
                        pool.append(Candidate(stratum, did, key, {
                            "box": box, "crop": crop, "title": row["title"],
                            "mark_pt": tuple(box.bbox),
                            "image": rendered[0], "capped": rendered[1],
                            "group": group, "n_cands": len(idxs)}))
                if fe.scale and got_blank < per_blank_stratum and len(blanks) < args.blank:
                    found = _find_blank(page, fe, rng, spec)
                    if found:
                        rendered = _render_cell(page, found[1], found[0])
                        if rendered:
                            blanks.append(Cell("", "blank", stratum, did, row["title"],
                                               found[1], rendered[0], capped=rendered[1],
                                               mark_pt=tuple(found[0].bbox)))
                            got_blank += 1
            finally:
                doc.close()
                gc.collect()
        scanned[stratum] = counts
        print(f"  [{stratum}] 扫 {len(counts)} 张 · 候选 {sum(counts)}", flush=True)
    return pool, scanned, blanks


#: 「系统读数」类的空白对照尺寸（页面点）。与真格同形同量级 —— 否则判读者
#: 一眼就能把对照组挑出来（旧生成器的教训，见模块文档）。
BLANK_ELEV_PT = (26.0, 12.0)
BLANK_AXIS_PT = 200.0
BLANK_CROSS_PT = 28.0

#: 红虚线的实线段 / 空档（像素）。空档要够宽，底下的点划线才看得出线型。
_DASH_ON, _DASH_OFF = 14.0, 12.0


def _blank_claim(spec: dict, rng) -> str:
    """空白对照也得带一个像样的读数 —— 缺读数本身就是识别对照组的线索。"""
    if spec["claims"] == "elevation":
        return f"{rng.choice([-1, 1]) * rng.randrange(300, 30000) / 1000.0:+.3f}"
    if spec["claims"] == "axis":
        return rng.choice(list("ABCDEFGHJKLMNP")) if rng.random() < 0.5 \
            else str(rng.randrange(1, 30))
    return f"{rng.randrange(1, 30)}x{rng.choice(list('ABCDEFGHJKLMNP'))}"


def _find_blank_claim(page, rng, spec) -> tuple | None:
    """「系统读数」类的空白对照：同形标记打在墨迹稀疏处（页面点，不经比例）。"""
    pr = page.rect
    for _ in range(15):
        cx = rng.uniform(pr.x0 + 0.15 * pr.width, pr.x0 + 0.70 * pr.width)
        cy = rng.uniform(pr.y0 + 0.15 * pr.height, pr.y0 + 0.70 * pr.height)
        if spec["mark"] in ("line", "dashed_line"):
            half = BLANK_AXIS_PT / 2
            if rng.random() < 0.5:
                a, b = (cx - half, cy), (cx + half, cy)
            else:
                a, b = (cx, cy - half), (cx, cy + half)
            mark = Mark(spec["mark"], (min(a[0], b[0]), min(a[1], b[1]),
                                       max(a[0], b[0]), max(a[1], b[1])), line=(a, b))
        elif spec["mark"] == "cross":
            mark = Mark("cross", (cx - BLANK_CROSS_PT, cy - BLANK_CROSS_PT,
                                  cx + BLANK_CROSS_PT, cy + BLANK_CROSS_PT),
                        line=((cx, cy), (cx, cy)))
        else:
            hw, hh = BLANK_ELEV_PT[0] / 2, BLANK_ELEV_PT[1] / 2
            mark = Mark("box", (cx - hw, cy - hh, cx + hw, cy + hh))
        crop = crop_box_pt(mark.bbox, 1.0, page_w=pr.width, page_h=pr.height, rel=spec["rel"])
        try:
            pix = render_clip(page, fitz.Rect(*crop), 40)
            gray = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
        except Exception:  # noqa: BLE001
            continue
        ink = sum(1 for p in gray.getdata() if p < 200) / max(gray.width * gray.height, 1)
        if 0 < ink < 0.02:
            return mark, crop
    return None


#: 「系统读数」类的取数：一张图 → [(标记, 读数文字)]。读数只用 ASCII
#: （容器字体没有中文字形），它会画在格子右上角给判读者对。
_CLAIM_SQL = {
    "elevation": """
        SELECT e.drawing_id, e.value_json->>'elevation_m' AS value,
               e.location_json->'bbox' AS bbox
        FROM drawing_extracted_info e
        WHERE e.is_active AND e.category = 'elevation'
          AND e.location_json ? 'bbox' AND e.drawing_id = ANY(:ids)""",
    "axis": """
        SELECT r.drawing_id, r.page_w, r.page_h, r.axes
        FROM axis_recognition r
        WHERE r.axes IS NOT NULL AND r.drawing_id = ANY(:ids)""",
    "anchor": """
        SELECT r.drawing_id, r.page_w, r.page_h, r.anchors
        FROM axis_recognition r
        WHERE r.anchors IS NOT NULL AND jsonb_array_length(r.anchors) > 0
          AND r.drawing_id = ANY(:ids)""",
}


#: 哪些图有这一类读数 —— 先筛再抽。锚点全库只有 17 张图有，
#: 随机抽 30 张几乎抽不到，不筛就会出一批空批次。
_CLAIM_DRAWINGS_SQL = {
    "elevation": ("SELECT DISTINCT drawing_id FROM drawing_extracted_info "
                  "WHERE is_active AND category = 'elevation' AND location_json ? 'bbox'"),
    "axis": ("SELECT drawing_id FROM axis_recognition "
             "WHERE axes IS NOT NULL AND jsonb_array_length(axes) > 0"),
    "anchor": ("SELECT drawing_id FROM axis_recognition "
               "WHERE anchors IS NOT NULL AND jsonb_array_length(anchors) > 0"),
}


#: 标记落在页面之外的读数条数（按类）。**不能静默丢掉**：这些格子渲染出来
#: 什么标记也没有，判读者会当成「空白」，而真正的问题是系统读数本身出了页面。
OFF_PAGE_MARKS: dict[str, int] = {}


def _mark_on_page(mark, page_w: float, page_h: float) -> bool:
    """标记的中心还在页面里吗。"""
    cx = (mark.bbox[0] + mark.bbox[2]) / 2
    cy = (mark.bbox[1] + mark.bbox[3]) / 2
    return 0 <= cx <= page_w and 0 <= cy <= page_h


def _as_json(value):
    return json.loads(value) if isinstance(value, str) else value


def _claims_of_row(claims: str, row, page_w: float, page_h: float, rng) -> list[tuple]:
    """库里的一行 → 这张图上的 [(Mark, 读数)]。"""
    out: list[tuple] = []
    if claims == "elevation":
        bbox = [float(v) for v in _as_json(row["bbox"])]
        value = float(row["value"])
        out.append((elevation_mark(tuple(bbox), page_w=page_w, page_h=page_h),
                    f"{value:+.3f}"))
    elif claims == "axis":
        for axis in _as_json(row["axes"]) or []:
            if axis.get("offset_pt") is None:
                continue
            # 每条轴线取线上随机一处 —— 固定取中点会让所有格子都落在图纸正中
            mark = axis_segment(float(axis.get("angle_deg") or 0.0),
                                float(axis["offset_pt"]), page_w=page_w, page_h=page_h,
                                at=rng.uniform(0.15, 0.85))
            if mark is not None:
                out.append((mark, str(axis.get("label") or "?")[:8]))
    elif claims == "anchor":
        for anchor in _as_json(row["anchors"]) or []:
            if anchor.get("x_norm") is None:
                continue
            out.append((anchor_mark(float(anchor["x_norm"]), float(anchor["y_norm"]),
                                    page_h=page_h),
                        f"{anchor.get('label_x', '?')}x{anchor.get('label_y', '?')}"[:9]))
    return out


async def _scan_claims(db, strata, spec, args, rng):
    """「系统读数」类的扫图：读数来自库，不跑识别，因此每层可以多扫几张。

    与构件类共用后续流程（分层抽样、加权、排页），差别只在候选从哪来。
    """
    pool: list[Candidate] = []
    scanned: dict[str, list[int]] = {}
    blanks: list[Cell] = []
    per_blank_stratum = max(1, math.ceil(args.blank / max(len(strata), 1)))
    have = {str(r["drawing_id"])
            for r in await db.fetch_all(_CLAIM_DRAWINGS_SQL[spec["claims"]])}
    for stratum, rows in sorted(strata.items()):
        rows = [r for r in rows if str(r["id"]) in have]
        rng.shuffle(rows)
        chosen = {str(r["id"]): r for r in rows[:SCAN_PER_STRATUM * 3]}
        if not chosen:
            scanned[stratum] = []
            print(f"  [{stratum}] 没有一张图有这一类读数", flush=True)
            continue
        found = await db.fetch_all(_CLAIM_SQL[spec["claims"]], {"ids": sorted(chosen)})
        by_drawing: dict[str, list] = {}
        for row in found:
            by_drawing.setdefault(str(row["drawing_id"]), []).append(row)
        counts: list[int] = []
        got_blank = 0
        for did, db_rows in by_drawing.items():
            meta = chosen[did]
            try:
                data = get_file_bytes(meta["file_key"])
                doc = fitz.open(stream=data, filetype="pdf")
            except Exception:  # noqa: BLE001
                continue
            try:
                page = doc[0]
                pw, ph = page.rect.width, page.rect.height
                marks: list[tuple] = []
                for row in db_rows:
                    try:
                        marks.extend(_claims_of_row(spec["claims"], row, pw, ph, rng))
                    except (TypeError, ValueError, KeyError):
                        continue        # 读数畸形的那条跳过，不拖垮整张图
                off = sum(1 for m, _c in marks if not _mark_on_page(m, pw, ph))
                if off:
                    OFF_PAGE_MARKS[spec["claims"]] = OFF_PAGE_MARKS.get(spec["claims"], 0) + off
                counts.append(len(marks) - off)
                marks = [(m, c) for m, c in marks if _mark_on_page(m, pw, ph)]
                for i in rng.sample(range(len(marks)), min(args.per_drawing, len(marks))):
                    mark, claim = marks[i]
                    crop = crop_box_pt(mark.bbox, 1.0, page_w=pw, page_h=ph, rel=spec["rel"])
                    rendered = _render_cell(page, crop, mark)
                    if rendered is None:
                        continue
                    pool.append(Candidate(stratum, did, f"{did}:{i}", {
                        "box": mark, "crop": crop, "title": meta["title"],
                        "mark_pt": tuple(mark.bbox), "claim": claim,
                        "image": rendered[0], "capped": rendered[1],
                        "group": "kept", "n_cands": len(marks)}))
                if got_blank < per_blank_stratum and len(blanks) < args.blank:
                    found_blank = _find_blank_claim(page, rng, spec)
                    if found_blank:
                        rendered = _render_cell(page, found_blank[1], found_blank[0])
                        if rendered:
                            blanks.append(Cell("", "blank", stratum, did, meta["title"],
                                               found_blank[1], rendered[0],
                                               capped=rendered[1],
                                               mark_pt=tuple(found_blank[0].bbox),
                                               claim=_blank_claim(spec, rng)))
                            got_blank += 1
            finally:
                doc.close()
                gc.collect()
        scanned[stratum] = counts
        print(f"  [{stratum}] {len(counts)} 张图 · 读数 {sum(counts)}", flush=True)
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
                   src.crop_pt, src.image, dup_of=orig, capped=src.capped,
                   drawing_candidates=src.drawing_candidates, mark_pt=src.mark_pt,
                   claim=src.claim)
        order = [target] + [i for i in range(n_sheets) if i not in (target, sheet_of[orig])]
        for i in order:
            if len(sheets[i]) < PER_SHEET:
                sheets[i].insert(random.Random(seed + i).randrange(len(sheets[i]) + 1), dup)
                break
    return sheets


def _text_width(draw, text: str, font) -> float:
    """文字像素宽（Pillow 版本之间 API 不同，逐个退回）。"""
    for attr, call in (("textlength", lambda: draw.textlength(text, font=font)),
                       ("getbbox", lambda: font.getbbox(text)[2] - font.getbbox(text)[0]),
                       ("getsize", lambda: font.getsize(text)[0])):
        target = draw if attr == "textlength" else font
        if hasattr(target, attr):
            try:
                return float(call())
            except Exception:  # noqa: BLE001
                continue
    return 16.0 * len(text)


def _draw_sheet(cells: list[Cell], font) -> Image.Image:
    sheet = Image.new("RGB", (COLS * CELL_PX, ROWS * CELL_PX), "white")
    dr = ImageDraw.Draw(sheet)
    for i, cell in enumerate(cells):
        x, y = (i % COLS) * CELL_PX, (i // COLS) * CELL_PX
        sheet.paste(cell.image, (x, y))
        dr.rectangle([x, y, x + CELL_PX - 1, y + CELL_PX - 1], outline=(120, 120, 120), width=2)
        dr.rectangle([x + 4, y + 4, x + 118, y + 42], fill=(255, 255, 255), outline=(0, 0, 0))
        dr.text((x + 12, y + 8), cell.code, fill=(0, 0, 0), font=font)
        if cell.claim:
            # 只用 ASCII —— 容器里没有中文字形，中文会画成豆腐块。
            # 宽度按字体实测，不按「每字若干像素」估：`+55.200` 估窄了会被切成
            # `+55.20(`，判读者拿到的就是一个**错的读数**。
            w = int(_text_width(dr, cell.claim, font)) + 20
            dr.rectangle([x + CELL_PX - w - 4, y + 4, x + CELL_PX - 4, y + 42],
                         fill=(255, 255, 255), outline=(200, 0, 0))
            dr.text((x + CELL_PX - w + 6, y + 8), cell.claim, fill=(200, 0, 0), font=font)
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
判 `nothing` 是正常且必要的结果；**也确实混了一些按上面判据明确成立的格子**，
判 `true` 同样是正常且必要的结果。**有少数格子会出现不止一次**（编号不同），
请各自独立判断，不要回头对照。

判 `true` 和判 `false` 都是完全正常的结果 —— 两个方向都有对照格在查。
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
    rows = [MANIFEST_HEADER]
    for i, sh in enumerate(sheets):
        _draw_sheet(sh, font).save(out / f"S{i + 1}.png")
        rows.extend(manifest_row(c, i + 1) for c in sh)
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
                       for g in ("kept", "gated", "blank", "dup", "pos")},
            "gate_isolation": getattr(args, "gate_isolation", None)}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    return n_cells, meta


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="columns", choices=sorted(KIND_SPEC))
    ap.add_argument("--per-drawing", type=int, default=PER_DRAWING,
                    help=f"每张图至多取几格（缺省 {PER_DRAWING}）。总体很小的类"
                         f"（锚点全库 35 个）把它调大即可全数判读")
    ap.add_argument("--positives", default=None,
                    help=f"正对照登记表（缺省 {POSITIVES_DIR}/<kind>.tsv）")
    ap.add_argument("--batch", required=True)
    ap.add_argument("--per-stratum", type=int, default=8)
    ap.add_argument("--blank", type=int, default=8)
    ap.add_argument("--seed", type=int, default=20260911)
    ap.add_argument("--out", default=None)
    ap.add_argument("--host-dir", required=True)
    ap.add_argument("--gate-isolation", type=float, default=None,
                    help="柱孤立度闸阈值：给了就额外抽「闸删掉的」一组，量误删率")
    args = ap.parse_args()
    if args.gate_isolation is not None and args.kind != "columns":
        ap.error("--gate-isolation 只用于柱（孤立度闸只作用于柱候选）")
    spec = KIND_SPEC[args.kind]
    out = Path(args.out or f"/tmp/gold_{args.batch}"); out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)

    db = databases_lib.Database(settings.database_url); await db.connect()
    try:
        strata = await _eligible_drawings(db)
        print(f"合格图 {sum(len(v) for v in strata.values())} 张，{len(strata)} 层")
        scan = _scan_claims if spec.get("claims") else _scan
        pool, scanned, blanks = await scan(db, strata, spec, args, rng)
        positives = await _render_positives(
            db, Path(args.positives) if args.positives else POSITIVES_DIR / f"{args.kind}.tsv",
            spec["mark"])
    finally:
        await db.disconnect()

    pool = [c for c in pool if c.payload.get("image") is not None]
    picked = stratify([c for c in pool if c.payload.get("group", "kept") == "kept"],
                      per_stratum=args.per_stratum, per_drawing=args.per_drawing,
                      seed=args.seed)
    picked_gated = stratify([c for c in pool if c.payload.get("group") == "gated"],
                            per_stratum=args.per_stratum, per_drawing=args.per_drawing,
                            seed=args.seed)
    codes = iter(make_codes(len(picked) + len(picked_gated) + len(blanks)
                            + len(positives) + 64, seed=args.seed))
    kept = [Cell(next(codes), "kept", c.stratum, c.drawing_id, c.payload["title"],
                 c.payload["crop"], c.payload["image"], capped=c.payload["capped"],
                 drawing_candidates=c.payload["n_cands"], mark_pt=c.payload["mark_pt"],
                 claim=c.payload.get("claim", ""))
            for c in picked]
    gated = [Cell(next(codes), "gated", c.stratum, c.drawing_id, c.payload["title"],
                  c.payload["crop"], c.payload["image"], capped=c.payload["capped"],
                  drawing_candidates=c.payload["n_cands"], mark_pt=c.payload["mark_pt"],
                  claim=c.payload.get("claim", ""))
             for c in picked_gated]
    for b in blanks + positives:
        b.code = next(codes)
    cells = kept + gated + blanks + positives
    random.Random(args.seed).shuffle(cells)
    sheets = _layout(cells, [c.code for c in kept], args.seed, codes)
    weights = _weights(strata, scanned)
    n, meta = _write_outputs(out, sheets, strata, scanned, weights,
                             args.kind, spec, args.host_dir, args)
    if OFF_PAGE_MARKS:
        print(f"  ⚠ 读数落在页面之外、无法判读（已剔除）：{OFF_PAGE_MARKS}")
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
