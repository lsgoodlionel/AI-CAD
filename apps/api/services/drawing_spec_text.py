"""从档案碎片重建成篇图纸说明。

**为什么需要**：档案里存的是 OCR 的**行级碎片**（实测 249303 条 `note`
平均 14 字符），样本里能直接看到跨行截断——「位应结合试成桩试验的
结果提供详尽的…」开头缺字（原句是「监理单位应结合…」）。
而需求要的是「**完整**把文字全部识别出来」，且这份说明是
「后期建模和审图中所有内容的总要求和验证起点」。

**为什么能从档案重建**：碎片带 bbox、置信 0.92~0.99。
按 Phase E 的「抽取一次·单一真相源」，重建不该再跑一遍 OCR。

重建三步：碎片 → 分栏（图纸说明常排多栏，不分栏左右会串）
→ 栏内按 y 拼接 → 有说明标题的栏才算说明块。
"""
from __future__ import annotations

import json
import re
from typing import Any

from core.model3d.reading_order import detect_columns
from core.model3d.spec_frame import contains, enclosing_frame

#: 标题最长字数——超过就是正文而非标题。
MAX_HEADING_CHARS = 16

#: 「说明」前的限定词最多几个字。实测 `结构设计总说明` 前缀 5 字是真标题，
#: 而 `同时满足产品说明` 前缀 6 字是正文——这条界线是量出来的，不是拍的。
MAX_HEADING_PREFIX_CHARS = 5

#: 出现即判为正文的句读。标题至多带一个尾冒号。
_SENTENCE_MARKS = "。，、；！？,;!?"

#: 交叉引用词。正文里的「详见总说明」「按设计说明」极多（实测占含
#: 「说明」碎片的大半），误判一次就凭空多出一个空说明块。
_CROSS_REF_RE = re.compile(r"[详见按参照依据据满足及和与]")

#: `说明` 结尾（可带序号/冒号）——`设计说明1`、`技术说明：` 都是真标题。
_NOTE_TAIL_RE = re.compile(r"^(?P<prefix>[\u4e00-\u9fa5]*)总?说明\d{0,2}[:：]?$")

#: 其余固定形态的标题。
_FIXED_HEADING_RES = (
    re.compile(r"^注[:：]$"),
    re.compile(r"^[\u4e00-\u9fa5]{0,4}技术要求[:：]?$"),
    # 中文数字序号开头的章节标题：一、总则 / 十二，土方开挖：
    # **序号后的部分不得带句末标点**——正文换行片段常以「八，」开头，
    # 实测「八，不能上翻。」被误判成标题。
    re.compile(r"^[一二三四五六七八九十]{1,3}[、，,．.]\s*[^。！？!?]{1,12}[:：]?$"),
)


#: 中文序号章节标题（`_FIXED_HEADING_RES` 的第三条）。
#: 单列出来是因为句读检查要为它开口子——「十二，土方开挖：」本身带逗号。
_SECTION_HEADING_RE = _FIXED_HEADING_RES[2]


def is_note_heading(text: str | None) -> bool:
    """判断一行是不是说明块的标题。

    判据来自**实测的真实标题分布**（`说明：`385 次、`设计说明`81、
    `技术说明：`33、`修改说明：`、`设计说明1`…），固定前缀表覆盖不了，
    所以改为「短前缀 + 说明」。

    必须挡住正文里的交叉引用——`详见总说明`、`按设计说明`这类在档案里
    比真标题还多，误判一次就凭空多出一个空说明块。
    """
    line = (text or "").strip()
    if not line or len(line) > MAX_HEADING_CHARS:
        return False
    body = line[:-1] if line[-1] in "：:" else line
    if any(mark in body for mark in _SENTENCE_MARKS) and not _SECTION_HEADING_RE.match(line):
        return False
    match = _NOTE_TAIL_RE.match(line)
    if match:
        prefix = match.group("prefix")
        return (len(prefix) <= MAX_HEADING_PREFIX_CHARS
                and not _CROSS_REF_RE.search(prefix))
    return any(pattern.match(line) for pattern in _FIXED_HEADING_RES)


def _xy_of(location: Any) -> tuple[float, float] | None:
    """取 (x, y)。

    **档案层有两种位置结构**（`reading_order._xy_of` 的注释早写了）：
    OCR 存 `{"bbox": [x0,y0,x1,y1]}`、矢量文字存 `{"x":…, "y":…}`。
    只认 bbox 会丢掉 **317045 条**矢量文字碎片（占三类总量的 33%），
    而重建说明恰恰最需要矢量文字——它没有 OCR 误差。

    另外 **`location_json` 可能是 JSON 字符串**（驱动对 jsonb 的处理），
    真库上不解析会直接 `'str' object has no attribute 'get'`。
    坏 JSON 只丢这一条，不能让整张图的重建炸掉。
    """
    if isinstance(location, str):
        try:
            location = json.loads(location)
        except (ValueError, TypeError):
            return None
    if not isinstance(location, dict):
        return None
    bbox = location.get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 2:
        try:
            return float(bbox[0]), float(bbox[1])
        except (TypeError, ValueError):
            return None
    try:
        return float(location["x"]), float(location["y"])
    except (KeyError, TypeError, ValueError):
        return None


