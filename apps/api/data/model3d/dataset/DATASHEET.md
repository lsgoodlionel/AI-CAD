# 数据卡（Datasheet）— 自建 CAD 专业域符号数据集

> 版本 V1.0 | 2026-07-10 | 任务 C-07（泳道 B｜数据）
>
> 上位：`docs/PHASE_C_TASKS.md` C-07；切分脚本 `apps/api/scripts/model3d/dataset_split.py`
> 预处理契约：`docs/PHASE_C_PREPROCESS_SCHEMA.md`（C-02）
>
> 本卡为**模板 + 当前口径说明**。随 C-05/C-06 数据到位，逐版本追加「规模/分布」实测数字。
> 参照 Gebru et al.《Datasheets for Datasets》结构。

---

## 1. 动机（Motivation）

- **用途**：训练/评测 CAD 图纸符号识别模型（C-09 CADTransformer 微调、C-14 评测基座），
  覆盖**结构 / 机电 / 装修**中文专业域，补齐官方权重仅建筑平面图的空白。
- **为何自建**：公开集（FloorPlanCAD 等）为英文建筑平面，与国内多专业施工图分布差异大；
  自建集用于领域微调与**公平评测**（防止用训练分布评测导致虚高）。
- **不适用**：非本数据卡列出的专业/图种（如总图、精装大样长尾符号）零样本表现不保证。

## 2. 组成（Composition）

- **样本单位**：一张图纸经 C-02 预处理产出的「图元 JSON + SVG」为一个样本（`Sample`）。
- **清单契约**（`dataset_split.Sample`，frozen dataclass）：
  | 字段 | 说明 |
  |------|------|
  | `sample_id` | 文档内唯一样本 ID |
  | `project_id` | **切分键**：同项目样本整体同 split（防泄漏，见 §5） |
  | `drawing_id` | 图纸 ID（溯源） |
  | `path` | 预处理产物路径（图元 JSON / SVG） |
  | `meta` | 透传元数据，含 `discipline`（专业）、来源、图种（view_type）等 |
- **标签来源**：C-04 自动标注（弱标签：图层/块名溯源）→ C-06 人工精标注（金标签回流）。
- **规模 / 类别分布**：随数据到位逐版本填写（下表为占位）。

  | 版本 | 项目数 | 样本数 | 结构 | 机电 | 装修 | 备注 |
  |------|-------:|-------:|-----:|-----:|-----:|------|
  | v0（冷启动） | TBD | TBD | TBD | TBD | TBD | C-05 |
  | v1（精标注） | TBD | TBD | TBD | TBD | TBD | C-06 |

  > 分专业/分类别的实测分布由切分脚本 `dataset_statistics()` 生成，随 `splits.json` 归档。

## 3. 采集过程（Collection）

- **来源**：平台内经授权的项目图纸（DXF/DWG/PDF），经 C-02 统一预处理入口转换。
- **许可/合规**：见 `docs/PHASE_C_LICENSE_AUDIT.md` 与 C-01 签核门禁；仅纳入已授权数据。
- **采集时间窗**：随版本记录（每个 `splits.json` 携带生成时间与 seed）。

## 4. 预处理 / 脱敏（Preprocessing / Cleaning）

- **预处理**：C-02 `preprocess_drawing` / C-03 `expand_blocks` + `normalize_doc`
  （等比归一化到 [0,1]），产物为图元 JSON + SVG（不含原始业务文本以外内容）。
- **脱敏**：剔除标题栏中的单位名称、项目敏感标识等 PII；`meta` 仅保留训练/审计所需字段。
- **优雅降级**：提取失败/空图产出空文档 + warning，不纳入正式清单。

## 5. 切分口径（Splits）——**按项目，非按图**

- **灵魂约束**：切分**按 `project_id`**，同一项目的**所有**图纸整体落在同一 split。
  绝不按图切——否则同项目样本跨 train/test 泄漏，评测虚高。
- **比例**：默认 `train:val:test = 0.8:0.1:0.1`（贪心逼近**样本级**比例，按项目整体分配）。
- **可复现**：固定随机种子（默认 `seed=42`）；同输入同种子 → 同切分（确定性洗牌）。
  一键复现：
  ```bash
  python apps/api/scripts/model3d/dataset_split.py manifest.json --out splits.json --seed 42
  ```
- **无泄漏自检**：脚本产出前调用 `assert_no_project_leakage()`；`verify_reproducible()`
  可断言两次切分逐位一致。
- **test 集冻结**：切分清单 `splits.json` 固化后 **test 集不得再变**，仅 C-18 最终评测解锁一次。
  变更须走版本升级并记录理由。

## 6. 已知偏差与限制（Known Biases / Limitations）

- **类别不均衡**：装修/机电符号长尾，结构域样本更充分；评测须看**分专业分类别**指标。
- **专业/图种覆盖不均**：早期版本以结构域为主（C-09 先结构迭代）。
- **弱标签噪声**：C-04 自动标注依赖图层/块名规范度，命名不规范图纸标签噪声较高（C-06 修正）。
- **大幅面丢失**：A0/A1 切图策略未在 C-02 覆盖，超大图细节可能丢失（见 C-02 §4.4 告警）。
- **PDF 无图层/块**：PDF 来源样本缺弱标签溯源。

## 7. 版本与维护（Versioning / Maintenance）

- **版本化**：数据集清单 + `splits.json` 纳入版本管理（MinIO + 清单；DVC 可选，不强制引入依赖）。
- **每版本冻结物**：`manifest.json`（样本清单）、`splits.json`（含 seed/ratios/统计）、本数据卡。
- **变更记录**：新增数据 → 新版本号 + 重新切分（新 seed 或沿用），旧 test 集在 C-18 前保持冻结。
- **维护者**：泳道 B（数据工程）。

## 8. 分发与许可（Distribution / License）

- **内部使用**：仅限平台内授权范围；不对外分发原始图纸。
- **数据许可**：以 `docs/PHASE_C_LICENSE_AUDIT.md` 结论为准；模型代码侧开源件许可另见该审计。
