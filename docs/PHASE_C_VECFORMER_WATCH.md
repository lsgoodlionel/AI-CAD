# Phase C VecFormer 权重释放跟踪 + 迁移预研（C-10）

> 版本 V1.0 | 2026-07-10 | 任务 C-10（泳道 C｜模型，**旁路跟踪，不阻塞主线**）
>
> 上位方案：`docs/AI_READING_TO_3D_MODEL.md` §3.1（关键技术选型 · 许可证陷阱）
> 任务定义：`docs/PHASE_C_TASKS.md` C-10
> 合规门禁：`docs/PHASE_C_LICENSE_AUDIT.md` §3.2（VecFormer 放行·跟踪）
> 共享契约：`apps/api/core/model3d/spotting/types.py`（`SpottingBackend` Protocol）

---

## 0. 本任务定位（先读）

- **VecFormer 是中期升级目标，不是当前依赖。** 短期 spotting 由 CADTransformer(MIT，权重已释放) 承担，
  VecFormer(Apache 2.0，FloorPlanCAD PQ **91.1**) 精度更高但**官方权重暂未释放**，无法服务化。
- **本任务只做三件事**：① 持续跟踪权重释放；② 评估输入格式与 C-02 预处理器兼容性；③ 预写迁移适配层接口。
  **不引入依赖、不写真实推理**——一旦权重释放且许可复核通过，可快速切换。
- **旁路，不阻塞主线**：CADTransformer 已可用，VecFormer 权重可能长期不释放，迁移允许无限期挂起。

---

## 1. 跟踪记录

### 1.1 跟踪对象

| 项 | 值 |
|---|---|
| 代码仓库 | https://github.com/WesKwong/VecFormer |
| 论文 | NeurIPS 2025，https://arxiv.org/abs/2505.23395 |
| FloorPlanCAD PQ | 91.1（当前可商用件精度天花板） |
| 代码许可证 | Apache 2.0 ✅（含专利授权，无 copyleft 传染） |
| 权重状态 | ❌ 暂未释放 |
| 开箱度 | 2/5（代码在，权重缺，环境需自搭） |

### 1.2 复查节奏

- **每月复查一次**（建议每月首个工作日），检查项：
  1. GitHub **Releases / Tags** 是否新增权重 asset；
  2. README / MODEL_ZOO / 权重下载链接（HuggingFace / Google Drive / 百度网盘）是否上线；
  3. Issues 中「weights release」相关讨论的官方回复；
  4. 论文项目主页 / arXiv 新版本是否附权重说明。
- **触发式复查**：社区/群内出现「VecFormer 放权重」消息时即时复查，不等月度窗口。
- **跟踪归属**：泳道 C 负责人；每次复查结果追加到 §1.3 表，无论是否释放都留痕（含「本月仍未释放」）。

### 1.3 跟踪状态表（滚动追加，最新在上）

| 复查日期 | 权重是否释放 | 释放渠道/链接 | 许可复核结论 | 复查人 | 备注 |
|---|---|---|---|---|---|
| 2026-07-10 | ❌ 否 | — | 待权重释放后启动复核 | （建档） | C-10 建档；仓库仅代码，无权重 asset |

> **填表纪律**：权重一旦释放，「许可复核结论」列必须先走 §4 复核流程，未复核前一律填「待复核（禁用）」，
> `VecFormerBackend.is_available()` 保持 `False`。

---

## 2. 迁移评估

### 2.1 输入格式：VecFormer vs C-02 `PrimitiveDoc` / SVG

三条硬约束（对齐 §3.1）：**所有开源模型只吃 SVG / 图元序列，没有一个直接吃 DXF/DWG**。C-02 预处理器
（`core/model3d/preprocess/`）已把 DXF/PDF 统一转为 `PrimitiveDoc`（图元 JSON，页面点 pt 坐标 + 图层/块元数据），
这正是为「喂各家模型」而设的稳定中间表示。VecFormer 迁移的核心工作是**在 `PrimitiveDoc` 之上再写一层适配**，
而非新造预处理。