#: 不进说明重建的档案分类。**标题栏碎片必须排除**——实测图名叫
#: 「设计说明7」的图，重建出的「正文」全是标题栏字段
#: （DRAWING TITLE / 工程编号 / 图号…）。图名与说明标题文本上
#: 无法区分，但标题栏的碎片本就不属于说明。
#: 说明块在档案层的分类名。
SPEC_CATEGORY = "spec_text"

#: 还必须排除 `spec_text` 自身——重建读的是「这张图的全部档案行」，
#: 其中包含上一轮写进去的说明块，于是正文被重复追加，
#: **实测每跑一次翻一倍**。自产的输出不能再当输入。
EXCLUDED_CATEGORIES = frozenset({"title_block", "title_block_label",
                                 SPEC_CATEGORY})


def tokens_from_archive(rows: list[Any] | None) -> list[dict]:
    """档案行 → 带坐标的 token。

    没有位置的碎片**直接丢弃**：它们无法参与排序，
    留着只会把顺序搅乱，而顺序错了整篇说明就读不成句子。
    """
    tokens: list[dict] = []
    for row in rows or []:
        data = dict(row)
        if data.get("category") in EXCLUDED_CATEGORIES:
            continue
        text = (data.get("content") or "").strip()
        xy = _xy_of(data.get("location_json"))
        if not text or xy is None:
            continue
        tokens.append({"text": text, "x": xy[0], "y": xy[1]})
    return tokens


#: 正文平均行长下限（字/行）。**实测**：图名叫「设计说明N」的图会拼出
#: 3.6 字/行 × 29 行 的块——那是标题栏的字段格。384 块实测分布
#: P10=5.6、P20=9.4，噪声全在最底部，取 6.0 只切掉明显噪声。
#: 不用 category 判是因为同一张图的标题栏碎片散落在
#: room_name / title_block_label / other 三个分类，分类不可靠。
MIN_AVG_LINE_CHARS = 6.0

#: 说明块内相邻行的最大垂直间距（pt）。超过就认为不属于同一块。
#: 图纸上一栏能横跨整张图，不设上限会把半张图的文字都吞进来。
MAX_LINE_GAP_PT = 60.0


def _blocks_in_column(column: list[dict],
                      frames: list[dict] | None = None) -> list[dict]:
    """一栏 → 若干说明块。

    **标题在栏内任意位置**：真实图纸一栏有上千碎片，
    要求「栏首即标题」时三张样图全部检出 0 块。
    每个标题向下取，遇到下一个标题或过大的垂直间距就收口。
    """
    ordered = sorted(column, key=lambda t: (t["y"], t["x"]))
    blocks: list[dict] = []
    index = 0
    while index < len(ordered):
        if not is_note_heading(ordered[index]["text"]):
            index += 1
            continue
        head = ordered[index]
        # **边框优先于间距启发式**：边框是制图者画出来的真实边界，
        # 而「间距超过 60pt」只是猜。没有边框时 `contains` 恒真，
        # 退回原有行为。
        frame = enclosing_frame(frames, head["x"], head["y"])
        body: list[dict] = []
        cursor = index + 1
        previous_y = head["y"]
        while cursor < len(ordered):
            token = ordered[cursor]
            if is_note_heading(token["text"]):
                break
            if not contains(frame, token["x"], token["y"]):
                break
            if frame is None and token["y"] - previous_y > MAX_LINE_GAP_PT:
                break
            body.append(token)
            previous_y = token["y"]
            cursor += 1
        if body:                      # 只有标题没正文的多半是误判
            lines = [t["text"] for t in body]
            avg_line_chars = round(
                sum(len(line) for line in lines) / len(lines), 1)
            if avg_line_chars < MIN_AVG_LINE_CHARS:
                index = cursor        # 标题栏字段格，不是说明
                continue
            blocks.append({
                "title": head["text"].strip(),
                "text": "\n".join(lines),
                # 记在块上而不是丢掉——人审要能看到「这块为什么可疑」。
                "avg_line_chars": avg_line_chars,
                # 记下由多少条碎片拼成：人工复核要能回溯，
                # 也是「重建有没有吃掉内容」的检查手段。
                "fragment_count": len(body) + 1,
                "x": min(t["x"] for t in [head] + body),
                "y": head["y"],
            })
        index = cursor
    return blocks


