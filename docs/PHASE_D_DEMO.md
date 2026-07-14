# Phase D 里程碑验收报告（D-23）

> 版本 V1.0 | 2026-07-14 | 对齐 `docs/PHASE_D_BLUEPRINT.md` 泳道6 D-23
>
> E2E Demo 脚本：`apps/api/tests/e2e/test_phase_d_demo.py`（DB/Celery 全量 mock，离线可复现）

---

## 0. 说明

Phase D（协同级）新增了「事件编排层」（D-08，`core/pipeline/`）与「Finding 统一聚合」
（D-05/D-07，`services/finding_service.py` + `routers/findings.py`），首次把此前散落的
「AI 审图」「工程建模」「算量」「创效提案」串成一条**自动打底、人工确认**的链路。

本 Demo 不重新实现业务逻辑，而是**直接驱动生产模块的公开函数/路由**（`tasks/ai_review.py`
的发射点、`tasks/pipeline.py` 的消费入口、`core/pipeline/handlers.py` 的判定逻辑、
`routers/findings.py` / `routers/pipeline.py` 的真实端点），用 mock DB/Celery 在进程内
串联验证：**该自动的地方是否真的无需手动触发**，**该人工确认的地方是否仍然只是建议制**。

---

## 1. 验收断言逐条勾对

| # | 断言 | 结论 | 自动化证据 |
|---|------|------|-----------|
| 1 | AI 审图完成后自动发射 `ai_review.completed` 事件并自动派发 Celery 消费，超阈值自动生成「建议重建模型」待办，全程无需人工触发 | ✅ | `test_step1_ai_review_completion_auto_emits_and_creates_rebuild_suggestion`（`events.emit_event` → `task.delay` 自动派发 → `tasks/pipeline._process` → `handlers.dispatch` → 建议落库）；`test_step1b_ai_review_task_source_wires_emit_on_completion`（源码级核实 `tasks/ai_review.py` 在 `drawings.status='ai_done'` 落库后**无条件**调用发射点，非旁路脚本触发） |
| 2 | 模型重建完成后自动刷新 QTO 并复用既有算量层真实重算，钢筋量下降超阈值自动生成「建议创建创效提案」待办 | ✅ | `test_step2_model_built_auto_refreshes_qto_and_creates_proposal_suggestion`（`build_scene_quantities` 真实调用重算、`save_quantity_summary` 落库、预估节约按默认钢价 4500 元/吨算出 36000 元 > 5000 门槛）；`test_step2b_model_build_task_source_wires_emit_on_completion`（源码级核实 `tasks/model_build.py` 在 `status='ready'` 落库后调用 `emit_model_built_event`） |
| 3 | 有创效潜力的 Finding 可转创效提案，但**必须人工显式调用**端点，产出仅 `draft`，三审签字硬约束不被绕过 | ✅ | `test_step3_finding_with_saving_potential_converts_to_draft_proposal_only`（`POST .../to-proposal` 返回 `status=draft`，草稿描述显式标注「需经二审经济师测算与签字后方可进入公示/分配流程」，且全程未触碰 `economist_signed_at` / `published` 相关 SQL） |
| 4 | 规则未命中创效潜力时诚实拒绝，绝不静默造一条空提案兜底 | ✅ | `test_step3b_finding_without_saving_potential_is_rejected_not_silently_drafted`（409 `NO_SAVING_POTENTIAL`，未写库） |
| 5 | 建议「采纳」仅是状态标记 + 审计留痕，绝不代为触发重建/建提案等硬动作 | ✅ | `test_step4_accept_suggestion_only_flips_status_never_triggers_hard_action`（DB 写操作精确为 2 次：`UPDATE pipeline_suggestions` + `INSERT audit_logs`，未触及 `project_models` / `incentive_proposals` 表）；`test_step4b_dismiss_is_symmetric_and_equally_inert`（忽略同等份量，同样不触发硬动作） |
| 6 | 自动化本身可控：项目/全局开关关闭时不生成建议，尊重项目自主选择 | ✅ | `test_step5_pipeline_step_disabled_skips_suggestion_generation`（开关关闭时甚至不查询 DB） |
| 7 | 契约文件（迁移 + Finding 来源常量）与本文档齐备，可复核 | ✅ | `test_standard_contract_files_present` |

---

## 2. 全链路（E2E Demo）

```
图纸上传 → AI 审图完成（tasks/ai_review.py）
  └─ [自动] emit_event(ai_review.completed) → Celery delay
       └─ [自动] tasks/pipeline.process_pipeline_event → handlers.dispatch
            └─ [自动，超阈值] handle_ai_review_completed → 「建议重建模型」待办

工程建模完成（tasks/model_build.py）
  └─ [自动] emit_model_built_event(model.built)
       └─ [自动] handle_model_built → build_scene_quantities 真实重算 QTO → 持久化
            └─ [自动，超阈值] 钢筋量下降换算节约额 → 「建议创建创效提案」待办

Finding 统一聚合（services/finding_service.py，五类来源）
  └─ [人工] POST /findings/{source}/{key}/to-proposal → 创效提案 draft
       （三审签字/公示/分配硬约束不被绕过）

建议待办（routers/pipeline.py）
  └─ [人工] POST /pipeline/suggestions/{id}/accept|dismiss → 仅状态标记 + 审计
       （人工仍需自行调用既有 POST /model/rebuild、POST /model/quantities/to-proposal
        等端点完成实际动作——建议本身不代为执行）
```

**自动 vs 人工边界一览**：

| 环节 | 触发方式 | 产出 | 是否可越权自动执行硬动作 |
|------|---------|------|------------------------|
| ai_review.completed → 重建建议 | 自动（审图任务内联发射） | `pipeline_suggestions` 待办 | 否——只生成建议 |
| model.built → QTO 刷新 | 自动（建模任务内联发射） | 项目级算量汇总持久化 | 是（QTO 本身是只读重算，非硬约束动作） |
| model.built → 创效提案建议 | 自动（同上，超阈值） | `pipeline_suggestions` 待办 | 否——只生成建议 |
| Finding → 创效提案 draft | **人工**显式 POST | `incentive_proposals`（status=draft） | 否——仅草稿，签字/测算仍走既有三审流程 |
| 建议 accept/dismiss | **人工**显式 POST | 状态标记 + 审计 | 否——不代为调用重建/建提案端点 |

---

## 3. 已知问题（发现但未修改，遵循测试任务文件边界）

- `core/pipeline/handlers.py` 文件头注释仍写「本工作块的文件边界不包含
  `tasks/model_build.py`，因此接线点暂不落地」，但源码复核（`test_step2b_...`）
  确认 `tasks/model_build.py` **已经**在建模成功落库后调用 `emit_model_built_event`。
  该注释是后续 PR 完成接线后遗留的过期说明，不影响功能，建议后续小改更新注释措辞。

---

## 4. 复现方式

```bash
cd apps/api
.venv/bin/python -m pytest tests/e2e/test_phase_d_demo.py -q --no-cov
```

全部用例 DB/Celery mock，无需真实 PostgreSQL/Redis，确定性可复现。