| 维度 | C-02 `PrimitiveDoc` | VecFormer 预期输入 | 兼容性差异 / 适配点 |
|---|---|---|---|
| 载体 | 图元 JSON（`Primitive` 序列） | 矢量图元序列（源于 SVG/矢量 CAD） | **同源**：均为矢量图元，非位图。适配层做「`PrimitiveDoc` → VecFormer 张量/图结构」编码 |
| 原语类型 | line / rect / polyline / text | 线/多段线/曲线等矢量原语 | C-02 已离散化为 line/rect/polyline；曲线离散化精度约定需与 VecFormer 训练口径对齐 |
| 坐标 | 页面点 pt（绝对） | 归一化坐标（平移到原点 + 尺度归一，NN 输入惯例） | **须归一化**：复用 C-03 `normalize.py`（`preprocess/normalize.py`）产出归一化坐标 |
| 文字 | text 原语（含 content） | **不处理中文文字**（符号识别 ≠ OCR） | 文字仍走 PaddleOCR；适配层将 text 原语**排除或降权**，不喂 VecFormer |
| 图层/块 | layer / block 元数据透传 | 模型不直接消费（弱标签属数据侧） | 元数据保留作 evidence / 弱标签溯源，不进模型前向 |
| 大幅面 | 页面尺寸 page_w/page_h | 有输入规模上限 | A0/A1 大幅面须切图（对齐第三章「大幅面细节进模型前丢失」告警），与 C-12 切图策略共用 |

**结论**：C-02 `PrimitiveDoc` 已覆盖 VecFormer 输入所需的全部原始信息，迁移**无需改预处理器契约**，
只需新增「`PrimitiveDoc` → VecFormer 输入编码」的适配函数（归一化复用 C-03，切图复用 C-12）。

### 2.2 与 CADTransformer(C-08) 的接口异同

| 项 | CADTransformer(C-08) | VecFormer(C-10) | 异同 |
|---|---|---|---|
| 许可 | MIT ✅（代码+权重齐全） | Apache 2.0 ✅（权重待释放） | 均可商用；VecFormer 卡在权重 |
| 输入 | SVG/图元序列（吃 C-02 输出） | 矢量图元序列（吃 C-02 输出） | **相同上游**：同一 `PrimitiveDoc`，各写各的编码适配 |
| 输出契约 | `SpottingResult` / `SymbolCandidate` | `SpottingResult` / `SymbolCandidate` | **完全相同**：共享 `types.py` 契约，输出无需转换 |
| 后端抽象 | 实现 `SpottingBackend` Protocol | 实现 `SpottingBackend` Protocol | **同一 Protocol**：C-12 服务可互换 |
| 依赖底座 | torch / dgl / torch-geometric（较老，环境地狱风险） | 待权重释放后确认（Apache 2.0，宽松） | 二者依赖树均无 copyleft 传染（见审计 §3.6） |
| 精度 | PQ 68.5（基线） | PQ 91.1（升级目标） | VecFormer 显著更高，是升级动机 |

**异同小结**：**上游（C-02 预处理）与下游（`SpottingResult` 契约、`SpottingBackend` Protocol、C-12 服务治理）完全共用**，
差异只集中在「模型内部输入编码 + 依赖环境 + 权重可用性」。这正是契约先行的收益：切换后端不触碰服务与融合层。

### 2.3 切换成本评估

| 成本项 | 评估 | 说明 |
|---|---|---|
| 上游预处理 | **零** | 复用 C-02 `PrimitiveDoc` + C-03 归一化，无改动 |
| 输出/服务契约 | **零** | 共享 `types.py`；C-12 SpottingService 后端可插拔 |
| 输入编码适配 | **低–中** | 新增 `PrimitiveDoc → VecFormer 输入` 编码；曲线离散化/切图口径对齐 |
| 依赖与环境 | **中** | 权重释放后锁定 torch 等版本；GPU 部署对齐 `infra/k8s/`（CUDA/cuDNN 再分发限制见审计 §3.6） |
| 权重许可复核 | **强制门禁** | 见 §4，未通过不得进产品 |
| 主线阻塞 | **无** | 旁路任务，CADTransformer 已兜底 |

**总评**：迁移主要成本落在「输入编码适配 + 环境锁定 + 许可复核」，因契约已固化，**切换成本可控且低风险**；
真正的不确定性是**权重是否/何时释放**（外部不可控），故本任务定为周期性跟踪而非排期实现。