def assemble_spec_blocks(tokens: list[dict] | None,
                         frames: list[dict] | None = None) -> list[dict]:
    """带坐标的 token → 说明块列表。

    **必须先分栏**：图纸说明常排成多栏，不分栏会把左右栏交替串成乱码。
    没有说明标题的栏不返回——图上的房间名、尺寸、轴号都会落进来，
    混进去会把「说明」这个概念稀释掉。
    """
    items = list(tokens or [])
    if not items:
        return []
    columns = detect_columns(items) or [items]
    blocks: list[dict] = []
    for column in columns:
        blocks.extend(_blocks_in_column(column, frames))
    return sorted(blocks, key=lambda b: (b["x"], b["y"]))


# ── 落库 ──────────────────────────────────────────────────────

#: 抽取器标识——与 OCR/矢量碎片区分开，便于回溯与重跑。
SPEC_EXTRACTOR = "spec_assembler"

#: **只删 auto**。删整个分类会把人工修正一并冲掉——实测 verify 写入后
#: 重跑单图，verified 记录消失只剩 auto，需求「调整后直接参与后期模型
#: 和审图」就此落空。
_DELETE_SPEC_SQL = f"""
DELETE FROM drawing_extracted_info
WHERE drawing_id = CAST(:drawing_id AS uuid)
  AND category = '{SPEC_CATEGORY}'
  AND (source_kind IS NULL OR source_kind = 'auto')
"""

_INSERT_SPEC_SQL = """
INSERT INTO drawing_extracted_info (
    project_id, drawing_id, category, content,
    value_json, location_json, extractor, confidence, extraction_version
)
VALUES (
    CAST(:project_id AS uuid), CAST(:drawing_id AS uuid), :category, :content,
    CAST(:value_json AS jsonb), CAST(:location_json AS jsonb),
    :extractor, :confidence, :extraction_version
)
"""


def spec_entry_params(project_id: str, drawing_id: str, block: dict,
                      version: int = 1) -> dict:
    """说明块 → 入库参数。

    标题与行长统计随块保存：人审面板要按它分流，
    建模/审图消费时也要知道这块说明的来源与可信度。
    """
    return {
        "project_id": project_id,
        "drawing_id": drawing_id,
        "category": SPEC_CATEGORY,
        "content": block.get("text", ""),
        "value_json": json.dumps({
            "title": block.get("title"),
            "fragment_count": block.get("fragment_count"),
            "avg_line_chars": block.get("avg_line_chars"),
            # **位置也放进 value_json**：`normalized_key` 只拿得到
            # `(category, content, value_json)`，位置只存 location_json
            # 时 auto 算出的 key 没有位置，与人审行配不上对——
            # 实测重跑后 auto 复活，生效值变成 2 条。
            "x": block.get("x"),
            "y": block.get("y"),
        }, ensure_ascii=False),
        "location_json": json.dumps({"x": block.get("x"), "y": block.get("y")}),
        "extractor": SPEC_EXTRACTOR,
        # 重建出的说明不是直接观测——置信度交给人审层定，这里不编造。
        "confidence": None,
        "extraction_version": version,
    }


async def persist_spec_text(db: Any, *, project_id: str, drawing_id: str,
                            blocks: list[dict], version: int = 1) -> int:
    """落库单图的说明块，返回写入条数。

    **只删 `spec_text` 这一类**：档案层通用的 `persist_drawing_info`
    是整图覆盖式（先 DELETE 全部再插），用它会把这张图上千条碎片
    全删掉——而说明正是从碎片重建来的。

    没重建出说明时也要执行删除：否则判据改进后，被新判据否掉的
    旧说明会永远留在库里（E1.5 的 supersedes 教训）。

    但**只删 auto**：人工修正必须活过重建，否则需求「调整后的信息
    可以直接参与后期模型和审图」就落空了。
    """
    await db.execute(_DELETE_SPEC_SQL, {"drawing_id": drawing_id})
    for block in blocks or []:
        await db.execute(_INSERT_SPEC_SQL,
                         spec_entry_params(project_id, drawing_id, block, version))
    return len(blocks or [])


def frames_from_drawings(drawings: list | None) -> list[dict]:
    """PyMuPDF 的矢量图形 → 能当说明框的矩形。

    **只读几何、不渲染**，所以整项目跑一遍的代价可控
    （对比：OCR 每张图 10~40 秒）。

    线段（零宽/零高）与字号大小的小方框都排除——
    前者不是框，后者是表格单元或符号，用它定边界会把块碎成几十片。
    """
    from core.model3d.spec_frame import MIN_FRAME_SIDE_PT

    frames: list[dict] = []
    for item in drawings or []:
        rect = item.get("rect") if isinstance(item, dict) else getattr(item, "rect", None)
        if rect is None:
            continue
        try:
            x0, y0 = float(rect.x0), float(rect.y0)
            x1, y1 = float(rect.x1), float(rect.y1)
        except (AttributeError, TypeError, ValueError):
            continue
        if (x1 - x0) < MIN_FRAME_SIDE_PT or (y1 - y0) < MIN_FRAME_SIDE_PT:
            continue
        frames.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1})
    return frames
