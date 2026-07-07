"""从机电矢量 PDF 自动构建 YOLO 设备检测数据集（弱监督自动标注）。

标注来源：core.model3d.geometry_extractor 提取矢量矩形/闭合块，
按设备尺寸阈值（0.5~5m，比例尺自动识别）过滤为 equipment 框（单类 class 0）。
输出 ultralytics 标准目录结构（images/labels/train|val + dataset.yaml）。

用法：
    python scripts/build_yolo_dataset.py <机电PDF目录> <输出目录> [--max-pages 80]
"""
from __future__ import annotations

import argparse
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from core.model3d.geometry_extractor import extract_pdf_geometry  # noqa: E402

RENDER_LONG_EDGE = 1280
EQUIPMENT_SIZE_M = (0.5, 5.0)
SCALE_RE = re.compile(r"1[:：]\s*(50|100|150|200|500)")
DEFAULT_SCALE_M_PER_PT = 100 * 0.000352778
MIN_BOXES_PER_PAGE = 2       # 少于 2 框的页面丢弃（标注密度太低）
MAX_BOXES_PER_PAGE = 120     # 超密页面丢弃（线框噪声）
VAL_RATIO = 0.15
SEED = 42


def _page_scale(texts: list) -> float:
    joined = "；".join(t[2] for t in texts)
    match = SCALE_RE.search(joined)
    if match:
        return int(match.group(1)) * 0.000352778
    return DEFAULT_SCALE_M_PER_PT


def _equipment_boxes(geom) -> list[tuple[float, float, float, float]]:
    """设备候选框（页面坐标 x,y,w,h）：矩形 + 闭合多边形包围盒按尺寸过滤。"""
    scale = _page_scale(geom.texts)
    lo, hi = EQUIPMENT_SIZE_M
    boxes: list[tuple[float, float, float, float]] = []

    def _keep(w_pt: float, h_pt: float) -> bool:
        return lo <= w_pt * scale <= hi and lo <= h_pt * scale <= hi

    for x, y, w, h, _filled in geom.rects:
        if _keep(w, h):
            boxes.append((x, y, w, h))
    for poly in geom.polys:
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        if _keep(w, h):
            boxes.append((min(xs), min(ys), w, h))
    return boxes


def _render_page(pdf_path: Path, out_png: Path) -> tuple[float, float] | None:
    import fitz

    try:
        doc = fitz.open(pdf_path)
        page = doc[0]
        zoom = RENDER_LONG_EDGE / max(page.rect.width, page.rect.height)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        pix.save(str(out_png))
        size = (float(page.rect.width), float(page.rect.height))
        doc.close()
        return size
    except Exception as exc:  # noqa: BLE001
        print(f"  渲染失败 {pdf_path.name}: {exc}")
        return None


def build(source_dir: Path, out_dir: Path, max_pages: int) -> None:
    pdfs = sorted(source_dir.rglob("*.pdf"))
    random.Random(SEED).shuffle(pdfs)
    print(f"源目录 PDF 共 {len(pdfs)} 张，目标采集 {max_pages} 页")

    for split in ("train", "val"):
        (out_dir / "images" / split).mkdir(parents=True, exist_ok=True)
        (out_dir / "labels" / split).mkdir(parents=True, exist_ok=True)

    collected = 0
    for pdf_path in pdfs:
        if collected >= max_pages:
            break
        try:
            geom = extract_pdf_geometry(pdf_path.read_bytes())
        except Exception:  # noqa: BLE001
            continue
        if geom.page_w <= 0:
            continue
        boxes = _equipment_boxes(geom)
        if not (MIN_BOXES_PER_PAGE <= len(boxes) <= MAX_BOXES_PER_PAGE):
            continue

        split = "val" if random.Random(collected).random() < VAL_RATIO else "train"
        stem = f"mep_{collected:04d}"
        png_path = out_dir / "images" / split / f"{stem}.png"
        if _render_page(pdf_path, png_path) is None:
            continue

        lines = []
        for x, y, w, h in boxes:
            cx = (x + w / 2) / geom.page_w
            cy = (y + h / 2) / geom.page_h
            lines.append(
                f"0 {cx:.6f} {cy:.6f} {w / geom.page_w:.6f} {h / geom.page_h:.6f}"
            )
        (out_dir / "labels" / split / f"{stem}.txt").write_text("\n".join(lines))
        collected += 1
        print(f"  [{collected}/{max_pages}] {split} {pdf_path.name[:44]} 框数={len(boxes)}")

    (out_dir / "dataset.yaml").write_text(
        f"path: {out_dir}\ntrain: images/train\nval: images/val\n"
        "names:\n  0: equipment\n"
    )
    print(f"完成：{collected} 页 → {out_dir}/dataset.yaml")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--max-pages", type=int, default=80)
    args = parser.parse_args()
    build(args.source, args.output, args.max_pages)
