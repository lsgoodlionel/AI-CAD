# Phase D 泳道5 · D-17 规范导入升级（docling）

> 对应 `docs/PHASE_D_LANE5_PLAN.md` D-17 节。本次范围：docling（IBM，MIT）
> 候选前段接入 + 离线 A/B 评测脚手架 + 规范/图集 PDF 来源清单。**未安装
> docling 依赖、未跑真实推理**——deps 缺失时优雅降级到既有链路，默认行为
> 不变。

## 1. 现状与改动

`services/regulation_importer.py::extract_text_from_pdf` 原本是
pymupdf4llm → pymupdf 两级降级，两者都是「按页抽文本流」，对规范 PDF 常见
的**多栏正文**（左右两栏交错拼接风险）和**表格**（构件截面表、荷载表等强
条文常见形式，坍缩成散乱文本行）没有结构化保真，下游 NLP 提取流水线
（Haiku 分类 → Sonnet 深提取 → AGE/Chroma）再从乱序文本里抠条文编号/表格
数据，准确率天然受限。

本次改动：

| 文件 | 改动 |
|---|---|
| `core/regulation/docling_extract.py`（新建） | docling 封装：懒加载单例 + 负例缓存 + 优雅降级，`is_available()` / `extract_with_docling(file_bytes, filename) -> str \| None` |
| `services/regulation_importer.py`（改） | 新增 `extract_with_docling` 薄包装 + `extract_text_from_pdf` 优先级链改为 **docling（若可用）→ pymupdf4llm → pymupdf**；`extract_text_from_pdf`/`extract_text` 补 `filename` 透传（原来 `extract_text_from_pdf` 不接收 filename，docling 需要文件名后缀信息） |
| `scripts/regulation/parse_ab_eval.py`（新建） | 规范 PDF 解析质量离线 A/B 评测 CLI（docling vs 既有链） |
| `tests/test_docling_extract.py`（新建） | 11 个离线测试，覆盖未安装降级 + 注入假 docling 包的成功/空结果/异常三分支 + `regulation_importer` 优先级链接线 |

### 降级链契约

```
extract_text_from_pdf(file_bytes, filename)
  ├─ extract_with_docling(file_bytes, filename)   # D-17 新增前段
  │    └─ core.regulation.docling_extract.extract_with_docling
  │         ├─ docling 未安装 → None（缓存负例，后续调用直接短路）
  │         ├─ 转换异常 → None（不抛出）
  │         ├─ 转换结果为空/纯空白 → None
  │         └─ 成功 → Markdown 文本
  ├─ 若上一步返回非空文本 → 直接返回（不再跑 pymupdf4llm/pymupdf）
  ├─ 否则 pymupdf4llm.to_markdown（未安装 → ImportError 被捕获，跳过）
  └─ 否则 fitz 逐页 page.get_text() 拼接（原始兜底，失败才真正抛 RuntimeError）
```

**本仓库当前开发环境未装 docling，也未装 pymupdf4llm**（`.venv` 内实测：
`import pymupdf4llm` 即 `ModuleNotFoundError`），因此当前实际生效路径是链路
最后一级——fitz 原始 `page.get_text()`。这与 D-17 改动前的行为完全一致，
验证了「不改默认行为」的约束。

## 2. A/B 评测口径（`scripts/regulation/parse_ab_eval.py`）

三项确定性指标，均不调用 LLM：

1. **条文编号抽取 Precision/Recall/F1**——复用既有
   `regulation_importer.split_into_paragraphs` 分段口径，从每段首行抠条文
   编号（如 `4.2.3`），与金标准编号集合做多重集精确匹配。
2. **条文顺序保真**（LCS 占比）——命中编号的抽取顺序与金标准顺序的最长公共
   子序列长度 / 金标准长度。F1 只看「有没有抽到」，抽到了但被多栏交错拼接
   打乱顺序不会体现在 F1 上，必须单独度量；这是本评测口径里唯一直接针对
   「多栏排版保真」的指标。
3. **表格结构保真**——正则探测 Markdown 表格分隔行（`| --- | --- |`）是否
   出现，与金标准「该样本是否含需保真表格」做二分类 Precision/Recall/F1。
   **这是结构语法代理指标，不是单元格级内容比对**——语义级表格内容比对
   需要逐单元格金标注，超出本轮范围。

三个候选后端：

