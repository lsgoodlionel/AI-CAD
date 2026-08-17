"""轴号圈识别 —— 从**圆圈符号**取轴号,替代从全图 OCR 文本里筛。

## 为什么必须改用圆圈

实测诊断:`category='axis'` 的 43612 条档案数据里,**轴号「位置序 vs 数值序」完全一致的
仅 4/1379 张图(0.3%)**,中位逆序率 0.60(比随机 0.5 还差)——说明这些数据**根本不是
轴号**,而是被误分类的尺寸/编号/图号。从文本里筛无法根治。

**建筑制图标准**:轴线端部画**圆圈**(直径 8–10mm),圈内写轴号。故正确路径是
**先找圈、再读圈内字**,而不是把全图数字当轴号。

## 判别链(单靠检圆不够)

实测单纯检圆:443 个圆里仅 14% 位于图面外围——柱/桩截面也是圆。故叠加三重判据:
1. **半径一致性**:同一张图的轴号圈大小统一 → 取半径众数簇;
2. **圈内有有效轴号文字**:OCR 出 1–2 位数字或字母(柱截面圈内通常无字);
3. **序列单调性**:数字轴号按位置排序后数值应递增(最强校验,可整图否决)。
"""
from __future__ import annotations

import re

#: 轴号圈直径 8–10mm;150dpi 下半径约 24–30px,放宽以容忍线宽/缩放
DEFAULT_MIN_R_PX = 14
DEFAULT_MAX_R_PX = 46
#: 半径众数簇宽度(px):同图轴号圈半径应落在同一窄带内
RADIUS_CLUSTER_PX = 6
#: 圈内 OCR 的裁剪外扩比例(留出边距,避免切到字)
CROP_PADDING_RATIO = 0.25
#: 有效轴号形态:1–2 位数字,或 1–2 位字母(可带撇号,如 A')
_AXIS_LABEL_RE = re.compile(r"^(?:\d{1,2}|[A-Za-z]{1,2}'?)$")
#: 单图最多 OCR 的候选圈数(防超大图爆时间)
MAX_OCR_CANDIDATES = 220
#: 圆去重距离(相对半径):同一轴号圈常被 Hough 重复检出多次
DEDUP_DIST_RATIO = 1.2
#: 圈内 OCR 最低置信:小裁剪块上 OCR 易误识(实测大量圈被读成「2」),须卡置信
MIN_OCR_CONFIDENCE = 0.75
#: 裁剪块放大倍数:150dpi 下轴号圈仅约 50px,字过小 → 放大后再识别
CROP_UPSCALE = 3


def is_axis_label(text: str) -> bool:
    """OCR 文本是否为合法轴号(1–2 位数字/字母)。"""
    return bool(_AXIS_LABEL_RE.match(str(text or "").strip()))


def radius_mode_cluster(
    circles: list[tuple[float, float, float]], width_px: float = RADIUS_CLUSTER_PX,
) -> list[tuple[float, float, float]]:
    """按半径取众数簇 —— 同图轴号圈大小统一,柱/桩截面圆则大小各异。

    circles: [(x, y, r)]。返回落在众数半径带内的圆。
    """
    if not circles:
        return []
    radii = sorted(c[2] for c in circles)
    best_lo, best_count = radii[0], 0
    for lo in radii:
        count = sum(1 for r in radii if lo <= r < lo + width_px)
        if count > best_count:
            best_lo, best_count = lo, count
    return [c for c in circles if best_lo <= c[2] < best_lo + width_px]


def dedup_circles(
    circles: list[tuple[float, float, float]], dist_ratio: float = DEDUP_DIST_RATIO,
) -> list[tuple[float, float, float]]:
    """合并重复检出的同一圆 —— Hough 常在同一轴号圈上给出多个中心相近的圆。

    实测:同一「2」轴号圈被检出 5 次(x=2517/2518/2519…),不去重会让同一轴号
    重复计入,污染序列单调性判断。距离 < 半径×dist_ratio 视为同一圆。
    """
    kept: list[tuple[float, float, float]] = []
    for x, y, r in sorted(circles, key=lambda c: -c[2]):
        dup = False
        for kx, ky, kr in kept:
            if ((x - kx) ** 2 + (y - ky) ** 2) ** 0.5 < max(kr, r) * dist_ratio:
                dup = True
                break
        if not dup:
            kept.append((x, y, r))
    return kept


