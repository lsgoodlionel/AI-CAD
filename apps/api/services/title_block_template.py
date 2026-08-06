"""图框字段区域记忆库(人工框一次 → 系统记住 → 自动套用到同版式图纸)。

**为什么**:图框版式在同一套图里是统一的,「专业」栏永远在同一个位置。
按标签找值(`title_block_discipline`)对付得了大多数图,但**标签本身没被 OCR 出来**
的图就彻底读不到(实测 140 张)。这时人在图上**框一次**那个区域,系统就能:

1. 立刻读出这张图的值;
2. **把这个区域记成模板**;
3. 自动套用到**同版式**的其他图纸(本项目优先,跨项目也能复用)。

**同版式怎么判**:图框位置与页面尺寸绑定,故用**页面宽高比**分桶。区域坐标统一
除以页高归一化,与 `drawing_transform`/人工轴线同口径,换算分辨率不影响。

**记忆的复用顺序**:本项目模板 → 全局模板(按命中次数降序)。人越用越准。
"""
from __future__ import annotations

from typing import Any

#: 支持记忆的字段 → 值归一化方式
SUPPORTED_FIELDS = ("discipline", "drawing_no", "title")

_ASPECT_PRECISION = 2      # 宽高比分桶精度:0.01 足以区分 A0 横/A1 竖等常见图幅
_ROW_TOLERANCE = 0.006     # 区域内文本归行容差(归一化单位)


def aspect_bucket(page_w: float, page_h: float) -> float | None:
    """页面宽高比分桶键——同一桶视为同版式图框。"""
    if not page_w or not page_h or page_h <= 0:
        return None
    return round(float(page_w) / float(page_h), _ASPECT_PRECISION)


#: 有效框选的最小边长——按**实际点尺寸**而非归一化比例。
#:
#: 曾按归一化 0.01 拍死,结果超大图直接废掉:实测 5084×2412pt 的图上,
#: 「给排水」那一格归一化只有 0.0046×0.0030,一框就报「区域为空」。
#: 字段格的物理尺寸与页幅无关(永远是几毫米),所以门槛必须用 pt。
MIN_REGION_PT = 3.0

#: 归一化门槛的兜底(不知道页高时用),取得足够松以免误杀
MIN_REGION_SIDE_FALLBACK = 0.0008

#: 两个模板区域「算同一个」的中心距阈值——反复框同一格会攒出一堆近重复模板,
#: 逐个试会把批量套用拖慢数倍
TEMPLATE_DEDUPE_TOLERANCE = 0.02


def normalize_region(
    x1: float, y1: float, x2: float, y2: float,
    page_h_pt: float | None = None,
) -> tuple[float, float, float, float] | None:
    """框选两点 → 规范矩形(左上/右下);退化或过小则无效。

    页高已知时按 `MIN_REGION_PT` 换算门槛——同样一格,A1 图与 A0 加长图的
    归一化尺寸差好几倍,只有按物理尺寸判才不会误杀大图。
    """
    left, right = sorted((float(x1), float(x2)))
    top, bottom = sorted((float(y1), float(y2)))
    floor = (MIN_REGION_PT / page_h_pt) if page_h_pt and page_h_pt > 0 \
        else MIN_REGION_SIDE_FALLBACK
    if right - left < floor or bottom - top < floor:
        return None
    return left, top, right, bottom


def dedupe_templates(
    templates: list[dict], tol: float = TEMPLATE_DEDUPE_TOLERANCE,
) -> list[dict]:
    """合并中心接近的模板,保留排序在前的(命中次数高的)。

    人反复框同一格会攒出一堆近重复模板;批量套用逐个试,每个都要做一次区域重识别
    (实测每次数秒),不去重会把耗时放大数倍。
    """
    kept: list[dict] = []
    for tpl in templates:
        cx = (float(tpl["x1"]) + float(tpl["x2"])) / 2
        cy = (float(tpl["y1"]) + float(tpl["y2"])) / 2
        if any(abs(cx - (float(k["x1"]) + float(k["x2"])) / 2) <= tol
               and abs(cy - (float(k["y1"]) + float(k["y2"])) / 2) <= tol
               for k in kept):
            continue
        kept.append(tpl)
    return kept


def texts_in_region(
    items: list[tuple[str, list[float]]],
    region: tuple[float, float, float, float],
    page_h: float,
) -> str:
    """区域内的图框文本 → 按「自上而下、自左而右」拼成一串。

    同一行的 token 以空格分隔、换行以 `\n` 分隔——**分隔符不能省**,否则框选时
    连标签一起框进来会粘成「图号IP-41-30」,无法剔除标签。
    items 的 bbox 是页面点坐标(pt),region 是归一化坐标,故按 page_h 换算比较。
    """
    if not page_h or page_h <= 0:
        return ""
    left, top, right, bottom = region
    picked: list[tuple[float, float, str]] = []
    for text, b in items:
        if len(b) < 4 or not text:
            continue
        cx, cy = (b[0] + b[2]) / 2 / page_h, (b[1] + b[3]) / 2 / page_h
        if left <= cx <= right and top <= cy <= bottom:
            picked.append((cy, cx, text))
    if not picked:
        return ""
    picked.sort()
    parts: list[str] = []
    prev_y = picked[0][0]
    for cy, _cx, text in picked:
        if parts:
            parts.append("\n" if cy - prev_y > _ROW_TOLERANCE else " ")
        parts.append(text)
        prev_y = cy
    return "".join(parts).strip()