- `docling`：直接调用 `extract_with_docling`；docling 未安装时该样本计入
  `unavailable_samples`，**不参与打分、不用 0 分冒充"跑过"**（与 D-16 OCR
  评测基座同一纪律）。
- `pymupdf4llm_chain`：脚本内独立重新实现 D-17 之前的降级链（不经过
  docling 前段），作为 A/B 基线。
- `current_pipeline`：直接调用生产用 `extract_text_from_pdf`，用于确认
  「docling 缺失时默认行为等同基线」这条约束在真实调用路径上也成立。

用法：

```bash
# 合成 demo（不依赖真实 PDF/docling，验证基座端到端；已跑通，见下方示例输出）
python scripts/regulation/parse_ab_eval.py --demo

# 真实评测（需你提供的规范 PDF 样本 + 人工标注金标签）
python scripts/regulation/parse_ab_eval.py --manifest manifest.json \
    --out docs/parse_ab_report.md --json docs/parse_ab_report.json
```

`manifest.json` 结构：

```json
{
  "samples": [
    {
      "sample_id": "gb50016-4.2",
      "path": "gb50016-4.2-excerpt.pdf",
      "gold": {
        "article_nos": ["4.2.1", "4.2.2", "4.2.3"],
        "table_expected": true
      }
    }
  ]
}
```

`--demo` 已在开发环境验证跑通（两个合成样本，一个后端保留表格分隔行、一个
丢弃表格），报告正确区分了两者的表格 Precision/Recall/F1（保留表格的一侧
1.000/0.500/0.667，丢表格的一侧 0.000/0.000/0.000），证明评测基座本身的
匹配逻辑正确；真实结论仍需真实规范 PDF + 金标签，见下节来源清单。

## 3. 真实 docling 调用的接线 TODO

`core/regulation/docling_extract.py::extract_with_docling` 文档字符串内
已列三条，摘要：

1. 当前用 `tempfile.NamedTemporaryFile` 落盘再转换（对任何「只支持路径
   输入」的 docling 版本都成立的保守写法）；装真实 docling 后应确认所装
   版本的内存流 API（`DocumentStream` 或等价物），改用 `BytesIO` 直接喂，
   省一次磁盘 IO。
2. 需确认 `export_to_markdown()` 产出的表格 Markdown 语法是否会打断
   `_ARTICLE_PATTERN` 依赖的「条文编号所在行连续性」——若表格块插入条文
   正文中间，可能需要在喂给 `split_into_paragraphs` 前加一道表格块隔离
   预处理。用 `parse_ab_eval.py` 在真实样本上实测后再决定。
3. 需评估大文件（整本规范书常见 200+ 页）docling 转换耗时是否需要挪到
   Celery 异步任务——现有 pymupdf4llm 路径是同步调用（见
   `import_regulation_file` 的调用方式），若 docling 显著更慢需要评估是否
   挤占导入任务的整体超时预算。

## 4. 规范 / 图集 PDF 来源清单（供后续评测取样）

**方针**：本文档只列来源渠道 + 免费/授权标注，供你后续挑选样本人工下载、
标注金标签；本次任务未批量下载任何受版权 PDF。国标本身版权状况特殊（强制
性国标依法应免费公开全文，推荐性国标/行业标准/标准图集则通常需付费购买
正版）。

### 4.1 免费可得（官方全文公开，优先取样源）

