# model3d 自建数据集 —— 目录约定

> 任务 C-05（泳道 B｜数据，关键路径）| 规范源：`docs/PHASE_C_DATASET_SPEC.md`
> 版本管理与切分：C-07（`apps/api/scripts/model3d/dataset_split.py` + `DATASHEET.md`）

本目录**只跟踪目录结构、清单（manifest）、schema 与文档**。
**图纸、权重、标注二进制等大文件一律走 MinIO / DVC，绝不进 git。**

---

## 1. 铁律：大文件不进 git

- **进 git**：本 README、`.gitkeep`、各层 `manifest.jsonl`（清单/指针）、`DATASHEET.md`（C-07）、schema 文件。
- **不进 git（走 MinIO / DVC）**：`.dwg` / `.dxf` / `.pdf` / `.svg` 原图与派生、`.npz` / 标注二进制、模型权重。
- git 侧只保留**指向 MinIO 对象的清单**（对象 key + 校验和 + 元数据），实现"可追溯不落地"。
- 数据集排除规则由主控在仓库根 `.gitignore` 统一维护——**本任务不改 `.gitignore`**。

> 涉密合规：所有原始图纸须先脱敏再入库（流程见 `docs/PHASE_C_DATASET_SPEC.md` §2.3），
> 脱敏映射（脱敏编号 ↔ 真实项目）单独加密登记，不随数据分发。

---

## 2. 目录结构

```
apps/api/data/model3d/dataset/
├── README.md              # 本文件（进 git）
├── .gitkeep               # 占位，使空目录被 git 跟踪
├── raw/                   # 原始脱敏图纸（DWG/DXF/矢量PDF）——MinIO，不进 git
│   └── manifest.jsonl     # 指针清单（进 git）：脱敏编号 / 专业 / 项目 / MinIO key / 校验和
├── weak_labeled/          # C-04 自动标注产出的弱标签样本——MinIO，不进 git
│   └── manifest.jsonl     # 指针清单（进 git）：样本 id / 弱标签版本 / 一致率抽检记录
├── gold/                  # C-06 人工精标注金标签——MinIO，不进 git
│   └── manifest.jsonl     # 指针清单（进 git）：样本 id / 标注员 / 质检 Kappa / 仲裁记录
└── splits/                # C-07 train/val/test 切分清单（按项目切分）——进 git
    ├── DATASHEET.md       # 数据卡（C-07 产出）
    └── split_v*.json      # 切分快照（固定种子，可复现；test 集冻结）
```

各层职责一句话：
- **raw**：脱敏后的矢量原图，唯一事实源。
- **weak_labeled**：C-04 图层/块 → 弱标签，负责规模冷启动（一致率 ≥70% 目标，有噪声）。
- **gold**：C-06 人工精修金标签，负责质量（Kappa ≥ 0.8），训练/评测的质量底线。
- **splits**：C-07 按项目切分的可复现快照；test 集冻结，仅 C-18 终评解冻一次。

---

## 3. 文件命名规范

统一脱敏编号命名，**不含真实项目名**（涉密）。格式：

```
{专业}_{项目脱敏号}_{图纸序号}[_{视图类型}].{ext}
```

- `专业`：`str`（结构）/ `arc`（建筑）/ `mep`（机电）/ `dec`（装修）。
- `项目脱敏号`：`p0001`、`p0002` …（脱敏映射单独加密登记）。
- `图纸序号`：`d001`、`d002` …（项目内唯一）。
- `视图类型`（可选）：`plan`（平面）/ `sect`（剖面）/ `elev`（立面）/ `detl`（详图）。
- `ext`：`dxf` / `pdf`（原图）/ `svg`（预处理派生）/ `json`（图元 JSON，见 `docs/PHASE_C_PREPROCESS_SCHEMA.md`）。

示例：`str_p0003_d012_plan.dxf`、`mep_p0007_d005_sect.svg`。

标注/样本 id 沿用同一 stem（去扩展名），保证 raw ↔ weak_labeled ↔ gold ↔ splits 全链路可对齐追溯。

---

## 4. 类别与切分口径

- **类别体系**：9 顶层类 + 分专业子类，见 `docs/PHASE_C_DATASET_SPEC.md` §1（锚定 `core/model3d/layer_conventions.py`）。
- **切分口径**：**按项目切分，非按图切分**（防同项目泄漏），test 集冻结——见规范 §4 与 C-07 脚本。
</content>
