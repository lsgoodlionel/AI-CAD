# Phase C 统一评测报告（C-14，滚动更新）

> 版本 V1.0 | 2026-07-10 | 任务 C-14（汇聚）
>
> 基座：`apps/api/core/model3d/eval/`（度量引擎 + 三方法编排 + 报告渲染）
> CLI：`apps/api/scripts/model3d/eval_harness.py`
> 依赖：C-07（数据切分）、C-13（融合）、C-09（真实微调，待）；参照 C-11 天花板

---

## 0. 当前状态（诚实先行）

- **基座已就绪**：度量、三方法编排、可复现报告全部实现并测试通过（17 用例）。
- **model 端为占位**：C-09 真实微调权重未就绪，「学习模型」暂由 spotting **mock** 兜底
  （规则派生），故当前 model 与 rule 近同，三方对比数字**仅验证基座连通**，非结论。
  C-09 权重到位后，**同一基座、同一切分**直接复评产出真实 M1 结论。
- **test 集须冻结**：按 C-07 **项目切分**，仅 C-18 终评解冻一次；本报告用合成 demo，不动 test 集。

---

## 1. 度量口径（锁定实现，规避与论文不可比）

| 指标 | 定义 |
|---|---|
| 匹配 | 预测框与真值框 **同类别** 且 **IoU > 0.5** → TP；未匹配预测 → FP；未匹配真值 → FN |
| 精度 / 召回 / F1 | P=TP/(TP+FP)，R=TP/(TP+FN)，F1=2PR/(P+R) |
| SQ（分割质量） | TP 的平均 IoU |
| RQ（识别质量） | TP / (TP + 0.5·FP + 0.5·FN) |
| **PQ（Panoptic Quality）** | **SQ × RQ**（FloorPlanCAD 口径） |
| 混淆矩阵 | 按位置匹配（不看类别）统计 (真值类别 → 预测类别)，含 `__missed__` / `__spurious__` |

实现见 `core/model3d/eval/metrics.py`，IoU 阈值默认 0.5。**PQ 实现随代码锁定**，避免口径漂移。

## 2. 复现方式（一键）

```bash
# 合成 demo（无需真实数据，验证基座端到端）
python apps/api/scripts/model3d/eval_harness.py --demo --out /tmp/report.md

# 真实评测（manifest = 冻结 test 集的图元 + 金标签）
python apps/api/scripts/model3d/eval_harness.py \
  --manifest <C-07 test 切分清单>.json \
  --out docs/PHASE_C_EVAL_REPORT.md --json /tmp/metrics.json
```

manifest schema：`{"samples":[{"sample_id","gt":[{category,bbox}],"primitives":[{id,type,points,layer,block}]}]}`。
金标签来自 `model_symbol_annotations`（C-16 confirmed，可经 `export_annotations.py` 导出）。

## 3. 首版对比（合成 demo — 仅验证基座，非结论）

> 样本数 1，IoU 0.5。model = mock 占位。列（框状符号）匹配正常；线型符号（梁/管）
> 因合成 GT 为零面积 bbox 无法 IoU 匹配——真实符号金标签为框状，不受此影响。

| 方法 | PQ | 精度 | 召回 | F1 |
|---|---|---|---|---|
| 纯规则 | 0.333 | 0.333 | 0.333 | 0.333 |
| 学习模型（mock 占位） | 0.333 | 0.333 | 0.333 | 0.333 |
| 融合 | 0.250 | 0.200 | 0.333 | 0.250 |

分类别 PQ：`column` 三方法均 1.0（框状符号匹配正常）；`beam` 三方法 0.0（demo 线型零面积 GT）。

## 4. 里程碑 M1 判据（待真实数据）

> **M1｜符号识别在专业域超纯规则**：在自建 test 集上，学习模型（C-09）或融合（C-13）
> 在**至少结构域**的 PQ/召回**显著优于纯规则**（本报告量化，SymPoint 为天花板参照）。

达成路线：C-09 微调权重 → 本基座在冻结 test 集复评 → 填入下表：

| 专业域 | 纯规则 PQ | 学习模型 PQ | 融合 PQ | SymPoint 天花板 | 结论 |
|---|---|---|---|---|---|
| 结构 | 待 | 待 | 待 | 待（C-11 隔离环境回流） | 待 |
| 机电 | 待 | 待 | 待 | 待 | 待 |
| 装修 | 待 | 待 | 待 | 待 | 待 |

## 5. 天花板参照（C-11，隔离环境）

SymPoint（非商用⛔，`research/sympoint-eval/` 隔离环境）**仅以数字形式**回流本报告，
量化「可商用模型（CADTransformer/VecFormer）距 SOTA 有多远」。代码与权重绝不进产品
（见 `docs/PHASE_C_LICENSE_AUDIT.md` §4，CI license 门禁强制）。

## 6. 融合正确性（结构性保证，与数据无关）

- 融合**召回 ≥ 纯规则**（规则候选全保留）——基座测试 `test_fusion_recall_not_below_rule` 断言。
- 规则强命中不被模型覆盖（C-13 arbitration）；输出均带 `source` + `confidence`。
- 精度可能因 model 补召回引入 FP 而波动（当前 mock 占位放大了此现象），C-09 真实模型 + 置信度标定后改善。