---

## 3. 适配层接口草案

### 3.1 `VecFormerBackend`（已落占位 stub）

占位实现：`apps/api/core/model3d/spotting/vecformer/__init__.py`，实现共享 `SpottingBackend` Protocol：

```python
class VecFormerBackend:
    name = "vecformer"
    def is_available(self) -> bool: ...   # 占位期恒 False（权重未释放）
    def spot(self, doc: PrimitiveDoc) -> SpottingResult: ...  # 占位期返回空 + 警告，绝不抛异常
```

- **绝不 import torch 或任何未装依赖**：占位期只依赖 `PrimitiveDoc` 与 `SpottingResult`（纯 Python）。
- **优雅降级**：`is_available()` 为 `False` 时，C-12 SpottingService 自动降级到 mock / CADTransformer。

### 3.2 输入适配点（权重释放后填充）

真实推理落地时，在 `spot()` 内新增编码适配（不改契约、不改上游）：

1. **归一化**：`PrimitiveDoc` 坐标（pt）→ 归一化域，复用 C-03 `preprocess/normalize.py`。
2. **文字剥离**：排除/降权 text 原语（符号识别不处理中文文字，文字走 PaddleOCR）。
3. **切图**：大幅面按 C-12 切图策略分片，分片结果合并回单张 `SpottingResult`。
4. **编码**：图元序列 → VecFormer 输入张量/图结构（依权重释放后的官方实现对齐）。
5. **回填**：模型输出 → `SymbolCandidate`（category / confidence / bbox / primitive_ids / evidence），
   `evidence` 标注 `{"backend": "vecformer", "model": <weights_version>}`。

### 3.3 与 C-12 服务契约对齐点

- **后端可插拔**：VecFormerBackend 与 CADTransformer、mock 同实现 `SpottingBackend` Protocol，
  C-12 SpottingService 按 `is_available()` 选择后端，权重就绪即切换，服务代码零改动。
- **引擎治理**：接入 C-12 时经 `core/llm/router.py` 引擎体系（断路器/回退/日志/配置缓存），
  与 CADTransformer 共用治理，VecFormer 作为可选后端注册。
- **离线兜底**：CI 无 GPU/权重时，`is_available()` 为 `False` → 降级 mock，端到端链路可测（对齐「AI 服务提供离线 mock」约定）。

---

## 4. 合规提示（权重释放 ≠ 自动放行）

> 对齐 `docs/PHASE_C_LICENSE_AUDIT.md` §3.2 门禁提示与 SymPoint 教训。

1. **代码许可 ≠ 权重许可。** VecFormer 代码为 Apache 2.0 ✅，但**权重可能另附条款**
   （如非商用），二者可不一致（SymPoint 即代码/权重同受非商用约束的反面教材）。
2. **权重释放后必须复核**：核实「权重是否随 Apache 2.0 发布，或另附非商用/研究限定条款」。
   - 复核**通过**（权重可商用）→ 更新 §1.3 状态表、`is_available()` 方可返回 `True`、启动 §3.2 输入适配落地；
     按 Apache 2.0 义务保留 `LICENSE` / `NOTICE` 并标注对上游文件的修改。
   - 复核**未通过**（权重不可商用）→ 视同 SymPoint 处置：**绝不进产品代码**，仅可在隔离评测环境
     （对齐审计 §4 隔离方案）作内部天花板对标，`VecFormerBackend` 保持占位不启用。
3. **复核前保守**：任何「待确认 / 未声明」一律按最保守口径（视同不可商用）处理，`is_available()` 恒 `False`。
4. **签核联动**：VecFormer 权重进产品前，须确认 Phase C 人工审核门禁（审计 §7）已就绪，未签核不得合入 `main`。

---

## 5. 待办清单（跟踪期滚动）

- [ ] 每月复查 GitHub Releases / README，结果追加 §1.3。
- [ ] 权重释放当月：启动 §4 权重许可复核，结论落 §1.3。
- [ ] 复核通过后：填充 §3.2 输入适配、锁定依赖版本、接入 C-12 后端注册。
- [ ] 保留 `LICENSE` / `NOTICE`（Apache 2.0 义务），标注上游修改。
