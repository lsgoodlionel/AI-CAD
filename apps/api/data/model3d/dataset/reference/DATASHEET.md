# 参考域符号数据集（reference）—— 数据卡

> 产出：`core.knowledge.legend_table` + `scripts/knowledge/build_symbol_dataset.py`
> 分类：`core.knowledge.label_map` + `scripts/knowledge/label_symbol_dataset.py`
> 资料清单：`core.knowledge.source_registry`（14 份，1851 页/节）
> 上位规范：`docs/PHASE_C_DATASET_SPEC.md`（顶层 9 类不得另起炉灶）

## 1. 这是什么

从**国家建筑标准设计图集与识图教材的图例表**里切出来的
「符号图 + 中文名 + 出处」配对。图例表版式规整（`名称|图例|说明` 或
`序号|符号|说明`），**同一行就是一对标注** —— 名字是编者写下的，不是猜的。

## 2. 这**不是**什么（最要紧的一条）

**这不是可以直接拿去训练生产检测器的数据集。**

存在明确的**域差异（domain gap）**，且无法靠数据增强消掉：

| | 本数据集 | 生产图纸 |
|---|---|---|
| 载体 | 出版物**扫描件**（1-bit 灰度，200 dpi 渲染）| 矢量 PDF |
| 线宽 | 印刷 + 扫描后的粗化与断裂 | 精确线宽（0.25b/0.5b/b）|
| 上下文 | 表格单元格里的**孤立示意** | 图面上与轴网/尺寸/其他构件交叠 |
| 尺度 | 单元格内固定大小 | 随图纸比例变化（1:1 ~ 1:2000）|

`docs/PHASE_C_DATASET_SPEC.md` §2.2 明文**拒收扫描件**，指的正是这种情况。
本数据集因此单列为 `reference/` 层，**不并入 `raw/` `weak_labeled/` `gold/`**，
也不参与 C-07 的 train/val/test 切分。

**它能用来做什么**：
- **符号词表**：中文名 ↔ 9 类分类法的映射依据（已产出 `label_map`）；
- **模板参考**：人工核对识别结果时的「标准长什么样」；
- **人工标注的参照图**：给标注员看的图例册（C-06 质检用）；
- **少样本/模板匹配的种子**，前提是在真实图纸上单独验证过。

**它不能用来做什么**：直接喂检测器训练并宣称在生产图纸上有效。

## 3. 规模与构成（实测，非估计）

| 指标 | 值 |
|---|---|
| 条目总数 | **380** |
| 无警告条目 | **253**（66.6%）|
| 名称取自「说明」列（表结构如此，非缺陷）| 246 |
| 来自旋转 90° 表格 | 218 |
| 磁盘占用 | 2.3 MB |

按来源：

| 资料 | 条目 |
|---|---|
| 看范例快速识读建筑电气工程图 | 238 |
| 建筑工程识图与预算快学一本通 | 130 |
| 22G101-1 | 6 |
| 22G101-3 | 6 |

按 `domain`：electrical 159 / unmapped 149 / material 41 / component 17 /
plumbing 4 / hvac 3 / annotation 3 / opening 3 / fire 1。

按顶层 `taxonomy`：**282 条为 `None`** —— 这不是失败，是事实：
材料图例是剖面填充图案、电路元件没有空间实体，它们本就不属于 9 类构件。
能落进 9 类的只有 98 条（equipment 65 / pipe 17 / door 6 / wall 3 /
window 3 / slab 3 / beam 1）。

## 4. 已知缺陷（如实登记）

1. **46 条没有名称**（`warnings: ["no_name"]`）——
   多因**合并单元格**：网格按线交点切，rowspan 的格被切碎，
   名称落在了别的网格行里。
2. **55 条名称存疑**（`name_suspect`）—— 说明的片段串进了名称位置，
   判据是长度与条目编号特征（`is_plausible_name`）。
3. **149 条未映射到分类**（其中 46 条本就无名称）——
   剩余多为 GB/T 4728 的冷门电路符号。**如实留白，未强行归类。**
4. **76 个候选页未出任何数据**（`no_header`）—— 表头认不出就不出数据，
   而不是假设「第二列是符号」。这些页的清单见构建日志。
5. 图集（22G101 等）只出了 12 条 —— 图集的正文是**构造详图**而非图例表，
   本方法只覆盖规整的图例表。图集的价值主要在**文字层**
   （见 `data/knowledge/drawing_standards/`），不在这里。

## 5. 合规

**这批资料是受版权保护的出版物**（中国建筑标准设计研究院、机械工业出版社等），
来源为用户本地提供的 `/Users/lionel/work/识图标准`。

- 切图与派生数据**仅供本平台内部识别与人工核对使用**；
- **不得对外分发、不得公开发布、不得随开源代码一起发布**；
- 目录已按 `apps/api/data/model3d/dataset/README.md` 的铁律处理：
  `symbols/` 下的图片**不进 git**（见 `.gitignore`），
  仓库里只保留 manifest 与本数据卡；
- 原件已上传至 MinIO `atlases` 桶（`knowledge/` 前缀），
  访问经平台既有的签名 URL 通道，不做匿名公开。

## 6. 可复现

```bash
# ① 扫描件 OCR（缓存在仓库之外，可断点续跑）
scripts/knowledge/ocr_all.sh 4

# ② 切图 + 标注
python scripts/knowledge/build_symbol_dataset.py --all
python scripts/knowledge/label_symbol_dataset.py
```

固定输入下结果确定（无随机性）。OCR 后端为 RapidOCR PP-OCRv6，
渲染 200 dpi —— 实测 200 与 300 dpi 的识别结果无实质差异
（68 vs 65 token、置信度同为 0.98），故取更快的 200。

## 7. 字段

`symbols_labeled.jsonl` 每行一条：

| 字段 | 含义 |
|---|---|
| `entry_id` / `image` | 稳定 id / 切图相对路径 |
| `name` / `note` / `code` | 中文名 / 说明 / 序号或代号 |
| `name_role` | `name_column`（表有名称列）/ `note_column`（表只有说明列）|
| `source_key` / `std_no` / `book_title` / `page_index` | 出处，可回溯到原件页 |
| `table_caption` | 表题原文（如「表 2-10 表示常用建筑材料的图例」）|
| `symbol_bbox` / `dpi` / `rotated` | 切图位置（图像像素）/ 渲染 dpi / 该页是否旋转过 |
| `domain` / `taxonomy` / `subclass` / `mep_system` | 分类（见 `label_map`）|
| `label_matched_by` | 命中的关键词，便于人工核对与调参 |
| `warnings` | `no_name` / `name_suspect` / `name_from_note` |
