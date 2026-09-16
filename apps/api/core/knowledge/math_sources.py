"""数理化教材清单 —— 合理性分析的**数学/物理上位依据**。

**为什么要有它**：`core/model3d/plausibility/` 的规则里写着「鞋带公式」
「Jordan 曲线定理」「i=b/√12」这些名字，但它们此前**只是名字** ——
没有一处可回溯的原文。规范那一侧已经立了规矩（`codes.py` 每条限值带条款号
与原文摘录，查不到就标 `UNVERIFIED` 不编数），数学这一侧不该是例外：
一条判据若说不出「凭哪条定理、在哪本书第几页」，它和拍脑袋没有区别。

**这批书能给什么、不能给什么**（先说清楚，免得事后失望）：

- **能给**：几何与力学的定理、公式、算法及其**成立条件** ——
  行列式即面积/体积、凸包、点在多边形内、多边形裁剪对凸性的要求、
  最小二乘与 RANSAC、刚体与碰撞、截面几何量。
- **不能给**：中国规范的限值表。轴压比、长细比容许值、跨厚比、混凝土
  强度设计值都在 GB 50010/50017/50011，通用教材里没有，
  `codes.py` 里那四条 `UNVERIFIED` **不会因为这批书而被填上**。

**版权**：与识图标准那批同一条纪律 —— 原件是用户本地的出版物，
派生全文不进版本库（`.gitignore`），仓库里只留管线代码、清单、
以及**短引用**（公式与其出处页码）。
"""
from __future__ import annotations

from pathlib import Path

from core.knowledge.source_registry import KnowledgeSource

#: 资料根目录。用户提供，只读，不搬运不改名。
SOURCE_ROOT = Path("/Users/lionel/work/02 book/数理化")

#: 派生全文的缓存目录（不进版本库）。
CACHE_DIRNAME = "math_physics"


def _source(key: str, filename: str, title: str, pages: int, method: str,
            discipline: str, priority: int, notes: str, **kw) -> KnowledgeSource:
    return KnowledgeSource(
        key=key, filename=filename, std_no=f"KB-{key}", title=title,
        kind="textbook", discipline=discipline, pages=pages,
        extract_method=method,
        identified_by="文件名 + PDF 文本层抽样（本批为电子版，非扫描件时文本层可直取）",
        priority=priority, notes=notes, root=SOURCE_ROOT, **kw)


