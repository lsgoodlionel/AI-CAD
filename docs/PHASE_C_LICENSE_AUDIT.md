# Phase C 开源件许可证合规审计（C-01）

> 版本 V1.0 | 2026-07-10 | 任务 C-01（泳道 A｜合规，总门禁）
>
> 上位方案：`docs/AI_READING_TO_3D_MODEL.md` 第三章「关键技术选型（含许可证 / 开箱度对比）」
> 任务定义：`docs/PHASE_C_TASKS.md` C-01 / C-11
> 关联设计：`docs/CROSS_VIEW_Z_RECOVERY_DESIGN.md`（Phase B 确定性 z 恢复，与 Phase C 学习模型分层）

---

## 0. 本文档的法律地位（先读）

- **这是准入门禁，不是参考资料。** 所有 G3（模型）/ G4（推理服务化）任务在本审计**书面签字前不得落任何模型代码**。
- **结论以「进产品代码可商用性」为唯一裁决口径。** 学术精度再高，只要不可商用，就**只能作内部对标天花板**，绝不进 `requirements.txt`、不进生产镜像、不进 `apps/` 产品树。
- **诚实优先。** 凡「待确认 / 未声明」一律按**最保守口径（视同不可商用）**处理，直到取得书面许可澄清。不得因「大概能用」就放行。

---

## 1. 结论速览（TL;DR）

| 开源件 | 用途 | 许可证 | 可商用 | 权重 | 进产品代码 | 门禁结论 |
|---|---|---|---|---|---|---|
| **CADTransformer** | 符号 spotting 短期首选 | MIT | ✅ | ✅ 已释放 | ✅ 允许 | **放行**（C-08/C-09/C-12） |
| **VecFormer** | 中期精度升级 | Apache 2.0 | ✅ | ❌ 暂未释放 | ✅ 允许（待权重） | **放行·跟踪**（C-10） |
| **SymPoint V1** | 内部精度天花板对标 | 非商用（Non-Commercial） | ⛔ | ✅ | ⛔ 禁止 | **隔离**（仅 C-11 评测） |
| **SymPoint V2** | 内部精度天花板对标 | 未声明（No License） | ⛔ | ✅ | ⛔ 禁止 | **隔离**（仅 C-11 评测） |
| **ArchCAD-400K 数据集** | 冷启动方法论借鉴 | 待确认 | ？ | 部分 | ⛔ 数据不分发 | **仅借方法论**（C-04/C-05） |
| **FloorPlanCAD 数据集** | 基线复现/评测 | 研究用途（见 §3.5） | ？（评测限定） | — | ⛔ 不进产品数据 | **仅评测**（C-08/C-14） |

**一句话门禁**：产品代码只允许 `CADTransformer(MIT)` 与 `VecFormer(Apache 2.0)`；`SymPoint` 系与外部数据集**物理隔离**在 `research/`，产品构建（git 树 + Docker 镜像）**任何时刻 grep 不到 SymPoint 依赖**（CI 硬门禁，见 §5）。

---

## 2. 审计范围

1. **模型本体**：CADTransformer、VecFormer、SymPoint V1/V2。
2. **数据集**：ArchCAD-400K、FloorPlanCAD。
3. **深度学习依赖树**：torch、dgl、torch-geometric 及其常见传染性依赖。
4. **预处理 / 工具链**：ezdxf、svgwrite、DVC、label-studio（C-06 可选标注工具）。
5. **隔离方案**：SymPoint 物理隔离边界（C-11 前置确认）。

> 本审计**不覆盖** Phase A/B 已入库的运行时依赖（`requirements.txt` 现有条目已在既往阶段审过）；仅审 Phase C **新引入**件。

---

## 3. 逐项审计明细

> 字段：件名 / 版本或来源 / 许可证 / 可商用 / 权重许可 / 依赖传染性 / 结论。

### 3.1 CADTransformer —— ✅ 放行（短期首选）

| 字段 | 值 |
|---|---|
| 来源 | https://github.com/VITA-Group/CADTransformer |
| 论文 | CVPR 2022，FloorPlanCAD PQ 68.5 |
| 许可证 | **MIT** |
| 可商用 | **✅ 是** |
| 权重许可 | 官方权重已释放；随仓库 MIT 条款，可商用 |
| 依赖传染性 | 依赖 torch / dgl / torch-geometric（均宽松许可，见 §3.6），**无 copyleft 传染** |
| 结论 | **准入放行。** 可进 `apps/api/core/model3d/spotting/cadtransformer/`、进 `requirements.txt`（或独立 extra）、进生产镜像。 |

