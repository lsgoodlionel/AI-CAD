"""识图标准资料清单 —— **单一来源**。

这批资料（`/Users/lionel/work/识图标准`）是识图/建模能力的**上位依据**：
此前 `drawing_conventions.CLAUSES` 里的条款是人凭记忆写的，
`component_mark._MARK_KINDS` 的代号表也是手抄的 —— 都没有可回溯的原件。
本模块把「哪本书 / 什么编号 / 多少页 / 文字怎么取 / 凭什么这样断定」
一次登记清楚，后续所有抽取、标注、条款校订都回指这里。

**辨识依据必须留痕**：扫描件的书名不是从文件名猜的，是 OCR 封面读出来的
（文件名 `std_191290.pdf` 完全不含书名）。每条记 `identified_by`。

**分级不是拍脑袋**：`priority` 依据实测——档案层里真实图纸引用的图集编号
统计（`drawing_extracted_info` 全库正则），G101 系列合计 299 次为最高频。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

#: 资料根目录。用户提供的原始资料，只读，不搬运不改名。
SOURCE_ROOT = Path("/Users/lionel/work/识图标准")

#: 文字来路。与 `regulation_books.extract_method` 同口径（migration 048）。
#: text_layer=PDF 文本层直取 / ocr=扫描件识别 / epub=结构化 HTML
ExtractMethod = str


@dataclass(frozen=True)
class KnowledgeSource:
    """一份资料的登记项。"""

    key: str                        # 稳定标识（目录名 / 主键），不含中文与空格
    filename: str                   # SOURCE_ROOT 下的相对路径
    std_no: str | None              # 图集号 / 标准号；教材为 None
    title: str                      # 全称
    kind: str                       # atlas=标准设计图集 / textbook=教材
    discipline: str                 # structure/architecture/mep/general
    pages: int                      # 实测页数
    extract_method: ExtractMethod
    identified_by: str              # 书名怎么断定的（OCR 封面 / 文件内文本层 / 文件名）
    priority: int                   # 1=最高。依据见模块 docstring
    supersedes: str | None = None   # 本图集替代了谁（封面明示）
    superseded_by: str | None = None  # 本清单内被谁替代
    notes: str = ""
    evidence: dict = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return SOURCE_ROOT / self.filename

    @property
    def is_scanned(self) -> bool:
        return self.extract_method == "ocr"


#: 全部 14 份资料。**页数与 extract_method 均为实测**（PyMuPDF 抽样中位数）：
#: 图集全部是每页一张 1-bit 全页扫描图，其中 8 本带一层质量参差的 OCR 文本层
#: （22G101-1 第 8 页实测为 `'=眩目刑图罩坝'` 级乱码），故一律重新 OCR，
#: 不采信原文本层。
SOURCES: tuple[KnowledgeSource, ...] = (
    # ---------- 图集：混凝土结构平法（引用最高频，实测 299 次）----------
    KnowledgeSource(
        key="22G101-1", filename="平法图集/22G101-1.pdf", std_no="22G101-1",
        title="混凝土结构施工图平面整体表示方法制图规则和构造详图"
              "（现浇混凝土框架、剪力墙、梁、板）",
        kind="atlas", discipline="structure", pages=138, extract_method="ocr",
        identified_by="OCR 封面 + 页内文本层（原文本层可读性差，仅用于交叉印证）",
        priority=1,
        notes="component_mark._MARK_KINDS 的权威上游。柱/墙/梁/板代号在此定义。",
        evidence={"archive_citations": {"16G101": 155, "11G101": 48,
                                        "16G101-1": 42, "16G101-2": 42,
                                        "16G101-3": 12}},
    ),
    KnowledgeSource(
        key="22G101-2", filename="平法图集/22G101-2.pdf", std_no="22G101-2",
        title="混凝土结构施工图平面整体表示方法制图规则和构造详图"
              "（现浇混凝土板式楼梯）",
        kind="atlas", discipline="structure", pages=83, extract_method="ocr",
        identified_by="OCR 封面", priority=1,
        notes="楼梯代号 AT~FT 等在此定义。金标准实测「墙」最大误检源正是楼梯（31%），"
              "现有 _MARK_KINDS 完全没有楼梯代号。",
    ),
    KnowledgeSource(
        key="22G101-3", filename="平法图集/22G101-3.pdf", std_no="22G101-3",
        title="混凝土结构施工图平面整体表示方法制图规则和构造详图"
              "（独立基础、条形基础、筏形基础、桩基础）",
        kind="atlas", discipline="structure", pages=121, extract_method="ocr",
        identified_by="OCR 封面", priority=1,
        notes="基础/承台/桩代号。E3-B 围护桩圆检测（+2705 桩）缺的正是编号侧证据。",
    ),
    KnowledgeSource(
        key="23G101-11", filename="平法图集/23G101-11.pdf", std_no="23G101-11",
        title="G101 系列图集常见问题答疑图解",
        kind="atlas", discipline="structure", pages=142, extract_method="ocr",
        identified_by="OCR 封面", priority=2, supersedes="17G101-11",
    ),
    # ---------- 图集：砌体 / 钢结构 ----------
    KnowledgeSource(
        key="22G614-1", filename="平法图集/22G614-1.pdf", std_no="22G614-1",
        title="砌体填充墙结构构造",
        kind="atlas", discipline="structure", pages=49, extract_method="ocr",
        identified_by="OCR 封面", priority=2, supersedes="12G614-1",
    ),
    KnowledgeSource(
        key="12SG620", filename="平法图集/std_191290.pdf", std_no="12SG620",
        title="砌体结构设计与构造",
        kind="atlas", discipline="structure", pages=99, extract_method="ocr",
        identified_by="OCR 封面（文件名 std_191290 不含任何书名线索）", priority=3,
        notes="全书无文本层，逐页仅一行水印『本资料限内部使用』。",
    ),
    KnowledgeSource(
        key="20G108-3", filename="平法图集/20G108-3.pdf", std_no="20G108-3",
        title="《钢结构设计标准》图示",
        kind="atlas", discipline="structure", pages=91, extract_method="ocr",
        identified_by="OCR 封面", priority=2,
        notes="component_labels 的 steel 关键词目前是手写的，此书是其上游。",
    ),
    # ---------- 图集：建筑专业 ----------
    KnowledgeSource(
        key="20J813", filename="平法图集/20J813.pdf", std_no="20J813",
        title="《民用建筑设计统一标准》图示",
        kind="atlas", discipline="architecture", pages=156, extract_method="ocr",
        identified_by="OCR 封面", priority=2, supersedes="06SJ813",
    ),
    KnowledgeSource(
        key="06SJ813", filename="平法图集/std_927.pdf", std_no="06SJ813",
        title="《民用建筑设计通则》图示",
        kind="atlas", discipline="architecture", pages=102, extract_method="ocr",
        identified_by="OCR 封面（文件名 std_927 不含书名）", priority=4,
        superseded_by="20J813",
        notes="已被 20J813 替代。仍抽取以便比对差异，但条款冲突时以 20J813 为准。",
    ),
    KnowledgeSource(
        key="24J804", filename="平法图集/24J804.pdf", std_no="24J804",
        title="民用建筑工程总平面初步设计、施工图设计深度图样",
        kind="atlas", discipline="architecture", pages=57, extract_method="ocr",
        identified_by="OCR 封面 + 页内文本层（本书原文本层质量最好，可交叉校验）",
        priority=1, supersedes="05J804",
        notes="『图纸深度』正是本平台主题。是图纸完整性审查的直接依据。",
    ),
    # ---------- 教材 ----------
    KnowledgeSource(
        key="textbook-lu-jianzhu-shitu",
        filename="建筑识图 (闾成德) (z-library.sk, 1lib.sk, z-lib.sk).pdf",
        std_no=None, title="建筑识图（闾成德，国家职业资格培训教材第 2 版）",
        kind="textbook", discipline="general", pages=321,
        extract_method="text_layer",
        identified_by="PDF 文本层 + 219 条书签目录", priority=1,
        notes="真文本层 + 完整多级目录，制图原理最系统的一本。",
    ),
    KnowledgeSource(
        key="textbook-zhao-zhitu-shitu",
        filename="建筑工程制图与识图 (赵建军) (z-library.sk, 1lib.sk, z-lib.sk).epub",
        std_no=None, title="建筑工程制图与识图（赵建军）",
        kind="textbook", discipline="general", pages=171,
        extract_method="epub",
        identified_by="EPUB OPF 元数据 + 171 个 HTML 章节", priority=1,
        notes="结构化最好：171 HTML + 370 JPG，图文已配对。pages 记的是章节数。",
    ),
    KnowledgeSource(
        key="textbook-shitu-yusuan",
        filename="建筑工程识图与预算快学一本通 (《建筑工程识图与预算快学一本通》编委会编,"
                 " 《建筑工程识图与预算快学一本通》编委会编 etc.)"
                 " (z-library.sk, 1lib.sk, z-lib.sk).pdf",
        std_no=None, title="建筑工程识图与预算快学一本通",
        kind="textbook", discipline="general", pages=247, extract_method="ocr",
        identified_by="OCR 封面（PDF 无任何文本层）", priority=3,
        notes="识图 + 算量，可与 QTO 模块相互印证。",
    ),
    KnowledgeSource(
        key="textbook-dianqi-shitu",
        filename="看范例快速识读建筑电气工程图 (《年范例快速识读建筑电气工程图》编委会编,"
                 " 看范例快速识读建筑电气工程图编委会编 etc.)"
                 " (z-library.sk, 1lib.sk, z-lib.sk).pdf",
        std_no=None, title="看范例快速识读建筑电气工程图",
        kind="textbook", discipline="mep", pages=74, extract_method="ocr",
        identified_by="OCR 封面（PDF 无任何文本层）", priority=2,
        notes="唯一的机电专业资料。金标准实测管线识别 0%（有把握口径），"
              "电气图例是补这块短板的起点。",
    ),
)

_BY_KEY = {s.key: s for s in SOURCES}


def get(key: str) -> KnowledgeSource:
    return _BY_KEY[key]


def all_sources(*, kind: str | None = None,
                max_priority: int | None = None) -> list[KnowledgeSource]:
    """按类型/优先级筛选，结果按 (priority, key) 稳定排序。"""
    out = [s for s in SOURCES
           if (kind is None or s.kind == kind)
           and (max_priority is None or s.priority <= max_priority)]
    return sorted(out, key=lambda s: (s.priority, s.key))


def total_pages(sources: list[KnowledgeSource] | None = None) -> int:
    return sum(s.pages for s in (sources if sources is not None else SOURCES))


def missing_files() -> list[str]:
    """清单里登记但磁盘上找不到的 —— 绝不静默跳过。"""
    return [s.filename for s in SOURCES if not s.path.exists()]