def monotonic_violation_rate(labeled: list[tuple[str, float]]) -> float | None:
    """数字轴号「位置序 vs 数值序」的逆序对占比;不足 3 个返回 None。

    真实轴网该值应接近 0(1 在 2 左边)。整图偏高说明识别失败,应整体弃用。
    """
    nums = [(int(l), p) for l, p in labeled if str(l).isdigit()]
    if len(nums) < 3:
        return None
    nums.sort(key=lambda t: t[1])
    values = [v for v, _ in nums]
    inversions = sum(
        1 for i in range(len(values)) for j in range(i + 1, len(values))
        if values[i] > values[j]
    )
    total = len(values) * (len(values) - 1) // 2
    return inversions / total if total else None


def crop_box(x: float, y: float, r: float, w: int, h: int) -> tuple[int, int, int, int]:
    """圆 → 裁剪框(含外扩),并夹到图像边界内。"""
    pad = r * (1 + CROP_PADDING_RATIO)
    return (
        max(int(x - pad), 0), max(int(y - pad), 0),
        min(int(x + pad), w), min(int(y + pad), h),
    )


def detect_axis_labels(
    gray_image, backend, warnings: list[str],
    min_r_px: int = DEFAULT_MIN_R_PX, max_r_px: int = DEFAULT_MAX_R_PX,
) -> dict:
    """灰度图 + OCR 后端 → 轴号圈识别结果。

    返回 {ok, labels: [{label, x_px, y_px, r_px}], violation_rate, candidates, reason}。
    ok=False 表示该图轴号识别不可信(逆序率过高或有效轴号太少)。
    """
    from core.model3d.circle_detector import detect_circles_px

    circles = detect_circles_px(gray_image, min_r_px=min_r_px, max_r_px=max_r_px)
    if not circles:
        return {"ok": False, "labels": [], "violation_rate": None,
                "candidates": 0, "reason": "未检出候选圆"}
    cluster = dedup_circles(radius_mode_cluster(circles))[:MAX_OCR_CANDIDATES]
    if not cluster:
        return {"ok": False, "labels": [], "violation_rate": None,
                "candidates": len(circles), "reason": "无一致半径的圆簇"}

    h, w = gray_image.shape[:2]
    labels: list[dict] = []
    for x, y, r in cluster:
        x0, y0, x1, y1 = crop_box(x, y, r, w, h)
        if x1 - x0 < 6 or y1 - y0 < 6:
            continue
        patch = gray_image[y0:y1, x0:x1]
        try:
            import cv2
            import numpy as np
            # 放大后再识别:小图块上 OCR 质量差是主要误识来源
            patch = cv2.resize(patch, None, fx=CROP_UPSCALE, fy=CROP_UPSCALE,
                               interpolation=cv2.INTER_CUBIC)
            rgb = np.stack([patch] * 3, axis=-1) if patch.ndim == 2 else patch
            boxes = backend.recognize(rgb, warnings)
        except Exception:  # noqa: BLE001 — 单圈 OCR 失败不影响整体
            continue
        for text, _bbox, conf in boxes or []:
            if float(conf) < MIN_OCR_CONFIDENCE:
                continue          # 低置信多为误识(实测小块 OCR 大量假「2」)
            if is_axis_label(text):
                labels.append({"label": str(text).strip(), "x_px": float(x),
                               "y_px": float(y), "r_px": float(r),
                               "confidence": float(conf)})
                break

    rate = monotonic_violation_rate([(d["label"], d["x_px"]) for d in labels])
    ok = len(labels) >= 3 and (rate is None or rate <= 0.15)
    return {
        "ok": ok, "labels": labels, "violation_rate": rate,
        "candidates": len(cluster),
        "reason": ("轴号圈识别可信" if ok else
                   f"有效轴号 {len(labels)} 个,逆序率 {rate}"),
    }