- **合规义务**：分发时保留 MIT 版权声明与许可证副本（在 `spotting/cadtransformer/` 内保留上游 `LICENSE` 与出处 `NOTICE`）。
- **风险**：依赖版本较老（dgl / torch 特定版本），**环境地狱风险属工程风险，非许可风险**；官方权重仅覆盖建筑平面图，自有专业域需 C-09 微调（不影响许可结论）。

### 3.2 VecFormer —— ✅ 放行·跟踪（中期升级）

| 字段 | 值 |
|---|---|
| 来源 | https://github.com/WesKwong/VecFormer / https://arxiv.org/abs/2505.23395 |
| 论文 | NeurIPS 2025，FloorPlanCAD PQ 91.1 |
| 许可证 | **Apache 2.0** |
| 可商用 | **✅ 是** |
| 权重许可 | **❌ 权重暂未释放**——代码可用，权重待观察其释放条款 |
| 依赖传染性 | Apache 2.0 含专利授权条款，宽松无 copyleft |
| 结论 | **准入放行（代码层面）**；权重释放前无法服务化，由 C-10 持续跟踪。权重释放后**必须复核其权重许可**再放行。 |

- **合规义务**：Apache 2.0 要求保留 `LICENSE` + `NOTICE`，标注对上游文件的修改。
- **门禁提示**：**权重释放 ≠ 自动放行**。释放后 C-10 须复核「权重是否随 Apache 2.0 或另附非商用条款」，复核通过方可进产品（对齐 SymPoint 教训：代码许可与权重许可可能不一致）。

### 3.3 SymPoint V1 —— ⛔ 隔离（仅内部对标）

| 字段 | 值 |
|---|---|
| 来源 | https://github.com/nicehuster/SymPoint |
| 许可证原文 | https://github.com/nicehuster/SymPoint/blob/main/LICENSE.txt |
| 论文 | ICLR 2024，FloorPlanCAD PQ 83.3 |
| 许可证 | **非商用（Non-Commercial Use Only）** |
| 可商用 | **⛔ 否** |
| 权重许可 | 权重同受非商用条款约束 |
| 依赖传染性 | **高危间接引入路径**：其数据加载器 / 评测脚本 / 预处理可能被误借入产品 |
| 结论 | **禁止进产品。** 仅允许存在于**物理隔离评测环境** `research/sympoint-eval/`（见 §4），产出**只以数字形式**回流 C-14 评测报告。 |

### 3.4 SymPoint V2 —— ⛔ 隔离（法务出局）

| 字段 | 值 |
|---|---|
| 来源 | SymPoint 后续版本 |
| 论文 | 2024，FloorPlanCAD PQ 90.1 |
| 许可证 | **未声明（No License）** |
| 可商用 | **⛔ 否**（无明示许可 = 默认保留全部权利，视同不可用） |
| 权重许可 | 未声明，同样保守出局 |
| 依赖传染性 | 同 V1 |
| 结论 | **禁止进产品。** 与 V1 同隔离处置。「未声明」在版权法下默认作者保留全部权利，比「明示非商用」更严——**绝不可因『没写不许用』而误判为可用**。 |

### 3.5 数据集 —— 仅方法论 / 仅评测

| 数据集 | 许可 | 处置 |
|---|---|---|
| **ArchCAD-400K** | 待确认（NeurIPS 2025，https://arxiv.org/abs/2503.22346） | **仅复刻「用 CAD 图层/块属性自动标注」方法论**（C-04），**不直接分发/再发布其数据**。若后续需引用其数据，先取得书面许可澄清再入库。 |
| **FloorPlanCAD** | 研究用途（CVPR 2021，https://arxiv.org/abs/2105.07147） | **仅用于 C-08 基线复现与 C-14 交叉参照评测**；**不进产品数据资产、不再分发**。自建专业域数据集（C-05）为产品训练的唯一合规数据源。 |

- **原则**：产品训练/微调（C-09）**只允许用自建数据集**（C-05/C-06/C-07，按项目切分、脱敏合规）。外部数据集一律限定「评测参照」或「方法论借鉴」，不进产品数据资产。

### 3.6 深度学习依赖树 —— ✅ 宽松许可