def normalize_region_text(
    raw: str, field: str, learned: dict | None = None,
) -> str | None:
    """区域原文 → 该字段的规范值;不合法返回 None(宁缺勿错,交人确认)。

    档案文本与区域重识别文本共用这套校验,保证两条路径判定一致。

    learned(学习闭环):已采纳的 OCR 纠错先还原糊字,词表扩充让新词也能认出来
    ——这是「采纳即生效」的落点之一。
    """
    from services.title_block_discipline import (
        has_cjk, is_field_label, normalize_discipline,
    )

    if not raw:
        return None
    if learned:
        from services.learned_rules import apply_ocr_corrections
        raw = apply_ocr_corrections(raw, learned.get("ocr_correction") or {})
    # 框选难免带进字段标签本身(「专业 给排水」),逐段剔除标签
    segments = [seg for seg in raw.split() if seg and not is_field_label(seg)]
    cleaned = " ".join(segments).strip()
    if not cleaned:
        return None

    if field == "discipline":
        compact = cleaned.replace(" ", "")
        got = normalize_discipline(compact)
        if got is None and learned:
            # 学到的新专业词:词表里没有但人反复确认过的,现在能认了
            from services.learned_rules import extra_vocabulary
            got = extra_vocabulary(learned).get(compact)
        return got
    if field == "drawing_no":
        no = cleaned.replace(" ", "")
        return None if has_cjk(no) or not (3 <= len(no) <= 32) else no
    if field == "title":
        return cleaned if 2 <= len(cleaned) <= 200 else None
    return None


def value_from_region(
    items: list[tuple[str, list[float]]],
    region: tuple[float, float, float, float],
    page_h: float,
    field: str,
) -> str | None:
    """区域内的档案文本 → 该字段的规范值。"""
    return normalize_region_text(texts_in_region(items, region, page_h), field)


# ── 仓储 ─────────────────────────────────────────────────────────

_INSERT_SQL = """
INSERT INTO title_block_templates
    (project_id, field, x1, y1, x2, y2, page_aspect, source_drawing_id, created_by)
VALUES (CAST(:project_id AS uuid), :field, :x1, :y1, :x2, :y2, :aspect,
        CAST(:source AS uuid), CAST(:created_by AS uuid))
RETURNING id
"""

_FIND_SQL = """
SELECT id, project_id, field, x1, y1, x2, y2, page_aspect, hit_count
FROM title_block_templates
WHERE field = :field
  AND (page_aspect IS NULL OR CAST(:aspect AS real) IS NULL
       OR abs(page_aspect - CAST(:aspect AS real)) < 0.02)
ORDER BY (project_id = CAST(:project_id AS uuid)) DESC, hit_count DESC, created_at DESC
LIMIT 20
"""

_BUMP_SQL = """
UPDATE title_block_templates
SET hit_count = hit_count + :n, last_used_at = now() WHERE id = CAST(:id AS uuid)
"""


async def save_template(
    db: Any, *, project_id: str, field: str,
    region: tuple[float, float, float, float], page_aspect: float | None,
    source_drawing_id: str, created_by: str | None,
) -> str | None:
    """记住一个人工框选的字段区域。"""
    row = await db.fetch_one(_INSERT_SQL, {
        "project_id": project_id, "field": field,
        "x1": region[0], "y1": region[1], "x2": region[2], "y2": region[3],
        "aspect": page_aspect, "source": source_drawing_id,
        "created_by": created_by})
    return str(row["id"]) if row is not None else None


async def find_templates(
    db: Any, *, project_id: str, field: str, page_aspect: float | None,
) -> list[dict]:
    """取可用模板:本项目优先,其次全局;同版式(宽高比接近)才给。"""
    rows = await db.fetch_all(_FIND_SQL, {
        "project_id": project_id, "field": field, "aspect": page_aspect})
    return [dict(r) for r in rows]


async def bump_hits(db: Any, template_id: str, n: int = 1) -> None:
    """模板命中计数 +n——用得越多排得越前,这就是「记忆」的强化。"""
    if n > 0:
        await db.execute(_BUMP_SQL, {"id": template_id, "n": n})


# ── 区域重识别(档案里那格没有文字时的兜底)────────────────────────

#: 小区域重识别的分辨率:图框字号小,300dpi 才认得准(全图 OCR 用 120~200)
REGION_OCR_DPI = 300


def ocr_region(
    pdf_bytes: bytes, region: tuple[float, float, float, float], *,
    dpi: int = REGION_OCR_DPI,
) -> str:
    """裁出框选区域**高分辨率重识别**。

    为什么需要:全图 OCR 会漏——实测 39 张图有「专业」标签却读不出值,
    因为那格的字压根没被识别出来。这时区域里没有任何档案文本,框选也读不到。
    裁小图 + 提高 dpi 能把这类救回来。任何依赖缺失/异常 → 返回 ""(降级)。
    """
    try:
        import fitz

        from core.model3d.ocr.service import _select_backend

        backend = _select_backend([])
        if backend is None:
            return ""
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            if doc.page_count < 1:
                return ""
            page = doc[0]
            page_h = float(page.rect.height)
            clip = fitz.Rect(
                region[0] * page_h, region[1] * page_h,
                region[2] * page_h, region[3] * page_h,
            )
            if clip.is_empty or clip.width < 1 or clip.height < 1:
                return ""
            pix = page.get_pixmap(dpi=dpi, clip=clip)
            from PIL import Image
            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

        tokens = backend.recognize(image, [])
        texts = [t[0] for t in tokens if t and t[0]]
        return " ".join(texts).strip()
    except Exception:  # noqa: BLE001 — 重识别失败降级为「读不到」
        return ""