#: 七本技术书。**页数与文本层可用性均为实测**（PyMuPDF 抽三页的字符数）：
#: 除 Knight《Physics》外，抽样页文本均在 500~3000 字符，可直取文本层；
#: Knight 抽样三页**全部为 0 字符** —— 是扫描件，要 OCR 才有正文。
SOURCES: tuple[KnowledgeSource, ...] = (
    _source(
        "strang-la",
        "Introduction to Linear Algebra (Ed 5) (Gilbert Strang) "
        "(z-library.sk, 1lib.sk, z-lib.sk).pdf",
        "Introduction to Linear Algebra, 5th Edition (Gilbert Strang)",
        584, "text_layer", "math", 1,
        "行列式即面积/体积（§5.3 Cramer's Rule, Inverses, and Volumes）—— "
        "鞋带公式与自交环正负相消的上位依据；最小二乘是变换求解的依据。",
        evidence={"sample_chars": [2348, 1856, 2146]}),
    _source(
        "roads-geometry",
        "Roads to Geometry, 3rd Edition (Edward C. Wallace, Stephen F. West) "
        "(z-library.sk, 1lib.sk, z-lib.sk).pdf",
        "Roads to Geometry, 3rd Edition (Wallace & West)",
        532, "text_layer", "geometry", 1,
        "公理几何 + 解析几何。凸性、点与多边形的关系、度量的定义在此。",
        evidence={"sample_chars": [1269, 1913, 2073]}),
    _source(
        "pbm-animation",
        "Foundations of Physically Based Modeling and Animation "
        "(Donald H. House, John C. Keyser) (z-library.sk, 1lib.sk, z-lib.sk).epub",
        "Foundations of Physically Based Modeling and Animation (House & Keyser)",
        36, "epub", "physics", 1,
        "刚体、碰撞检测与相互穿透、数值积分 —— `support.interpenetration` "
        "与几何核的上位依据。**按 EPUB spine 的「节」引用而不是页** ——"
        "EPUB 没有固定页，fitz 重排出来的页码随版面设置变，引不住。"
        "登记页数记 **36 节**（spine 实测）而不是 fitz 的 412 页 —— "
        "按 412 登记会让统计报出「缺页 376」这种假警报。",
        evidence={"sample_chars": [972, 1646, 2525], "container": "epub",
                  "fitz_reflowed_pages": 412, "spine_sections": 36}),
    _source(
        "multiple-view-geometry",
        "Multiple View Geometry in Computer Vision (Richard Hartley) "
        "(z-library.sk, 1lib.sk, z-lib.sk).pdf",
        "Multiple View Geometry in Computer Vision (Hartley & Zisserman)",
        672, "text_layer", "geometry", 2,
        "射影/仿射变换、RANSAC、鲁棒估计 —— 图纸配准（相似变换 + RANSAC，"
        "实测 19% 粗差下最小二乘 RMSE 72.5m）那条链路的上位依据。",
        evidence={"sample_chars": [2687, 1483, 506]}),
    _source(
        "engineering-math",
        "Modern Engineering Mathematics (Abul Hasan Siddiqi, Mohamed Al-Lawati etc.) "
        "(z-library.sk, 1lib.sk, z-lib.sk).pdf",
        "Modern Engineering Mathematics (Siddiqi & Al-Lawati)",
        851, "text_layer", "math", 2,
        "向量分析、微分方程、数值方法。梁的挠曲微分方程与量纲分析可在此取证。",
        evidence={"sample_chars": [1510, 1650, 962]}),
    _source(
        "math-for-ml",
        "Mathematics for Machine Learning (Marc Peter Deisenroth, A. Aldo Faisal etc.) "
        "(z-library.sk, 1lib.sk, z-lib.sk).pdf",
        "Mathematics for Machine Learning (Deisenroth, Faisal & Ong)",
        417, "text_layer", "math", 3,
        "概率、最优化、主成分。金标准的加权与抽样、离群判据可在此取证。",
        evidence={"sample_chars": [2004, 2487, 2849]}),
    _source(
        "knight-physics",
        "Physics for Scientists and Engineers A Strategic Approach with Modern Physics "
        "(2nd Edition) (Randall D. Knight) (z-library.sk, 1lib.sk, z-lib.sk).pdf",
        "Physics for Scientists and Engineers, 2nd Edition (Randall D. Knight)",
        1450, "ocr", "physics", 1,
        "静力平衡 ΣF=0 / ΣM=0、力矩、重心、刚体 —— 支承与重力那一族规则的"
        "上位依据。**扫描件无文本层**（抽样三页 0 字符），要 OCR 才能取证；"
        "1450 页全量 OCR 代价高，按章节定向 OCR。",
        evidence={"sample_chars": [0, 0, 0]}),
)

#: 登记但**不抽取**的两本：科普读物，没有可引用的公式或定理陈述。
#: 记在这里是为了让「为什么这批是 7 本不是 9 本」有答案，而不是看起来漏了。
EXCLUDED: tuple[tuple[str, str], ...] = (
    ("A Brief History of Time (Stephen Hawking)",
     "科普读物，全书无公式推导，与构件合理性判据无交集"),
    ("Shape: The Hidden Geometry of ... (Jordan Ellenberg)",
     "科普读物，几何是叙事主题而非可引用的定理陈述"),
)


def by_key(key: str) -> KnowledgeSource:
    for source in SOURCES:
        if source.key == key:
            return source
    raise KeyError(f"未登记的资料 {key!r}")