| 依赖 | 典型许可 | 可商用 | 备注 |
|---|---|---|---|
| **PyTorch (torch)** | BSD-3-Clause | ✅ | CADTransformer 底座 |
| **DGL (dgl)** | Apache 2.0 | ✅ | 图神经网络库 |
| **PyG (torch-geometric)** | MIT | ✅ | 图卷积算子 |
| **numpy / scipy** | BSD | ✅ | 数值计算 |
| **opencv-python**（若用） | Apache 2.0 | ✅ | 图像处理；**注意区分** `opencv-python`(Apache) 与打包了 GPL 组件的发行版，产品用官方 Apache wheel |
| **Pillow** | HPND（宽松，类 MIT） | ✅ | 已在库 |
| **CUDA / cuDNN 运行时** | NVIDIA EULA | ⚠️ | **闭源商业条款**：可用于商业推理，但**不得再分发** CUDA/cuDNN 二进制；镜像内使用需遵守 NVIDIA 容器分发条款（对齐 `infra/k8s/` GPU 部署时复核）。 |

- **结论**：DL 依赖树**无 copyleft 传染风险**；唯一需留意的是 **CUDA/cuDNN 的再分发限制**（NVIDIA EULA），部署镜像时遵守其容器分发条款即可，不影响自研代码许可。

### 3.7 预处理 / 工具链 —— ✅ 宽松许可

| 件 | 许可 | 可商用 | 用途 |
|---|---|---|---|
| **ezdxf** | MIT | ✅ | DXF 解析（已在库，C-02/C-03 复用） |
| **svgwrite** | MIT | ✅ | SVG 序列化（C-02，可选，或手写序列化） |
| **DVC** | Apache 2.0 | ✅ | 数据集版本管理（C-07，可选，MinIO 清单为备选） |
| **label-studio**（社区版） | Apache 2.0 | ✅ | 标注工具（C-06 可选；**优先复用前端审校工作台 C-16**，避免另起平台） |

---

## 4. SymPoint 隔离方案确认（C-11 前置门禁）

> C-11 落地前，本节隔离边界**必须经负责人签字**。任何 SymPoint 代码/权重泄漏进产品即法务事故。

### 4.1 隔离原则（三条红线）

1. **物理隔离**：SymPoint 代码、权重、其专属数据加载器/评测脚本**只存在于** `research/sympoint-eval/`，该目录：
   - **不纳入产品构建**（不在任何 `apps/*/Dockerfile` 的构建上下文内）；
   - **git 不追踪**（`.gitignore` 排除，见 §4.2），或置于**完全独立的私有仓**（更强隔离，推荐长期）；
   - **不进 `requirements.txt`**，其依赖装在隔离环境自有的 `research/sympoint-eval/requirements-eval.txt`（不与产品共享）。
2. **单向数据流**：隔离环境**只输出数字**（PQ/精度/召回等对标指标）回流 `docs/PHASE_C_EVAL_REPORT.md`（C-14）。**代码与权重绝不回流产品。**
3. **可验证**：产品代码库（`apps/`、`packages/`、`infra/`、`requirements*.txt`）**任何时刻 `grep -ri sympoint` 必须为空**——由 CI 硬门禁强制（§5）。

### 4.2 隔离落地清单

- `.gitignore` 增 `research/sympoint-eval/`（隔离区不入 git；大权重走隔离环境本地/私有存储）。
- `.dockerignore`（各产品镜像上下文）确保 `research/` 不入镜像——**当前 `apps/api` 镜像上下文为 `apps/`，`research/` 本就在上下文之外，天然隔离**；仍在根 `.dockerignore` 显式排除 `research/` 作为二次保险。
- CI 新增 `license-compliance` job（§5）：产品树 grep SymPoint → 命中即 **fail**。
- `research/sympoint-eval/README.md`（不入 git，本地建立）载明：隔离目的、非商用禁令、「仅评测、只出数字」纪律。

### 4.3 隔离环境形态（二选一，推荐后者）

| 形态 | 隔离强度 | 说明 |
|---|---|---|
| 同仓 `research/sympoint-eval/`（gitignore + dockerignore） | 中 | 便捷；靠忽略规则 + CI grep 门禁保证不泄漏。**MVP 采用。** |
| **完全独立私有仓/容器** | 高 | 物理隔离最彻底，产品仓 grep 恒空。**长期推荐**，尤其对外交付/审计场景。 |

---

## 5. CI 合规门禁（C-01 交付物之一）

在 `.github/workflows/ci.yml` 新增 `license-compliance` job，含两道确定性检查：

1. **SymPoint 防泄漏门禁（硬失败）**：对产品树（`apps/`、`packages/`、`infra/`、`requirements*.txt`、`docs/` 除本审计与评测报告外）执行 `grep -ri`，命中 `sympoint` 关键字即 `exit 1`。
   - **落地关键**：把「文档里提到 SymPoint」与「代码/依赖里引入 SymPoint」区分开——门禁**排除** `docs/` 下的审计/评测文档（它们本就要写 SymPoint 这个词），只扫**可执行产品面**（代码 + 依赖清单 + 构建配置）。
