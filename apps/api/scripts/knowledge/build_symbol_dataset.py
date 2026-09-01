#!/usr/bin/env python3
"""图例表 → 符号切图 + 标注清单（训练标记数据）。

流程：定位候选页 → 判旋转 → 检表线 → 切单元格 → 认表头 → 逐行配对
→ 裁符号图 + 写 manifest。

**旋转方向不猜、试出来**：横排表印在竖开本上，转 90° 有两个方向。
判据不是几何直觉，而是**转完之后能不能认出表头** —— 认得出的那个方向
才是对的。认不出就两个方向都放弃，如实记 `no_header`。

用法：
    python scripts/knowledge/build_symbol_dataset.py --all
    python scripts/knowledge/build_symbol_dataset.py --key textbook-shitu-yusuan --page 36
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.knowledge import legend_table as lt, source_registry as sr  # noqa: E402
from scripts.knowledge.ocr_cache import load_cache                    # noqa: E402

OUT_ROOT = (Path(__file__).resolve().parents[2] / "data" / "model3d"
            / "dataset" / "reference")

DPI = 200

#: 候选页判据：出现「图例/符号/代号」等词达到此次数。
#: 阈值低会把正文里偶然提到「图例」的页拉进来，高会漏掉只有表头的页。
#: 取 2 —— 表头 + 表题至少两处。
CANDIDATE_KEYWORD_MIN = 2

_KEYWORD_RE = re.compile(r"图例|代号|符号|图形符号")

#: 表题：`表 2-10 表示常用建筑材料的图例`
_TABLE_CAPTION_RE = re.compile(r"表\s*([0-9]+(?:[\-‐‑–—.][0-9]+)*)[\s　]*(.{0,40})")


def candidate_pages(key: str) -> list[dict]:
    rows = load_cache(key)
    return [r for r in rows
            if len(_KEYWORD_RE.findall(r["text"])) >= CANDIDATE_KEYWORD_MIN]


def _binarize(img):
    import cv2

    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) if img.ndim == 3 else img
    # 扫描件明暗不均，用自适应阈值；取反使「墨=255」，形态学才好做。
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                 cv2.THRESH_BINARY_INV, 31, 15)


def _render(page, dpi: int):
    import numpy as np

    pix = page.get_pixmap(dpi=dpi)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
    return img[:, :, :3] if pix.n >= 3 else np.repeat(img, 3, axis=2)


def _tokens_px(cached_tokens: list[dict], dpi: int) -> list[dict]:
    """缓存里的 token bbox 是页面点，换算到图像像素。"""
    s = dpi / 72.0
    out = []
    for t in cached_tokens:
        b = t.get("b") or []
        if len(b) >= 4:
            out.append({"t": t.get("t", ""), "b": [v * s for v in b]})
    return out


def _ocr_image(img) -> list[dict]:
    """对已旋转的图像重新识别 —— 旋转后原缓存的 bbox 全部作废。"""
    from core.knowledge.text_extract import _ocr_engine, _poly_to_bbox

    result = _ocr_engine()(img)
    if result is None or not getattr(result, "txts", None):
        return []
    boxes = getattr(result, "boxes", None)
    out = []
    for k, text in enumerate(result.txts):
        if boxes is None or k >= len(boxes):
            continue
        out.append({"t": (text or "").strip(), "b": _poly_to_bbox(boxes[k])})
    return out


def process_page(source, page, cached: dict, *, dpi: int = DPI) -> tuple[list, dict]:
    """一页 → (标注列表, 供裁图用的图像与元信息)。"""
    import cv2
    import numpy as np

    img = _render(page, dpi)
    tokens = _tokens_px(cached.get("tokens") or [], dpi)
    rotated = lt.is_rotated(cached.get("tokens") or [])

    attempts: list[tuple[bool, object, list[dict]]] = []
    if rotated:
        # 两个方向都试；**认得出表头的那个方向才算对**。
        for code in (cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE):
            rimg = cv2.rotate(img, code)
            attempts.append((True, rimg, _ocr_image(rimg)))
    attempts.append((False, img, tokens))

    best: tuple[int, list, object, bool] = (-1, [], img, False)
    for is_rot, image, toks in attempts:
        binary = _binarize(image)
        entries: list = []
        # 一页常有并排的多个表 —— 逐块建网格，块内坐标再平移回整页坐标，
        # 裁图时才不会错位。
        for table_index, (bx0, by0, bx1, by1) in enumerate(lt.detect_tables(binary)):
            roi = binary[by0:by1, bx0:bx1]
            ys, xs = lt.detect_grid(roi)
            if len(ys) < 3 or len(xs) < 2:
                continue
            # **只收中心落在本块内的 token**。不过滤的话，右表的文字
            # 平移后会落进左表的格子里（实测「金属」串成左表第 2 行的名称）。
            roi_tokens = []
            for tok in toks:
                b = tok.get("b") or []
                if len(b) < 4:
                    continue
                cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
                if not (bx0 <= cx <= bx1 and by0 <= cy <= by1):
                    continue
                roi_tokens.append({"t": tok["t"],
                                   "b": [b[0] - bx0, b[1] - by0,
                                         b[2] - bx0, b[3] - by0]})
            cells = lt.build_cells(roi, ys, xs, roi_tokens)
            found = lt.extract_entries(source.key, cached["index"], table_index,
                                       cells, rotated=is_rot, dpi=dpi)
            entries.extend(
                type(e)(**{**e.__dict__,
                           "symbol_bbox": (e.symbol_bbox[0] + bx0,
                                           e.symbol_bbox[1] + by0,
                                           e.symbol_bbox[2] + bx0,
                                           e.symbol_bbox[3] + by0)})
                for e in found)
        if len(entries) > best[0]:
            best = (len(entries), entries, image, is_rot)

    if best[0] <= 0:
        return [], {"reason": "no_header", "rotated_guess": rotated}
    return best[1], {"image": best[2], "rotated": best[3]}


def table_caption(text: str) -> str:
    m = _TABLE_CAPTION_RE.search(text or "")
    return (m.group(0).strip() if m else "")


def main() -> int:
    import fitz
    from PIL import Image

    ap = argparse.ArgumentParser()
    ap.add_argument("--key", action="append", default=[])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--page", type=int, action="append", default=[])
    ap.add_argument("--out", default=str(OUT_ROOT))
    ap.add_argument("--dpi", type=int, default=DPI)
    args = ap.parse_args()
    logging.disable(logging.INFO)

    keys = args.key or ([s.key for s in sr.all_sources() if s.is_scanned]
                        if args.all else [])
    if not keys:
        ap.error("需要 --key 或 --all")

    out_root = Path(args.out)
    img_dir = out_root / "symbols"
    img_dir.mkdir(parents=True, exist_ok=True)
    manifest = out_root / "symbols_manifest.jsonl"
    written, skipped = 0, []

    with manifest.open("w", encoding="utf-8") as fh:
        for key in keys:
            source = sr.get(key)
            pages = candidate_pages(key)
            if args.page:
                pages = [p for p in pages if p["index"] + 1 in args.page]
            if not pages:
                continue
            doc = fitz.open(source.path)
            try:
                for cached in pages:
                    entries, info = process_page(source, doc[cached["index"]],
                                                 cached, dpi=args.dpi)
                    if not entries:
                        skipped.append((key, cached["index"] + 1,
                                        info.get("reason", "?")))
                        continue
                    caption = table_caption(cached["text"])
                    array = info["image"]
                    for entry in entries:
                        x0, y0, x1, y1 = entry.symbol_bbox
                        crop = array[y0:y1, x0:x1]
                        if crop.size == 0:
                            continue
                        rel = f"symbols/{entry.entry_id.replace('/', '__')}.png"
                        Image.fromarray(crop).save(out_root / rel)
                        row = entry.to_dict()
                        row.update({"image": rel, "table_caption": caption,
                                    "discipline": source.discipline,
                                    "std_no": source.std_no,
                                    "book_title": source.title})
                        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                        written += 1
                    print(f"  {key} p{cached['index'] + 1}: {len(entries)} 条"
                          f"{' [旋转]' if info.get('rotated') else ''}", flush=True)
            finally:
                doc.close()

    print(f"\n共写出 {written} 条符号标注 → {manifest}")
    if skipped:
        print(f"未出数据的候选页 {len(skipped)} 个（如实记录，未强行出数）：")
        for key, page, reason in skipped[:15]:
            print(f"  {key} p{page} — {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