| 来源 | 说明 | 免费程度 |
|---|---|---|
| [国家标准全文公开系统](https://openstd.samr.gov.cn/bzgk/std/) | 国家市场监督管理总局官方系统，**强制性国标（GB）依规全文免费公开**，含在线阅读 + PDF 下载（部分标准仅预览，下载版无目录/不可选中文字，需注意与购买版排版差异） | ✅ 强制性国标免费；部分推荐性/仅预览标准受限 |
| [全国标准信息公共服务平台](https://std.samr.gov.cn/gb/) | 国标目录查询 + 公告，是 openstd 的配套检索入口 | ✅ 查询免费，下载跳转 openstd |
| [住房和城乡建设部 官网 标准规范栏目](https://www.mohurd.gov.cn/gongkai/fdzdgknr/bzgf/index.html) | 工程建设标准发布公告，部分标准（尤其强条通用规范，如《建筑防火通用规范》GB55037）随公告附免费 PDF | ✅ 部分免费（视具体公告） |
| [建标库 jianbiaoku.com](http://www.jianbiaoku.com/webarbs/list/117/1.shtml) | 第三方聚合站，在线阅读 + 部分免费下载建筑规范 | 🟡 免费但非官方，条款/版权状态自担核实责任，建议仅用于快速核对条文，不作为正式取样底稿 |

**取样建议**：优先从 openstd 抓**强条密集、含表格/多栏排版**的通用规范
（下方 4.3 已列出候选标准号），这类标准依法全文免费公开，法律风险最低，
且天然覆盖 D-17 要评测的两个难点（表格保真 + 多栏保真）。

### 4.2 需授权购买（标准图集为主，正版渠道）

| 来源 | 说明 | 授权程度 |
|---|---|---|
| [中国建筑标准设计研究院 · 国标电子书库](https://ebook.chinabuilding.com.cn/) | 官方电子书库，收录全部 10 个专业 1000+ 项国家建筑标准设计图集（如 22G101 系列平法图集）+ 3000+ 项国家/行业标准规范 | ❌ 需付费订阅/购买，账号级授权 |
| [国标电子书库商城 shop.chinabuilding.com.cn](https://shop.chinabuilding.com.cn/) | 图集/标准正版购买入口 | ❌ 按册/按年付费 |
| [中国建筑标准设计网](https://www.chinabuilding.com.cn/books.html) | 图集信息查询与出版物介绍 | 🟡 信息免费，图集本身需购买 |
| [中国建筑工业出版社](https://www.cabp.com.cn/) | 规范/图集官方出版发行方之一 | ❌ 需购买 |

**取样建议**：标准图集（构件截面表、配筋图等表格密度最高的场景）取样若
需要，建议走单位现有正版渠道（若项目部/图书馆已订阅国标电子书库，直接用
现有授权账号导出样张），不建议为评测临时购买整套图集；也不建议使用未经
核实授权状态的第三方聚合下载站（搜索中出现的 doc88/bzfxw 类站点版权状态
不明确，本清单不收录）。

### 4.3 候选标准号（覆盖结构/建筑/机电，兼顾强条密度与表格/多栏排版特征）

以下均为常见通用规范，多数条款为强制性条文，依法可在 openstd 免费获取，
适合作为 D-17 A/B 评测的初始取样对象（具体版本号以 openstd 检索到的现行
有效版为准）：

- **结构**：GB 50010《混凝土结构设计规范》、GB 50011《建筑抗震设计规范》、
  GB 55008《混凝土结构通用规范》（构件截面/配筋表格密集）
- **建筑/防火**：GB 55037《建筑防火通用规范》、GB 50016《建筑设计防火
  规范》（条文编号密度高、疏散距离/防火间距常配表格）
- **机电**：GB 50015《建筑给水排水设计标准》、GB 50019《建筑环境通用
  规范》（暖通空调）、GB 51348《民用建筑电气设计标准》
- **通用/双栏排版典型样本**：GB/T 50001《房屋建筑制图统一标准》（图示与
  文字混排，适合测试版面分析对纯文字外内容的处理）

## 5. 关键取舍

1. **优先 docling（MIT）而非 MinerU（AGPL-3.0）**——按你已确认的方向，
   MinerU 的 CJK 表格能力据称更强，但 AGPL 商用需过
   `PHASE_C_LICENSE_AUDIT` 同款审计（你尚未拍板授权），本轮不评估、不接入。
2. **不安装 docling 依赖**——避免在未确认「A/B 结论值得占用部署镜像体积」
   前就引入新的重依赖；`is_available()` 契约保证依赖到位后即可无缝启用，
   不需要再改一次业务代码。
3. **表格保真用语法代理指标而非内容比对**——单元格级金标注成本高，本轮
   用「是否保留 Markdown 表格语法」这一更便宜但仍有区分度的代理指标先跑
   通评测基座；后续若表格语法保真已确认稳定领先，再决定是否值得投入内容
   级标注。
4. **顺序保真单独成一项指标而非并入 F1**——多栏排版交错拼接是规范 PDF 解析
   最隐蔽的失效模式（编号都"抽到了"，PR/F1 看起来正常，但下游按顺序拼接
   条文正文时会把不相邻的两段接在一起），必须用 LCS 占比单独暴露，不能让
   F1 掩盖这个问题。
5. **`extract_text_from_pdf` 新增 `filename` 形参**——docling 需要文件名
   后缀信息（用于临时文件命名），这是本次对既有函数签名唯一的改动；已
   同步更新其调用方（`extract_text` 内部调用点）；`filename` 设为可选
   参数（默认空串）避免破坏假设的外部调用方。