2. **禁用件清单校验**：`requirements.txt` / `requirements*.txt` 不得出现 `sympoint`（大小写不敏感）及未来追加的 ⛔ 清单项。

> 门禁是「告警型」还是「阻断型」？——**SymPoint 泄漏为合规红线，采用阻断型（`exit 1`）**，区别于现有 Bandit/Trivy 的告警型。

---

## 6. 准入门禁检查清单（签字前逐条核对）

- [ ] CADTransformer(MIT) / VecFormer(Apache 2.0) 许可结论书面确认，`LICENSE`+`NOTICE` 留存方案明确。
- [ ] VecFormer 权重释放后「权重许可复核」流程写入 C-10 跟踪文档。
- [ ] SymPoint V1/V2 隔离边界（§4）经**负责人/法务签字**。
- [ ] `research/sympoint-eval/` 隔离规则落地（.gitignore + 根 .dockerignore）。
- [ ] CI `license-compliance` job 上线且对 SymPoint 泄漏**阻断**（§5）。
- [ ] ArchCAD-400K 仅借方法论、不分发数据的边界写入 C-04/C-05 设计。
- [ ] FloorPlanCAD 仅评测、不进产品数据资产的边界写入 C-08/C-14 设计。
- [ ] CUDA/cuDNN 再分发限制在 `infra/k8s/` GPU 部署时复核。

---

## 7. 人工审核门禁（可执行签核，非纸面签字）

纸面签字栏已升级为**双通道可执行门禁**，由 CI 强制。必需角色：**技术负责人 / 法务合规 / Phase C 负责人**。

### 7.1 双通道 · OR 语义

每个角色可经**以下任一通道**完成签核（「密码签章两者一个通过即视为人工审核完毕」）：

| 通道 | 机制 | 状态 |
|---|---|---|
| **专用密码** | 相关人员**自设专用密码**（bcrypt 存哈希，明文不落盘）；签核时输入密码校验通过 | ✅ 已上线 |
| **电子签章** | `SealVerifier` 抽象接口 + 指纹参考实现；登记签章指纹后走验签通道 | 🔧 **预留**（接口就绪，待接入真实 CA / 数字签章） |

**门禁整体通过** = 全部必需角色均已签核（每角色密码或签章任一即可）。

### 7.2 操作（CLI）

```bash
# 相关人员各自设定专用密码（交互式，不回显）
python apps/api/scripts/model3d/phase_c_signoff.py set-password --role 技术负责人

# 密码通道签核
python apps/api/scripts/model3d/phase_c_signoff.py sign --role 技术负责人

# 电子签章通道（预留）：先登记指纹，再验签签核
python apps/api/scripts/model3d/phase_c_signoff.py set-seal  --role 法务合规 --fingerprint <FP>
python apps/api/scripts/model3d/phase_c_signoff.py seal-sign --role 法务合规 --seal-fingerprint <FP>

# 查看状态 / 门禁校验（CI 消费）
python apps/api/scripts/model3d/phase_c_signoff.py status
python apps/api/scripts/model3d/phase_c_signoff.py check --enforce-if-model-code
```

- 状态持久化于 `apps/api/data/model3d/phase_c_signoff.json`（哈希/签核记录，git 追踪、PR 可审）。
- 核心逻辑 `apps/api/services/phase_c_signoff.py`；测试 `apps/api/tests/test_phase_c_signoff.py`。

### 7.3 CI 强制（自我武装）

CI `license-compliance` job 运行 `check --enforce-if-model-code`：

- **尚无 G3/G4 模型代码**（`core/model3d/spotting/`、`fusion/`）→ 顾问态放行（`exit 0`）。
- **模型代码一旦落地** → 门禁自动武装：未完成人工审核则 **`exit 1` 阻断合入**。

> **签核生效前，G3/G4 任何模型代码不得合入 `main`。** 审计内容变更时用 `revoke` 撤销相关签核并重签。本文档随 Phase C 推进滚动更新（新增开源件须回 §3 补审）。

---

## 附：引用来源

- CADTransformer (MIT)：https://github.com/VITA-Group/CADTransformer
- VecFormer (Apache 2.0, PQ 91.1)：https://github.com/WesKwong/VecFormer ｜ https://arxiv.org/abs/2505.23395
- SymPoint 非商用许可原文：https://github.com/nicehuster/SymPoint/blob/main/LICENSE.txt
- ArchCAD-400K (NeurIPS 2025)：https://arxiv.org/abs/2503.22346
- FloorPlanCAD (CVPR 2021)：https://arxiv.org/abs/2105.07147
- 上位选型表：`docs/AI_READING_TO_3D_MODEL.md` §3.1
