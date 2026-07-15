# Phase D 验收报告（泳道 6 · 工程化与验收）

> 2026-07-15 | 承接 `docs/PHASE_D_BLUEPRINT.md`（6 泳道 24 工作块）
>
> 主题：把 Phase A/B/C 建成的能力串成产品（模块串联 / 合并同类入口 / 流程引导 + 前沿升级）。
> 本报告逐条核验交付、验收标准、诚实边界。

## 0. 总体验收结论

- **泳道 1-4（模块串联/操作简化）**：✅ 全部完成，已合并 main（PR #12、#15，CI 全绿）。
- **泳道 5（前沿升级，研究型）**：✅ 代码/评测基座全落地（`feat/phase-d`，8 提交待 PR）；真实数字部分卡外部条件（如实标注）。
- **泳道 6（工程化/收尾）**：✅ 路由迁移、手册同步、E2E Demo、度量埋点完成；本报告即验收产出。
- **测试**：后端全量 **1443 passed**；前端 vitest 72/72、tsc 零新增类型错误。
- **本地栈**：docker 已更新至最新代码，migration 026/027/028 应用，api/celery 健康。

## 1. 逐泳道验收

### 泳道 1 — 工作台与引导 ✅
- 项目工作台 `pages/project/Hub/`（流程 Steps + 5 卡 + 近期活动），`/`→`/hub` 默认落地。
- 统一上传向导 `DrawingList/UploadWizard.tsx`（自动分流单张/批量/ZIP）。
- 帮助内嵌 `components/HelpTip.tsx` + Help 锚点深链。

### 泳道 2 — 审查合并 ✅
- Finding 统一模型（migration 026，五源聚合 + 四态状态机）、审查中心 `pages/review/Center/`、审图→创效线索（to-proposal 只建草稿）。

### 泳道 3 — 建模串联 ✅
- 事件编排层（migration 027，建议制）、spotting 融合回灌、OCR 三馈线、LOD300 section-z 最近邻配准（+ 围护剖面剔除 + **VLM 第二标高源**）、算量中心。

### 泳道 4 — 模型页/看板 ✅
- 模型页三模式（浏览/审校/算量，20 文件）+ 统一收件箱；看板角色自适应合并 + 管线建议面板。

### 泳道 5 — 前沿升级 ✅（真数字部分见 §2 边界）
- D-16 OCR 评测基座 + PaddleOCR-VL stub + **远程 VLM 读图适配器**；D-17 docling 适配器 + A/B；D-18 GraphRAG 融合 + **接入编排(灰度关)** + 评测 harness + 自举金标准；D-19 跟踪；D-20 **本地 Mac 训练实测可行**。

### 泳道 6 — 工程化/收尾 ✅
- 路由迁移兼容、手册同步 V1.2、E2E Demo（`tests/e2e/test_phase_d_demo.py`）、北极星度量埋点、本验收报告。

## 2. 诚实边界（外部条件阻塞，非缺陷）

| 项 | 缺什么 | 现状 |
|---|---|---|
| D-17 docling 真 A/B | docling 版面模型(HF 下载受阻) + 文本型 GB 标准 PDF | 适配器 + A/B harness 就绪，`--demo` 已验证逻辑 |
| D-18 GraphRAG 真精度 | 合规标注金标准集（现审校队列无合规问题入列） | 融合 + 编排 + harness 就绪；远程 qwen3.5 核查已实证通 |
| D-20 全量微调 | Linux CUDA GPU（Mac CPU 图操作慢，全量以天计） | 训练环 smoke 已在 Mac 实测跑通（loss 单调降） |
| 歌剧院实测层高 | 竣工图缺建筑楼层剖面（24 剖面 20 张围护/基坑） | 非算法问题；VLM 第二标高源(灰度 `VLM_SECTION_Z_ENABLED`)为潜在补救 |

## 3. 关键工程结论（本轮沉淀）

1. **section-z「0 实测层高」根因是数据**（缺建筑楼层剖面），非算法——「绝不虚高」正确回落。VLM 读图可补第二标高源。
2. **D-20 本地 Mac 训练可行**：阻碍全是版本钉不对（dgl 必须 1.1.x 非 2.x、timm 同期版），非 GPU 根本限制；真实 CADTransformer 在 Mac 前向+反向跑通。
3. **远程 ollama 接入**：qwen3.5 结构化输出 + qwen3.5-vision 读图均实证；地址存 gitignored 配置、不入 github。
4. **健壮性修复**：`llm_call_logs` 月分区维护（DEFAULT 兜底 + celery beat）、router 日志写失败不阻断 LLM 调用 + model_db_id FK 修正。

## 4. 后续（另行推进）

- D-17 真 A/B（docling 模型 + GB PDF）、D-18 真精度（标注飞轮）、D-20 全量微调（GPU）。
- VLM 第二标高源在歌剧院开启灰度验证是否点亮实测层高。
- 陈旧 worktree 清理（确认无活动会话后）。
