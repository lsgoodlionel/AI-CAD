"""比例证据的**测量**脚本（先量后改的「量」这一步）。

对给定图纸清单逐张抽取一批**原始特征**（不做任何判断、不定阈值），
落成 JSON 供离线分析。阈值必须从这份实测分布里长出来，
而不是先拍脑袋定一个再去找数据支持 —— 项目里 `MAX_BANDS=40`
误杀 451 张核心平面图就是反面教材。

用法：
    python -m scripts.model3d.probe_scale_evidence <manifest.tsv> <out.json>
    python -m scripts.model3d.probe_scale_evidence --all <out.json> [上限]

manifest.tsv 需含表头且第一列是 ref、第三列是 drawing_id（由
`scale_gold_manifest.py` 产出）。`--all` 则扫全库有变换记录的图。
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import sys

import databases as databases_lib

from core.config import settings
from core.storage import get_file_bytes

#: 图上明写比例的**宽口径**正则：分母任意整数。
#: 识别器现用的 `_SCALE_RE` 只认 50/100/150/200/500 五个值，
#: 这里放宽是为了先量出「图上到底写了什么」，不是直接采信。
SCALE_TEXT_RE = re.compile(r"1\s*[:：]\s*(\d{1,7})")

#: 近似平行线对的搜索窗口（pt）。40pt 在 1:100 下约合 14 米，
#: 足以覆盖墙厚（0.2~0.4m）到轴距（6~9m）之间的全部尺度。
PAIR_GAP_MAX_PT = 40.0
#: 判为「同向」的角度容差（度）。
PARALLEL_TOL_DEG = 2.0

#: PDF 页坐标里 y 向下增长；`_densest_cell` 用的 `page.rect` 同向，
#: 而 `extract_pdf_geometry` 输出的是**显示坐标系**（同向）。两者可直接比。


def _percentiles(values: list[float], ps: tuple[float, ...]) -> list[float]:
    if not values:
        return [0.0] * len(ps)
    s = sorted(values)
    out = []
    for p in ps:
        idx = min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))
        out.append(round(s[idx], 4))
    return out


def _pair_gaps(lines: list[tuple[float, float, float, float]]) -> list[float]:
    """同向且相互投影有重叠的线段之间的法向间距（pt）。

    墙的平面表达是两条平行线；间距即墙厚。这里不区分墙/梁/其它，
    只把间距分布量出来 —— 是不是墙由分布形状说话。
    """
    horiz: list[tuple[float, float, float]] = []   # (y, x0, x1)
    vert: list[tuple[float, float, float]] = []    # (x, y0, y1)
    for x0, y0, x1, y1 in lines:
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length < 2.0:
            continue
        angle = abs(math.degrees(math.atan2(dy, dx))) % 180.0
        if angle < PARALLEL_TOL_DEG or angle > 180.0 - PARALLEL_TOL_DEG:
            horiz.append(((y0 + y1) / 2, min(x0, x1), max(x0, x1)))
        elif abs(angle - 90.0) < PARALLEL_TOL_DEG:
            vert.append(((x0 + x1) / 2, min(y0, y1), max(y0, y1)))
    gaps: list[float] = []
    for group in (horiz, vert):
        group.sort()
        for i, (pos_a, a0, a1) in enumerate(group):
            for pos_b, b0, b1 in group[i + 1:]:
                gap = pos_b - pos_a
                if gap > PAIR_GAP_MAX_PT:
                    break
                if gap < 0.3:
                    continue
                overlap = min(a1, b1) - max(a0, b0)
                if overlap > max(2.0, gap):
                    gaps.append(gap)
    return gaps


def _median_spacing(positions: list[float]) -> float:
    if len(positions) < 2:
        return 0.0
    gaps = sorted(b - a for a, b in zip(positions, positions[1:]))
    return gaps[len(gaps) // 2] if gaps else 0.0


def _densest_cell_rect(data: bytes) -> tuple[float, float, float, float] | None:
    """复算金标准批次裁的那一格（`mk_scale._densest_cell` 同口径），返回 pt 矩形。

    **为什么要它**：判读者看的是这一格，不是整张图。一张图上常同时画着
    1:100 的平面和 1:10 的节点详图 —— 整张图的统计量无法预测「这一格里
    8 米的红线像不像 8 米」。要跟金标准可比，特征就得量在同一格上。
    """
    try:
        import fitz
        from PIL import Image as _Image

        page = fitz.open(stream=data, filetype="pdf")[0]
        pix = page.get_pixmap(dpi=PROBE_DPI)
        img = _Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
        w, h = img.size
        best, best_ink = (0, 0), -1.0
        for gy in range(max(1, int(GRID * (1 - EXCLUDE_BOTTOM)))):
            for gx in range(max(1, int(GRID * (1 - EXCLUDE_RIGHT)))):
                cell = img.crop((gx * w // GRID, gy * h // GRID,
                                 (gx + 1) * w // GRID, (gy + 1) * h // GRID))
                ink = sum(1 for p in cell.getdata() if p < 200) / max(cell.width * cell.height, 1)
                if ink > best_ink:
                    best_ink, best = ink, (gx, gy)
        gx, gy = best
        r = page.rect
        cw, ch = r.width / GRID, r.height / GRID
        return (r.x0 + gx * cw, r.y0 + gy * ch,
                r.x0 + (gx + 1) * cw, r.y0 + (gy + 1) * ch)
    except Exception:  # noqa: BLE001 — 复算不出就不给这一组特征
        return None


#: `mk_scale.py` 裁格用的常量（复算必须逐字一致）。
PROBE_DPI, GRID = 24, 3
EXCLUDE_RIGHT, EXCLUDE_BOTTOM = 0.28, 0.18


def features_for(data: bytes, *, title: str) -> dict:
    """一张图的原始特征（纯测量，无判断）。"""
    from core.model3d import extract_pdf_geometry
    from core.model3d.element_recognizer import _detect_axes

    geom = extract_pdf_geometry(data)
    texts = list(geom.texts or [])
    all_text = " ".join(t[2] for t in texts)
    axis_x, axis_y, _trunc = _detect_axes(
        geom.lines, geom.page_w, geom.page_h, texts)

    lengths = [math.hypot(x1 - x0, y1 - y0) for x0, y0, x1, y1 in geom.lines]
    xs = [c for x0, y0, x1, y1 in geom.lines for c in (x0, x1)]
    ys = [c for x0, y0, x1, y1 in geom.lines for c in (y0, y1)]
    gaps = _pair_gaps(list(geom.lines))

    cell = _densest_cell_rect(data)
    cell_lines = ([ln for ln in geom.lines if _in_rect(*ln, cell)]
                  if cell else [])
    cell_lengths = [math.hypot(x1 - x0, y1 - y0) for x0, y0, x1, y1 in cell_lines]
    cell_gaps = _pair_gaps(cell_lines)

    return {
        "title": title,
        "page_w": round(geom.page_w, 2),
        "page_h": round(geom.page_h, 2),
        "n_lines": len(geom.lines),
        "n_rects": len(geom.rects),
        "n_polys": len(geom.polys),
        "n_texts": len(texts),
        "text_len": len(all_text),
        # 图上明写的比例：全部命中及其出现次数
        "scale_text_hits": _hit_counts(all_text),
        "axis_x": len(axis_x),
        "axis_y": len(axis_y),
        "axis_labeled": sum(1 for lb, _ in (*axis_x, *axis_y)
                            if str(lb or "").strip()),
        "axis_sp_x_pt": round(_median_spacing([p for _l, p in axis_x]), 4),
        "axis_sp_y_pt": round(_median_spacing([p for _l, p in axis_y]), 4),
        "axis_gaps_x_pt": [round(g, 3) for g in _sorted_gaps(axis_x)][:60],
        "axis_gaps_y_pt": [round(g, 3) for g in _sorted_gaps(axis_y)][:60],
        "content_w_pt": round(max(xs) - min(xs), 2) if xs else 0.0,
        "content_h_pt": round(max(ys) - min(ys), 2) if ys else 0.0,
        "len_pct_pt": _percentiles(lengths, (0.1, 0.25, 0.5, 0.75, 0.9, 0.99)),
        "n_pair_gaps": len(gaps),
        "gap_pct_pt": _percentiles(gaps, (0.1, 0.25, 0.5, 0.75, 0.9)),
        "gap_hist_pt": _hist(gaps),
        # 原始样本：留给离线分析试各种统计量，避免为换一个统计量重跑全批
        "gap_sample_pt": [round(g, 3) for g in _sample(gaps, 4000)],
        "len_sample_pt": [round(v, 3) for v in _sample(lengths, 4000)],
        # --- 判读那一格（与金标准同口径）---
        "cell_rect_pt": None if cell is None else [round(v, 2) for v in cell],
        "cell_n_lines": len(cell_lines),
        "cell_len_sample_pt": [round(v, 3) for v in _sample(cell_lengths, 3000)],
        "cell_gap_sample_pt": [round(g, 3) for g in _sample(cell_gaps, 3000)],
    }


def _in_rect(x0, y0, x1, y1, rect) -> bool:
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return rect[0] <= cx <= rect[2] and rect[1] <= cy <= rect[3]


def _sample(values: list[float], cap: int) -> list[float]:
    """等间隔抽样到 cap 个 —— 固定步长，可复现。"""
    if len(values) <= cap:
        return list(values)
    step = len(values) / cap
    return [values[int(i * step)] for i in range(cap)]


def _hit_counts(all_text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for m in SCALE_TEXT_RE.finditer(all_text):
        out[m.group(1)] = out.get(m.group(1), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1])[:12])


def _sorted_gaps(axes: list[tuple[str, float]]) -> list[float]:
    positions = sorted(p for _l, p in axes)
    return [b - a for a, b in zip(positions, positions[1:])]


def _hist(values: list[float]) -> dict[str, int]:
    """0.5pt 一档，只保留最高的 12 档 —— 看间距有没有集中的众数。"""
    out: dict[str, int] = {}
    for v in values:
        key = f"{math.floor(v * 2) / 2:.1f}"
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1])[:12])


async def main() -> int:
    args = sys.argv[1:]
    db = databases_lib.Database(settings.database_url)
    await db.connect()

    if args and args[0] == "--all":
        out_path = args[1]
        limit = int(args[2]) if len(args) > 2 else 100000
        rows = await db.fetch_all(
            "SELECT t.drawing_id, t.scale_m_pt, t.confidence, d.title, d.file_key "
            "FROM drawing_transform t JOIN drawings d ON d.id=t.drawing_id "
            f"ORDER BY t.drawing_id LIMIT {limit}")
        todo = [(str(r["drawing_id"]), "", float(r["scale_m_pt"]),
                 float(r["confidence"] or 0.0), r["title"], r["file_key"])
                for r in rows]
    else:
        man_path, out_path = args[0], args[1]
        wanted = []
        for line in open(man_path).read().splitlines()[1:]:
            parts = line.split("\t")
            wanted.append((parts[0], parts[2]))
        todo = []
        for ref, did in wanted:
            r = await db.fetch_one(
                "SELECT t.scale_m_pt, t.confidence, d.title, d.file_key "
                "FROM drawing_transform t JOIN drawings d ON d.id=t.drawing_id "
                "WHERE t.drawing_id=:d", {"d": did})
            if r is None:
                continue
            todo.append((did, ref, float(r["scale_m_pt"]),
                         float(r["confidence"] or 0.0), r["title"], r["file_key"]))
    await db.disconnect()

    out = {}
    for i, (did, ref, scale, conf, title, key) in enumerate(todo, 1):
        try:
            data = get_file_bytes(key)
            feat = features_for(data, title=str(title or ""))
        except Exception as exc:  # noqa: BLE001 — 取不到就记原因，不中断整批
            feat = {"error": str(exc)[:200]}
        feat.update({"ref": ref, "scale_m_pt": scale, "confidence": conf})
        out[did] = feat
        if i % 25 == 0:
            print(f"  {i}/{len(todo)}", flush=True)
    json.dump(out, open(out_path, "w"), ensure_ascii=False, indent=1)
    print(f"写出 {out_path}（{len(out)} 张）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
