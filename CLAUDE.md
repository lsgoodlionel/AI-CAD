# CAD — 图纸深化全过程管理平台

> 最后更新：2026-08-06 | 实现进度：Phase 0~4B 全部完成；会审审查 V4（方法论）已并入 AI 审图；Phase 5 批量读图与整套审图完成；Phase 6 工程 3D 模型基座完成（模型成为全平台成果展示主通道之一）；超级工程建模 Phase A（AI 读图→IFC/Fragments）已合并 main；超级工程建模 Phase B（算量级：跨视图 z 恢复 + 构件拓扑 + IFC-QTO 算量 + 创效打通）完成（B-01~B-24）；**Phase C（BIM 级）离线可交付部分全部完成：泳道 A 合规门禁（C-01 许可审计 + 人工审核双通道门禁 + C-11 隔离）、泳道 B 数据关键路径（C-02~C-07）、泳道 C 模型（契约基座 + C-08/C-10/C-12/C-13）、泳道 D 审校（C-15/C-16/C-17）、C-14 评测基座、C-18 验收 Demo，累计 227 测试全绿、双门禁 PASS；里程碑 M2（审校收敛返工点）达成，M1（符号识别超纯规则）基座就绪、终评数字待 C-09 真实微调（卡 GPU/脱敏数据/权重）**；**分支 `fix/model-3d-quality`（PR #11）：上海大歌剧院实测驱动的建模致命修复（渲染/幻影层/标高/sprawl/贴图/红点/未分层）+ 模型页 UX + 楼层标高人工录入通道（migration 025）+ Web 帮助中心 `/help` + 工程模型页内存优化 1.1GB→115MB（队列分页/折叠卸载/按需渲染/InstancedMesh）+ 图纸全文 OCR **真实推理落地**（RapidOCR aarch64 回退 + 大图分块识别，歌剧院剖面图实测 13 标高候选置信 0.96~1.00，`core/model3d/ocr`，34 单测，`docs/MODEL_OCR.md`）；并复核更正「compose build 坏了」误判（实为漏 `--profile app`/`up` 不带 `--build`，见 `infra/DEV.md`）**；**分支 `feat/phase-e`（22 提交，蓝图 `docs/PHASE_E_BLUEPRINT.md` V2）：确立「图纸信息档案层」为全平台数据主线（抽取一次·单一真相源·人审在环·分层消费）。E0 健康看板 500 修复 + OCR 后端随镜像交付（RapidOCR）；E1 工程信息模块（migration 029 档案表 + 抽取编排 + 聚合 API + 前端页 + 全站图纸预览）；E1.5 档案层升级（migration 030 人审 verified 层「auto/verified 分离，supersedes_key 跨重抽抑制复活」+ 导入即建档 + 档案读取契约 API + 工程信息页人审修正 UI）；E2 工程模型「轴网」显示层 + E2-consume 建模 section-z 改读档案标高（去重复 OCR，建模不再自跑 OCR）；E3-0 缺口审计（`docs/PHASE_E_E3_AUDIT.md`；**其中「无图层」结论已于 2026-08-18 被推翻——实测 path 100% 带 layer 字段，当时只查了 OCG 定义、没查 path 的 layer 属性，见蓝图 §8.6**）；**E3-B 围护桩圆检测（栅格 HoughCircles，`core/model3d/circle_detector.py`，双闸防误检，整机 columns 3089→5794 +2705 桩，真实有效）**；路径C（A1 每图坐标变换持久化 migration 031 + A2 档案轴号→3D + C-下一步档案 OCR 文字→构件类型标签 `component_labels`，代码/测试完成，整机显效受 OCR 回填覆盖 + 变换质量门控）。累计约 90 新单测全绿。待续（已入蓝图/任务）：E-末 OCR 回填提速、E3-4 板恢复+降噪、E3-5 收口文档**；**Phase F/G（扫描进度页 + 双向追溯）已合并 main**；**Phase H（实体中心装配 + 人审收敛，H1~H26，蓝图 `docs/PHASE_H_BLUEPRINT.md`）与 Phase I（建筑制图标准驱动的识别，蓝图 `docs/PHASE_I_BLUEPRINT.md`）+ 本轮系统化能力（编号无关角色判别/部分图纸建模/单体归属兜底/标高配对/档案原地重分类/分幅识别/变换比例门禁）已提交分支 `feat/phase-hij`，migration 033~044，后端 2708 passed / 0 failed**；**下阶段蓝图 `docs/PHASE_J_BLUEPRINT.md`：主线是「世界坐标规模化」（实测仅 11 张图有世界锚点 = 0.5%，`placed_drawings=0`），一个月回顾见 `docs/DEV_REVIEW_2026-07.md`。整体完成度 ≈62%，「PDF 路线准确识别 ≥90%」门槛未达 ⇒ 不启动 DWG 路线**

## 项目概述

本项目基于《全面推行图纸深化全过程管理体系》分析报告，自主开发（整合 GitHub 开源库）一套覆盖建筑施工全周期的图纸深化管理与创效平台。

核心目标：将图纸深化从"按图施工"升级为"以图创效"，通过数字化手段实现年产值 2%-3% 的隐性利润挖掘。

## 实现进度概览

| 模块 | 状态 | 关键文件 |
|------|------|---------|
| JWT 认证 + RBAC | ✅ | `routers/auth.py`, `src/access.ts` |
| 三审状态机 | ✅ | `core/workflow/`, `routers/drawings.py`, `routers/technical_review.py`, `routers/economic_review.py`, `routers/settlement_review.py` |
| MinIO 文件存储 | ✅ | `core/storage.py` |
| Celery 异步任务 | ✅ | `tasks/ai_review.py` |
| 四引擎 AI 审图框架 | ✅ | `core/ai_review/` (base/rules_engine/kg_engine/rag_engine/vision_engine/orchestrator) |
| YAML 规则引擎 | ✅ | `data/rules/common.yaml`, `structure.yaml`, `architecture.yaml`, `mep.yaml`, `decoration.yaml` |
| 模型路由层 | ✅ | `core/llm/router.py`, `core/llm/circuit_breaker.py`, `core/llm/providers/` |
| 管理后台 API | ✅ | `routers/admin/` (5 个模块) |
| 创效激励系统 | ✅ | `routers/incentive.py`, `services/bonus_calculator.py`, `services/certificate_generator.py` |
| 前端 UmiJS 骨架 | ✅ | `apps/web/src/` (app.tsx/access.ts/routes.ts/Login) |
| 管理后台前端 | ✅ | `apps/web/src/pages/admin/` (ModelManagement + EngineParams + RegulationManagement) |
| 图纸列表/详情前端 | ✅ | `apps/web/src/pages/drawings/` (含 AIReviewPanel) |
| 创效激励前端 | ✅ | `apps/web/src/pages/incentive/` |
| AI 审图报告生成（PDF/Excel）| ✅ | `services/ai_report_generator.py`, `routers/drawings.py` |
| 规范知识库管理 | ✅ | `routers/regulations.py`, `services/regulation_importer.py`, `tasks/regulation_import.py` |
| 兑现凭证 PDF | ✅ | `services/certificate_generator.py` |
| 公示期自动推进 | ✅ | `tasks/proposal_notice.py` |
| 经济测算引擎（钢筋翻样）| ✅ | `core/economic/rebar_calculator.py`, `routers/economic_calc.py` |
| 数据看板 | ✅ | `routers/dashboard.py`, `pages/dashboard/GroupDashboard/`, `pages/dashboard/ProjectDashboard/` |
| 外部规范 API 定时同步 | ✅ | `tasks/regulation_api_sync.py`, `core/celery_app.py` |
| 测试套件（pytest + E2E）| ✅ | `apps/api/tests/`, `apps/web/tests/e2e/` |
| PWA 配置 | ✅ | `public/manifest.json`, `public/sw.js`, `app.tsx` |
| CI/CD | ✅ | `.github/workflows/ci.yml` |
| K8s 生产部署 | ✅ | `infra/k8s/base/`, `infra/k8s/overlays/production/` |
| PDF 内嵌预览 | ✅ | `DrawingDetail/PdfViewer.tsx`, `DrawingDetail/index.tsx` |
| YOLOv8 图元检测 | ✅ | `core/ai_review/yolo_detector.py`, `vision_engine.py` |
| LangGraph 多轮推理 | ✅ | `core/ai_review/langgraph_agent.py`, `rag_engine.py` |
| 会审审查第5引擎（19专业蒸馏协议）| ✅ | `core/ai_review/review_audit/`, `data/review_protocol/`, `migrations/007+008` |
| 会审 V2：对象识别+场景+问题包+文书化输出 | ✅ | `review_audit/{object_identifier,scenario_router,question_pack_builder,document_writer}.py` |
| 会审 V3：SOP 逐项清单核查（蒸馏 05 SOP）| ✅ | `review_audit/checklist_runner.py`, `data/review_protocol/review_checklists.yaml`, `scripts/build_review_checklists.py`, `migrations/009` |
| 会审 V4：方法论升级（六步控制链+五维审查+闭环不足判定+结构化处理建议）| ✅ | `review_audit/{control_chain,dimension_checker,action_recommender}.py`, `data/review_protocol/review_methodology.yaml`, `migrations/011` |
| 会审审查并入 AI 审图（删除独立模块）| ✅ | 第5引擎 `review` + AI审图面板「会审审查」Tab（`ReviewFindings.tsx`）；`services/reviewAudit.ts`（共享类型）|
| Phase 5：批量上传/ZIP 整套导入 + 文件名解析 + DWG→DXF（ODA）| ✅ | `routers/drawings.py`（/batch、/import-zip）、`services/drawing_filename_parser.py`、`core/ai_review/dwg_support.py` |
| Phase 5：套图审图（单张/多张/整套）+ 跨图分析 | ✅ | `routers/review_batches.py`、`tasks/batch_review.py`、`core/ai_review/cross_drawing.py`、`migrations/012`；前端 `pages/drawings/ReviewBatch/` |
| Phase 6：工程 3D 模型基座（楼层堆叠+图纸贴图+IFC glTF+成果标记）| ✅ | `services/{floor_parser,model_builder}.py`、`tasks/model_build.py`、`routers/project_models.py`、`migrations/013`；前端 `pages/model/ProjectModel/`（three.js）+ 四处平台入口 |
| Phase 7：3D 模型 V2 构件级重建（总体/单体/柱墙梁板/机电管线设备）+ YOLOv8 | ✅ | `core/model3d/`（几何提取+确定性构件识别）、`services/model_elements.py`（scene v2 单体分组+YOLO 接线）；前端 `elementsBuilder.ts`（构件挤出渲染三模式）；ultralytics 入镜像 |
| Phase B 工作块一：图种判别（平面/剖面/立面/详图）| ✅ | `services/drawing_view_classifier.py`、`drawing_filename_parser.py`（view_type 关键词）|
| Phase B 工作块二：跨视图 z 恢复 MVP（剖面标高→真实层高，点亮 `cross_view_match` gate）| ✅ | `core/model3d/section_level_extractor.py`、`services/model_z_levels.py`、`section_z_recovery.py`、`model_story.py`（z_overrides）、`migrations/019` |
| Phase B 工作块三：立面洞口 + 构件截面表（替换硬编码梁高/板厚/管径）| ✅ | `core/model3d/elevation_opening_extractor.py`、`services/model_component_sections.py`、`migrations/020` |
| Phase B 工作块四：全三视图配准（轴网锚点+z 装配+置信降级框架）| ✅ | `core/model3d/{grid_anchor_extractor,provenance}.py`、`services/cross_view_registration.py`、`core/ai_review/cross_view_z.py` |
| Phase B 工作块五：构件拓扑规则（门窗-墙/梁-柱/板-梁，确定性纯几何）| ✅ | `core/model3d/topology_rules.py`、`services/model_topology.py`、`migrations/021` |
| Phase B 工作块六：IFC-QTO 算量（混凝土净体积/模板/钢筋 + 汇总 API）| ✅ | `services/{model_qto,model_qto_summary}.py`、`migrations/022`、`GET /projects/{id}/model/quantities` |
| Phase B 工作块七：QTO → 创效激励打通（草稿入三审硬约束）| ✅ | `routers/project_models.py`（`POST /model/quantities/to-proposal`）|
| Phase B 工作块八：测试与里程碑 E2E Demo（合成整套图，验收总标准 1–5）| ✅ | `tests/e2e/test_phase_b_demo.py`、`tests/test_phase_b_edge_cases.py`、`docs/PHASE_B_DEMO.md` |
| Phase C 泳道 A｜合规（C-01）：开源件许可证审计（CADTransformer MIT/VecFormer Apache 放行；SymPoint ⛔ 隔离）+ CI 阻断型 license 门禁 | ✅ | `docs/PHASE_C_LICENSE_AUDIT.md`、`.github/workflows/ci.yml`（`license-compliance`）、`.gitignore`/`.dockerignore`（`research/sympoint-eval/` 隔离） |
| Phase C 泳道 A｜人工审核门禁（C-01 签字栏升级）：密码 + 电子签章（预留）双通道 OR 语义，CI 随模型代码自我武装阻断 | ✅ | `services/phase_c_signoff.py`、`scripts/model3d/phase_c_signoff.py`、`data/model3d/phase_c_signoff.json`、`tests/test_phase_c_signoff.py` |
| Phase C 泳道 B｜数据（C-02）：DXF/DWG/PDF → 统一 SVG + 图元 JSON 预处理器（复用 geometry_extractor/dwg_support，图层/块弱标签透传，优雅降级）| ✅ | `core/model3d/preprocess/{__init__,schema,primitive_json,dxf_to_svg}.py`、`scripts/model3d/preprocess_drawing.py`、`tests/test_preprocess.py`、`docs/PHASE_C_PREPROCESS_SCHEMA.md` |
| Phase C 泳道 B｜数据（C-03）：块 INSERT 递归展开（嵌套/缩放旋转/MINSERT 阵列）+ 每图元保留块名·图层弱标签（修 C-02 线段丢块名缺口）+ 坐标等比归一化到 [0,1] | ✅ | `core/model3d/preprocess/{block_expander,normalize}.py`、`tests/test_block_expander.py`、`tests/test_normalize.py` |
| Phase C 泳道 B｜数据（C-04，关键路径）：图层/块属性 → 弱标签自动标注引擎（复用 layer_conventions 基础分类器 + 补充映射表，9 类/4 系统硬约束，弱标注质量报告）| ✅ | `core/model3d/dataset/auto_label.py`、`data/model3d/layer_class_map.yaml`、`tests/test_auto_label.py` |
| Phase C 泳道 B｜数据（C-05）：中文专业域数据集冷启动规范（symbol taxonomy 精化 9 类 + 采集/脱敏规范 + 分专业目标样本量 + FloorPlanCAD 交叉参照）| ✅ | `docs/PHASE_C_DATASET_SPEC.md`、`data/model3d/dataset/{README.md,.gitkeep}` |
| Phase C 泳道 B｜数据（C-06）：人工精标注规范 + 质检（双人交叉+仲裁，IoU/Kappa≥0.8 硬门槛，复用 DrawingAnnotationQueue 工具，金标签回流数据飞轮）| ✅ | `docs/PHASE_C_ANNOTATION_GUIDE.md`、`docs/PHASE_C_ANNOTATION_QC_TEMPLATE.md` |
| Phase C 泳道 B｜数据（C-07）：数据集版本/切分（**按项目切分防泄漏** + 固定种子可复现 + test 集冻结 + 数据卡）| ✅ | `scripts/model3d/dataset_split.py`、`data/model3d/dataset/DATASHEET.md`、`tests/test_dataset_split.py` |
| Phase C 泳道 C｜模型契约基座：符号候选契约 + 后端 Protocol + 离线 mock（复用 auto_label 让无 GPU 链路端到端可跑）| ✅ | `core/model3d/spotting/{__init__,types,mock_backend}.py` |
| Phase C 泳道 C｜模型（C-08）：CADTransformer(MIT) 推理封装 PoC（adapter 纯函数可测 + torch/dgl 懒加载 + 无权重/GPU 优雅降级 + 依赖锁定/Dockerfile 片段）| ✅ | `core/model3d/spotting/cadtransformer/*`、`requirements-spotting.txt`、`tests/test_cadtransformer_backend.py` |
| Phase C 泳道 C｜模型（C-12）：符号 spotting 推理微服务（接 ModelRouter 引擎治理/日志，后端有序回退 mock，离线可测）| ✅ | `core/model3d/spotting/service.py`、`routers/model_spotting.py`、`migrations/023_symbol_spotting.sql`、`tests/test_model_spotting.py` |
| Phase C 泳道 C｜模型（C-13）：学习模型×确定性规则融合引擎（规则强命中不被覆盖 + 模型补召回 + 冲突仲裁，输出带 source+confidence）| ✅ | `core/model3d/fusion/*`、`data/model3d/fusion_policy.yaml`、`tests/test_fusion_engine.py` |
| Phase C 泳道 C｜模型（C-10 旁路）：VecFormer(Apache2.0) 权重释放跟踪 + 迁移预研 + 占位 stub（同实现 SpottingBackend）| ✅ | `docs/PHASE_C_VECFORMER_WATCH.md`、`core/model3d/spotting/vecformer/__init__.py` |
| Phase C 泳道 C｜C-09 微调 / C-11 SymPoint 天花板评测 | ⏳ 顺延 | 卡 GPU/自建数据/隔离环境（C-11 在 gitignore 的 `research/sympoint-eval/` 跑）；C-09 待 C-08+GPU+C-07 数据 |
| Phase C 泳道 D｜审校契约基座：人审动作埋点表 + 符号标注表 + 前端共享类型/端点 | ✅ | `migrations/024_review_actions.sql`、`apps/web/src/services/modelReview.ts` |
| Phase C 泳道 D｜前端审校（C-16）：DrawingAnnotationQueue 深化（符号级候选框+置信度着色+确认/否定/改类/补框，标注+埋点双写，COCO 导出喂 C-09）| ✅ | `pages/model/ProjectModel/DrawingAnnotationQueue.tsx`、`routers/model_annotations.py`、`scripts/model3d/export_annotations.py`、`tests/test_model_annotations_router.py` |
| Phase C 泳道 D｜前端审校（C-15）：SemanticReviewQueue 深化（拓扑闭合/命名/规范人审，低置信+规则-模型冲突优先排队，写埋点+audit_logs）| ✅ | `pages/model/ProjectModel/SemanticReviewQueue.tsx`、`routers/model_review.py`、`tests/test_model_review.py` |
| Phase C 泳道 D｜前端审校（C-17）：返工点埋点度量看板（确认/改类/否定/补框率 by 专业 by 类别 + 收敛趋势，rework=reclass+reject+addbox，25–30% 效率口径）| ✅ | `routers/dashboard.py`（扩展 model-review-metrics）、`pages/model/ProjectModel/ModelQualityPanel.tsx`、`tests/test_model_review_metrics.py` |
| Phase C 汇聚（C-14）：统一评测基座（纯规则 vs 学习模型 vs 融合，PQ/精度/召回/F1/分专业分类别/混淆矩阵，度量口径锁定，一键复现）| ✅ | `core/model3d/eval/{metrics,harness,report}.py`、`scripts/model3d/eval_harness.py`、`tests/test_eval_harness.py`、`docs/PHASE_C_EVAL_REPORT.md`（model 端待 C-09 真实权重复评出 M1 结论）|
| Phase C 收口（C-18）：里程碑 Demo + 验收报告（逐条勾对 6 项验收总标准，M2 达成/M1 基座就绪终评待 C-09，能力边界如实）| ✅ | `docs/PHASE_C_ACCEPTANCE.md`、`tests/e2e/test_phase_c_demo.py`（离线端到端断言标准 1/3/4/5/6 + 标准 2 基座就绪）|
| **Phase I｜建筑制图标准驱动的识别能力**（蓝图 `docs/PHASE_I_BLUEPRINT.md`）：真值基准驱动 + **架构反转为「轴号圈锚定轴线」**——轴号圈三张图 **100% 精确**（108/108、107/107、126/126），轴线位置 **95%（103/108）近零误检**（旋转分区由过检 75 条变为**精确 31 条**）；**轴号 99/99 且三区逐项无偏移**（附加轴线曾夹在主序列中间致 2-H 被标成 2-J；字符个数判据只得 6/8——漏的字形在 x 上接触并成一簇——改用 **`/` 笔画长度比**：字母斜画 0.42 vs `/` 0.47~0.48，**0.43~0.46 三图全空**，8/8 检出）——OCR 8 种配置最好 1/24 被否掉后，改按 §8.0.3 编写顺序**推导**（轴号递增⇔法向偏移递减）+ §8.0.5 分区分组，分区**编号**人工确认每区一次；**坐标标注→世界锚点 15 处、RMSE 5.7 毫米**（引线 16/16 检出+读出，OCR 在坐标上置信 0.96~1.00 而在轴号上仅 1/24，差别在字号；负号丢失用「簇共识+RANSAC 变换」双路修复，19% 粗差下最小二乘 RMSE 72.5m 残差无分界故必须 RANSAC）；**M-I5 接进建模**——轴号对锚点 8 条落 `axis_intersections` + 用实测比例补 `drawing_transform`，**placements 求解成功（旋转 70.29°、RMSE 6.1 毫米、suspect=False，此前恒为 0）**，顺带修掉共享缺陷「相似变换不支持反射」（工程坐标 X=北/Y=东 是左手系，不支持时残差 105m 被 suspect 正确拒绝）；**I-5.1 识图规则内置** `core/model3d/drawing_conventions.py`——国标条款单一来源（`CLAUSES` 登记条款原文/生效模块/实测依据，第 8 章七条全在册）+ **可执行校验** `validate_axis_labels`（§8.0.3 类型与连续/§8.0.4 跳 I·O·Z/§8.0.5 分区一致/§8.0.6 附加轴线不占主序号），三张图违规 0 条；圈径、字母序列、线型语义三处常量改为回指单一来源（此前各写一遍会漂移） | ✅ **M-I1~M-I5 全部达成**；外轮廓/特殊轴网/全字符识别待攻 |
| **Phase I 接入系统**：识别链路从一次性脚本变为产品能力——migration 041（识别结果一图一行 + **人工确认分区号单独存表**不被重跑冲掉）、`services/{axis_recognition,axis_recognition_repo}.py`（**OCR 可注入**故离线全可测）、`tasks/axis_recognition.py`（项目扇出+单图，与 drawing_info_extract 同构）、`routers/axis_recognition.py`（5 端点含**确认分区号**）、`pages/project/Info/AxisRecognitionPanel.tsx`（分区号确认/粗错清单/违规清单）；修掉两个真问题：从验证脚本带进来的魔数「主带≥8 成员」（小图纸分区会被整个漏掉，改用「配上垂直对没有」）、**未确认分区号时锚点串图**（轴号退化成裸标签致两分区 `1×A` 撞身份，锚点 8→7，改为只为已确认分区写锚点）；**识别轴号已反哺 3D 场景**（`axes_to_scene` 与 archive 同契约、最后合并由它升级标签；实测 99 条→scene x=39/y=29 **零重复标签**与真值吻合，31 条斜轴按设计跳过）；闭环验证：确认分区号→重跑→锚点 7→8、scene 轴号 `1-1…2-15`/`1-A…2-P` 无重复 | ✅ 已交付 | `core/model3d/{drawing_conventions,axis_label_circle,axis_label_band,axis_zone_grouping,axis_label_derive,coord_annotation,axis_label_glyph,axis_normal,line_type_classifier,vector_axis_extractor}.py`、`services/{axis_world_anchors,drawing_anchor}.py`、`scripts/model3d/render_region.py`、`tests/fixtures/axis_ground_truth.py`（真值两重交叉校验：尺寸链 + 圈数 **108=100 主圈+8 附加**；旧写法「99 主轴线+9 附加」假设一轴一圈，而 §8.0.2 允许两端各注一个，已更正）；GB/T 50001 §8.0.2（圈 8~10mm·圆心在轴线延长线上）/§8.0.3（依次注写）/§8.0.5（分区编号→一图三套轴网）/§8.0.6（**附加轴线分数式 `2-1/k`**）；修掉三个真 bug + **证伪自己上一版的 92%**（旧分母 52 漏 17 条，完整分母下实为 71%）|
| **Phase H｜实体中心装配 + 人审收敛**（蓝图 `docs/PHASE_H_BLUEPRINT.md`，H1~H26）：以「统一轴号」为主键的数据关联；大模型三职责限定为**读意图/仲裁/分区，不做几何**；阅图顺序作为装配编排原则；**图框字段区域记忆**（框一次、记住、跨项目复用）、**交叉点定位**（把整张图摆进工程坐标系）、构件按工程坐标摆放、**自学习闭环**（人工标注反哺自动识别）、人工标定轴线基准（绕开 OCR 瓶颈）、图框「专业」栏修正专业、按图纸目录组织整套图。诚实边界：`with_grid` 覆盖 0→2252（27.8%）**仍是部分配准**，完整配准需上游轴网双向检测改进 | ✅ 已交付 | migration 033~040、`services/{component_*,learning_*,title_block_*,manual_axis,axis_line_memory,axis_intersection_repo,drawing_directory,non_scaled_drawings}.py`、`components/{AxisCalibrator,TitleBlockPicker,ZoomPanViewer,DrawingTraceDrawer}.tsx` |
| **本轮系统化能力｜编号无关化 + 部分图纸建模**（用户约束：「图纸编号只适用于一个工程，系统底层逻辑要适用于各类编号，统一兜底标准是国标，还要预留部分图纸建模」）：`drawing_role` 三级级联（内容→国标术语→学到的编号模式），**零编号知识判出 89.1%**、加学到的 148 个编号段后 **90.8%**；`partial_set` 四场景各自正确降级；`building_unit_fallback` 四态（原报「1866 未分配」**虚高 2.1 倍**，实为 907）；`level_elevation_pairing`+`level_elevation_overrides` 让标高覆盖 **0 层→10 层**（按单体分组 + §11.8.5 正负号校验 + 孤证不立）；`archive_reclassify` **原地**重分类（改判据不必重抽 OCR，扫 103 万行改 12.66 万行）；`multi_view_split` 分幅识别（判据：§8.0.5 分区双向标轴号，投影图单向）。**五条设计约束**见 `docs/MODELING_PIPELINE_BLUEPRINT.md` §7：图号体系不得硬编码/兜底标准是国标/降级必须可见/缺失不得阻断/判不出就说判不出 | ✅ 已交付 | migration 042~044、`services/{drawing_role,partial_set,building_unit_fallback,level_elevation_pairing,level_elevation_overrides,archive_reclassify,multi_view_split}.py`、`SetCapabilityPanel.tsx` |
| **本轮修掉的真缺陷**：①**符号场闸误杀 451 张核心平面图**（我上一轮加的 `MAX_BANDS=40`，而一层完整平面图有 42 条带；三个替换判据均被实测证伪——喷淋图最长带 58 > 平面图 24）→ 改为只标记不拦截，A-10-04C 轴线 0→178；②**变换比例无上限**，最离谱 **1:335 万**，**且 35 张的 confidence 全是 1.00**（旧公式衡量的是轴号识别质量，与比例对错无关）→ §6.0.4 比例门禁 + 吸附离散值；③**P2 接线静默失效**（字段名写错被宽泛 `except`+info 吞掉，整条通道从未生效）；④**标高来源到不了界面**（只写进 `story_tables`，而前端读 `scene.floors[]`，标签从未显示过）；⑤轴网聚合**结果依赖输入顺序**；⑥同名轴号冲突（真正含义是**变换不一致**，非标签写错）；⑦标高被说明文字污染 **18127 条（47%）** | ✅ 全部已修 | 见 `docs/DEV_REVIEW_2026-07.md` §3 |
| 工程 3D 模型操作手册（用户版 + 管理员版，覆盖界面操作/构建流程/API/能力边界/降级/合规/安全遗留项）**边开发边更新** | ✅ | `docs/MODEL_MANUAL_USER.md`、`docs/MODEL_MANUAL_ADMIN.md`（迭代模型/API/权限/边界时须同步更新对应章节 + 文末版本历史登记）|
| `fix/model-3d-quality`｜建模致命修复（上海大歌剧院实测：渲染空白→13层/幻影层42→13/标高±400m→真实/sprawl 2583m→397m/贴图/红点2万→1500/未分层噪声清零）| ✅ | `services/{model_story,model_builder}.py`、`core/model3d/element_recognizer.py`、`core/storage.py`（MinIO 公网端点）、`docs/MODEL_EVAL_SGOH.md` |
| `fix/model-3d-quality`｜工程模型页 UX（折叠面板 + 3D 视角控制按钮 + 边框）| ✅ | `pages/model/ProjectModel/{CollapsiblePanel,ModelViewer,index}.tsx` |
| `fix/model-3d-quality`｜楼层标高人工录入/校正通道（自动打底→人工校正，累加层高抬升上层）| ✅ | migration 025、`services/model_story_manual.py`、`routers/project_models.py`、`StoryHeightPanel.tsx` |
| `fix/model-3d-quality`｜Web 帮助中心 `/help`（用户/管理员手册按角色切，零依赖 md 渲染，构建前同步 docs→public/manual）| ✅ | `pages/Help/{index,Markdown}.tsx`、`scripts/copy-manuals.mjs`、`config/routes.ts` |
| `fix/model-3d-quality`｜工程模型页内存优化 **1.1GB→115MB**（队列分页/折叠即卸载/重队列默认折叠/按需渲染/标记 InstancedMesh/设备逐层合并 faceIndex）| ✅ | `pages/model/ProjectModel/{DrawingAnnotationQueue,CollapsiblePanel,ModelViewer,sceneBuilder,elementsBuilder,index}.tsx`、`__tests__/instancing.test.ts` |
| `fix/model-3d-quality`｜图纸全文 OCR（核心功能）**真实推理落地**：PaddleOCR/RapidOCR 有序回退（paddle 在 linux/aarch64 SIGSEGV → `CAD_OCR_DISABLE_PADDLE=1` + RapidOCR）+ 大图分块识别（26→261 token）+ 分类 + 下游接入缝；歌剧院剖面图实测 13 标高候选置信 0.96~1.00 | ✅（下一步：wiring 到 section-z/配准/语义）| `core/model3d/ocr/{types,classify,paddle_backend,rapid_backend,mock_backend,service,consume}.py`、`scripts/model3d/ocr_drawing.py`、`tests/test_model3d_ocr.py`（34 例）、`docs/MODEL_OCR.md` |
| `fix/model-3d-quality`｜开发环境认知更正（compose 没坏，是漏 `--profile app`/`up` 不带 `--build`）+ 权威工作流文档 | ✅ | `infra/DEV.md`、`infra/docker-compose.dev.yml`、`docs/DEV_HANDOFF.md` |
| Phase E｜E0 健康看板 500 修复（daily 费用 SQL make_interval + 断路器 redis key str/bytes 兼容）+ OCR 后端随镜像交付（Dockerfile `OCR_EXTRA` 层，aarch64 装 RapidOCR）| ✅ | `routers/admin/call_logs.py`、`apps/api/Dockerfile`、`tests/test_admin_call_logs.py` |
| Phase E｜E1 工程信息模块（图纸信息档案存储底座 + 抽取编排 + 聚合 API + 前端页 + 全站图纸预览）| ✅ | migration 029、`services/drawing_info_extractor.py`、`tasks/drawing_info_extract.py`、`routers/project_info.py`、`routers/drawings.py`(preview)、`pages/project/Info/`、`components/DrawingPreviewModal.tsx`、`services/projectInfo.ts` |
| Phase E｜E1.5 档案层升级：人审 verified 层（auto/verified 分离，`supersedes_key` 跨重抽抑制复活）+ 导入即建档 + 档案读取契约 API + 工程信息页人审修正 UI | ✅ | migration 030、`services/drawing_archive.py`、`routers/drawing_archive.py`、`pages/project/Info/index.tsx` |
| Phase E｜E2 工程模型「轴网」显示层（scene 携带 axes + three.js 轴线/轴号渲染 + elementFilter 显隐）+ E2-consume 建模 section-z 改读档案标高（去重复 OCR，无档案降级）| ✅ | `services/model_elements.py`(axes)、`services/model_builder.py`(`_section_levels_from_archive`)、`pages/model/ProjectModel/{elementsBuilder,sceneBuilder}.ts` |
| Phase E｜E3-0 缺口审计：歌剧院 2309 图 100% PDF、矢量文字取不到、识别器无欠费（近方 poly 13↔识别 13）；桩/钢柱是圆/线段簇表达。**⚠「无图层」结论已推翻（2026-08-18）：实测 path 100% 带 layer，只是命名不规范、命中率 6.6%** | ✅ | `docs/PHASE_E_E3_AUDIT.md` |
| Phase E｜**E3-B 围护桩/圆柱圆检测（栅格 HoughCircles，双闸：仅平面图 view_type=plan + 仅结构/通用；0.5m 下限排钢筋；去重）——整机 columns 3089→5794（+2705 桩），剖面零误检** | ✅ | `core/model3d/circle_detector.py`、`services/model_elements.py`、`tests/test_circle_detector.py` |
| Phase E｜路径C：A1 每图坐标变换持久化（`drawing_transform` pt→米）+ A2 档案轴号→3D（好标签升级识别路径 "X"）+ C-下一步 档案 OCR 文字→构件类型标签（钢构/幕墙/围护桩，就近关联，不新增顶层类别）| ✅ 代码/测试完成（整机显效受 OCR 回填覆盖+变换质量门控）| migration 031、`services/drawing_transform.py`、`services/model_elements.py`(archive_axes_to_scene/type labels)、`core/model3d/component_labels.py` |

---

## 技术栈（最终确认）

### 前端
- **框架**: React 18 + TypeScript + Vite
- **UI 组件**: Ant Design 5 + [ant-design-pro](https://github.com/ant-design/ant-design-pro)（管理后台基座）+ [pro-components](https://github.com/ant-design/pro-components)
- **图纸预览**: [react-pdf-viewer](https://github.com/react-pdf-viewer/react-pdf-viewer)（PDF 在线预览）
- **PDF 生成**: [pdfme](https://github.com/pdfme/pdfme)（审查报告、兑现凭证）
- **移动端**: PWA（Service Worker + Manifest），保留原生 App 升级路径

### 后端
- **框架**: FastAPI (Python 3.12+) + SQLAlchemy 2.0 + Alembic
- **工作流**: [transitions](https://github.com/pytransitions/transitions)（状态机，三审流程核心）
- **异步任务**: Celery + Redis（AI 审图异步处理、通知推送）
- **API 响应**: 统一信封格式（success / data / error / meta）

### AI 审图微服务（四引擎架构）
- **DXF/DWG 解析**: [ezdxf](https://github.com/mozman/ezdxf)
- **IFC/BIM 解析 + 碰撞检测**: [IfcOpenShell](https://github.com/IfcOpenShell/IfcOpenShell)
- **PDF 解析 + 批注**: [PyMuPDF](https://github.com/pymupdf/PyMuPDF)
- **PDF → LLM 文本**: [pymupdf4llm](https://github.com/pymupdf/pymupdf4llm)
- **OCR + 图元识别**: PaddleOCR（扫描版图纸文字识别）+ YOLOv8（图元检测）
- **LLM 编排**: [LangChain](https://github.com/langchain-ai/langchain) + [LangGraph](https://github.com/langchain-ai/langgraph)
- **知识图谱**: Apache AGE（PostgreSQL 扩展，规范 Cypher 查询）
- **向量数据库**: [Chroma](https://github.com/chroma-core/chroma)（规范语义检索，RAG 引擎）
- **3D BIM 预览**: [speckle-server](https://github.com/specklesystems/speckle-server)（可选集成）

### 数据与存储
- **主库**: PostgreSQL 16（含 Apache AGE 扩展，支持图数据库）
- **缓存/队列**: Redis 7（会话缓存 + Celery 队列 + 断路器状态）
- **向量存储**: Chroma（独立服务）
- **文件存储**: MinIO（图纸、报告、图集，AES-256 加密）

### 基础设施
- **Excel 处理**: openpyxl（规范导入、报告生成）
- **Word 处理**: python-docx（规范文件导入）
- **测试**: Pytest + Playwright
- **容器**: Docker Compose（开发）→ Kubernetes（生产）
- **监控**: Prometheus + Grafana

---

## 工作目录结构

```
CAD/
├── CLAUDE.md                  # 本文件
├── docs/                      # 文档与分析报告
│   ├── PRD.md                 # 产品需求文档（V3.0，含实现状态）
│   ├── ARCHITECTURE.md        # 系统架构设计（V2.0）
│   ├── PLAN.md                # 开发计划（V4.0，含完成标记）
│   └── source/                # 原始参考文档
├── apps/
│   ├── web/                   # 前端 UmiJS Max 应用（已实现）
│   │   ├── package.json       # UmiJS Max + Ant Design 5 + ProComponents
│   │   ├── .umirc.ts          # UmiJS 配置（代理/标题/布局）
│   │   ├── config/
│   │   │   └── routes.ts      # 路由配置（图纸/激励/管理后台/404）
│   │   └── src/
│   │       ├── app.tsx        # 全局运行时（getInitialState/request/layout）
│   │       ├── access.ts      # RBAC 访问控制（6 个权限维度）
│   │       ├── services/
│   │       │   └── drawings.ts  # 图纸 + 三审 API 调用封装
│   │       └── pages/
│   │           ├── Login/         # 登录页（JWT 存储 + redirect）
│   │           ├── 404.tsx
│   │           ├── drawings/
│   │           │   ├── DrawingList/   # 图纸列表（ProTable）
│   │           │   └── DrawingDetail/ # 图纸详情 + 三审面板 + AI 审图报告
│   │           │       ├── TechnicalReviewPanel.tsx
│   │           │       ├── EconomicReviewPanel.tsx  # 403 ECONOMIC_REVIEW_NOT_SIGNED 已处理
│   │           │       ├── SettlementReviewPanel.tsx # 403 QUOTA_SHEET_MISSING 已处理
│   │           │       └── AIReviewPanel.tsx        # AI 审图问题列表 + PDF/Excel 下载
│   │           ├── incentive/
│   │           │   ├── ProposalList/  # 提案列表（漏斗状态）
│   │           │   └── ProposalDetail/ # 详情（测算/签字/分配）
│   │           └── admin/
│   │               ├── ModelManagement/ # 模型管理五标签页
│   │               ├── EngineParams/    # 引擎业务参数配置
│   │               └── RegulationManagement/ # 规范知识库（文件/条文/API源/搜索）
│   ├── api/                   # 后端 FastAPI 应用（已实现）
│   │   ├── main.py            # 15 个 Router 注册
│   │   ├── core/
│   │   │   ├── auth.py        # JWT 签发/验证（Access 24h + Refresh 30d）
│   │   │   ├── storage.py     # MinIO 封装（presigned URL 5min）
│   │   │   ├── llm/           # 模型路由层
│   │   │   │   ├── providers/ # Anthropic/OpenAICompat/Ollama/CustomHTTP
│   │   │   │   ├── router.py  # ModelRouter（30s 缓存 + 断路器 + 回退链 + 日志）
│   │   │   │   └── circuit_breaker.py
│   │   │   └── ai_review/     # 四引擎框架
│   │   │       ├── base.py    # DrawingContext / AIIssue / BaseEngine
│   │   │       ├── rules_engine.py  # YAML DSL 规则引擎
│   │   │       ├── kg_engine.py     # AGE Cypher + SQL 降级
│   │   │       ├── rag_engine.py    # Chroma + LangGraph 三步推理
│   │   │       ├── vision_engine.py # ezdxf/fitz/PaddleOCR + YOLO
│   │   │       ├── yolo_detector.py # YOLOv8 图元检测（graceful degradation）
│   │   │       ├── langgraph_agent.py # LangGraph 三步推理代理
│   │   │       └── orchestrator.py  # Vision串行 → [Rules/KG/RAG]并行
│   │   ├── routers/
│   │   │   ├── auth.py
│   │   │   ├── drawings.py          # 含 AI 审图问题/PDF/Excel 端点
│   │   │   ├── technical_review.py
│   │   │   ├── economic_review.py   # 403 ECONOMIC_REVIEW_NOT_SIGNED
│   │   │   ├── settlement_review.py # 403 QUOTA_SHEET_MISSING
│   │   │   ├── incentive.py         # 创效提案全生命周期
│   │   │   ├── regulations.py       # 规范书/条文/API源/文件导入/搜索
│   │   │   └── admin/               # 5 个管理模块
│   │   ├── services/
│   │   │   ├── bonus_calculator.py      # 铁三角分配（Decimal 精确计算）
│   │   │   ├── ai_report_generator.py   # PyMuPDF 批注版 PDF + openpyxl Excel
│   │   │   ├── certificate_generator.py # 兑现凭证 A4 PDF
│   │   │   ├── regulation_importer.py   # NLP 提取流水线（pymupdf4llm/Haiku/Sonnet）
│   │   │   ├── audit.py
│   │   │   └── notification.py
│   │   ├── tasks/
│   │   │   ├── ai_review.py             # Celery 任务驱动四引擎
│   │   │   ├── proposal_notice.py       # 公示期到期自动推进状态
│   │   │   ├── regulation_import.py     # MinIO → NLP 流水线 → DB/AGE/Chroma
│   │   │   └── regulation_api_sync.py   # 外部规范 API 定时同步（每小时 beat）
│   │   ├── data/rules/
│   │   │   ├── common.yaml          # 通用规则（CMN-001~005）
│   │   │   ├── structure.yaml       # 结构专业规则（STR-001~006）
│   │   │   ├── architecture.yaml    # 建筑专业规则（ARC-001~008）
│   │   │   ├── mep.yaml             # 机电专业规则（MEP-001~008）
│   │   │   └── decoration.yaml      # 装修专业规则（DEC-001~007）
│   │   ├── dependencies.py
│   │   ├── requirements.txt
│   │   └── migrations/
│   │       ├── 001_initial_schema.sql   # 核心业务表
│   │       └── 002_model_management.sql # 模型路由管理表
│   └── ai-review/             # AI 审图微服务（目录预留）
├── packages/
│   ├── shared-types/          # 共享 TypeScript 类型（待创建）
│   └── ui-components/         # 公共 UI 组件库（待创建）
├── infra/
│   ├── docker-compose.yml     # 开发环境编排（✅ PG+AGE/Redis/MinIO/Chroma/minio-init）
│   └── k8s/                   # 生产部署配置（✅ 已完成）
│       ├── base/              # Kustomize 基础层（namespace/configmap/所有 Deployment/Service/Ingress/监控）
│       └── overlays/production/ # 生产 overlay（3副本 + 生产镜像 ${IMAGE_TAG}）
├── scripts/                   # 构建与运维脚本（待创建）
└── packages/                  # 共享包（待创建：shared-types / ui-components）
```

---

## 核心业务模块

### 1. 三审三算工作流引擎（最高优先级）

- **一审（技术规范化）**: AI 规范复核 + BIM 碰撞检查，项目总工确认
- **二审（经济最优化）**: 多方案商务对比（≥2 方案），经济师在线签字——**一票否决核心节点**
- **三审（结算合规化）**: 可结算蓝图 + 限额领料单，发布到班组

**强制约束**: 经济师未签字 → 系统 API 层硬拦截（HTTP 403），前端入口禁用

### 2. AI 智能审图系统（自建四引擎）

**引擎 1 — 规则引擎**（强条硬编码，100% 确定性）
- YAML DSL 定义规范规则
- 几何/阈值检查（消防分区面积、疏散距离、钢筋锚固长度公式）
- 零 LLM 调用，毫秒级响应

**引擎 2 — 知识图谱推理引擎**（条件合规，Apache AGE）
- 规范条文知识图谱：RegBook → Chapter → Article → Condition → Requirement
- 义务等级：MUST / SHOULD / MAY / MUST_NOT
- NLP 提取流水线：pymupdf4llm → Haiku 批量分类 → Sonnet 深度提取 → AGE 图存储
- Cypher 查询推理条件合规（IF A THEN MUST B）

**引擎 3 — RAG + LLM 引擎**（语义扩展，LangChain）
- Chroma 向量检索，Top-K 规范匹配
- LangGraph Agent 多轮推理
- 覆盖规则引擎和 KG 引擎未覆盖的模糊条文

**引擎 4 — 视觉/OCR 引擎**（扫描图纸）
- PaddleOCR 文字识别（标注、说明文字）
- YOLOv8 图元检测（钢筋符号、预留洞标识）
- 扫描版图纸处理补充

**经济测算层**（独立业务价值）
- 钢筋翻样：GB50010-2010 锚固/搭接公式，抗震系数（一二级1.15/三级1.05/四级1.00）
- 下料优化：遗传算法，目标废料率 ≤ 1.5%
- 对比原始方案，自动生成优化建议

### 3. 模型路由层（运行时热切换）

所有 AI 引擎调用统一经过 `ModelRouter`：

```
引擎调用 → ModelRouter.route(engine_name, messages)
    ↓ 查询 DB（30s 缓存）
    → 选取 primary 配置（temperature/max_tokens/top_p 等）
    → 调用对应 Provider（Anthropic/OpenAI兼容/Ollama/自定义HTTP）
    → 断路器检查（Redis 分布式状态）
    → 失败时按序回退 fallback_1 → fallback_2
    → 异步记录调用日志（engine/model/tokens/延迟/费用/成功率）
```

**14 个预定义引擎名称**:
- `regulation_classifier` / `regulation_extractor`（规范 NLP 提取）
- `kg_compliance_reasoning` / `kg_suggestion_generator` / `kg_diff_analyzer`（KG 引擎）
- `rag_qa` / `rag_rewriter`（RAG 引擎）
- `rebar_annotation_parser` / `cost_explanation_writer` / `optimization_hint_writer`（经济测算）
- `report_summary_writer`（报告生成）
- `drawing_visual_analyzer`（视觉引擎）
- `drawing_semantic_vlm`（VLM 语义引擎，Phase A：读图名/标题栏/判专业/跨图提示，本地 Ollama / 云端 DashScope 热切换，种子见 `migrations/018_vlm_engine_seed.sql`）
- `incentive_description_writer`（激励描述）

### 4. 规范知识库（三途径输入）

- **页面手动录入**: 管理后台表单，保存后自动向量化
- **文件批量导入**: PDF / Word / Excel，异步处理，人工确认后发布
- **外部 API 接入**: 配置端点和认证，定时增量同步
- 管理后台：增删改查、发布/下线控制、版本管理

### 5. 创效激励分配系统

- 净节约额在线测算（公式：A - B - C）
- 三方签字顺序约束（项目经理 → 经济师 → 集团总监）
- 铁三角分配（集团 20% / 项目团队 50% / 提案人 30%）
- 兑现记录与凭证 PDF 生成

---

## 模型路由配置

### 提供商类型

| 类型 | 说明 | 配置方式 |
|------|------|---------|
| `anthropic` | Claude API | `api_key_env` 指定环境变量名 |
| `openai_compat` | OpenAI / DeepSeek / Qwen 等 | `base_url` + `api_key_env` |
| `ollama` | 本地 Ollama | `base_url`（默认 localhost:11434） |
| `custom_http` | 自研或专业模型 REST API | `base_url` + Jinja 请求模板 + JSONPath 响应提取 |

### 内置提供商（数据库种子数据）

- Claude API（Anthropic）
- OpenAI（openai_compat）
- DeepSeek（openai_compat）
- Ollama 本地（ollama）

### 引擎配置参数

每个引擎 × 任务类型（primary / fallback_1 / fallback_2 / batch）可独立配置：
- `model_id`：关联 `llm_models` 表
- `temperature`（0-2）
- `max_tokens`（整数）
- `top_p`（0-1）
- `frequency_penalty`（0-2）
- `prompt_template_version`：关联 `prompt_templates` 表
- `extra_params`：JSONB，自定义 HTTP 请求模板等

### 断路器参数（Redis 分布式）

- `failure_threshold` = 5（连续失败次数，触发 OPEN）
- `success_threshold` = 2（HALF_OPEN 状态成功次数，恢复 CLOSED）
- `recovery_sec` = 60（OPEN → HALF_OPEN 等待秒数）

---

## 引擎业务参数

### 知识图谱引擎参数（scope: `kg`）

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `classify_batch_size` | number | 20 | Haiku 批量分类批次大小 |
| `extract_confidence_min` | slider | 0.7 | 深度提取最低置信度 |
| `mandatory_obligation_words` | tags | MUST,必须,应当 | 强制义务词汇 |
| `graph_query_depth_max` | number | 5 | Cypher 最大查询深度 |
| `kg_high_confidence` | slider | 0.85 | 高置信度阈值 |
| `kg_low_confidence` | slider | 0.60 | 低置信度阈值 |
| `embedding_model` | select | bge-m3 | 向量化模型 |
| `reranker_model` | select | bge-reranker-large | 重排序模型 |
| `rag_top_k` | number | 10 | RAG 检索 Top-K 数量 |
| ...（共 13 个参数） | | | |

### 经济测算引擎参数（scope: `economic`）

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `standard_bar_lengths` | tags | 9000,10000,12000 | 可选钢筋原料长度（mm）|
| `seismic_factor_grade1` | slider | 1.15 | 一级抗震锚固修正系数 ζaE |
| `seismic_factor_grade2` | slider | 1.15 | 二级 ζaE |
| `seismic_factor_grade3` | slider | 1.05 | 三级 ζaE |
| `seismic_factor_grade4` | slider | 1.00 | 四级（非抗震）ζaE |
| `lap_factor_25pct` | slider | 1.20 | 搭接百分率 ≤25% 系数 |
| `lap_factor_50pct` | slider | 1.40 | 搭接百分率 50% 系数 |
| `lap_factor_100pct` | slider | 1.60 | 搭接百分率 100% 系数 |
| `field_waste_d8` | slider | 0.030 | φ8 现场损耗率 |
| `field_waste_d12` | slider | 0.025 | φ12 现场损耗率 |
| `field_waste_d16` | slider | 0.020 | φ16 现场损耗率 |
| `field_waste_d20` | slider | 0.018 | φ20 现场损耗率 |
| `field_waste_large` | slider | 0.015 | φ25+ 现场损耗率 |
| `target_waste_rate` | slider | 0.015 | 下料优化目标废料率 |
| `auto_proposal_min_saving` | number | 5000 | 自动建议最低节约额（元）|
| `price_db_update_reminder_days` | number | 30 | 价格库更新提醒间隔（天）|
| ...（共 18 个参数） | | | |

---

## 管理后台（Admin）

`/admin/model-management` — 五标签页模型管理界面：
1. **健康看板**: 提供商连通性（ONLINE/OFFLINE）+ 断路器状态 + 7 日成本汇总表
2. **提供商管理**: CRUD + 一键健康检查 + 全量健康检查
3. **模型管理**: 按提供商筛选 + CRUD（模型 ID/上下文窗口/价格/是否支持视觉）
4. **引擎配置**: 按引擎筛选，ProTable 行内编辑（温度滑块/max_tokens/top_p），启用/禁用切换
5. **调用日志**: 7日/30日费用趋势图 + 错误日志 + 断路器异常列表

`/admin/engine-params` — 引擎业务参数配置：
- 左侧 Tab：知识图谱引擎 / 经济测算引擎
- 右侧：Schema 驱动动态表单（数字/滑块/选择/多选/标签输入）
- 每个参数单独保存，蓝点标记未保存修改
- 支持一键重置为默认值，显示最后修改时间

---

## 开发约定

### 代码规范

- 遵循 `~/.claude/rules/common/` 全局规范
- 前端遵循 `~/.claude/rules/ecc/web/` 规范
- 后端：Repository Pattern 封装数据库操作；依赖注入管理服务
- 所有状态变更写入 `audit_logs`（只追加，不可修改）

### 命名

- 数据库表名：`snake_case`，复数形式
- API 路径：`/api/v1/{resource}/{id}`
- 前端组件：`PascalCase`
- Python 模块：`snake_case`

### 测试要求

- 最低覆盖率 **80%**
- 强制 TDD：先写测试，再实现
- 三审状态机：必须 100% 覆盖所有状态边界（包括非法跳转）
- AI 服务：提供离线 mock 用于 CI 测试

### 安全

- 所有 API 需 JWT 认证（Access 24h + Refresh 30d）
- 图纸传输 TLS 1.2+，存储 AES-256，下载签名 URL（5 分钟有效）
- 权限粒度：集团 → 分公司 → 项目部 → 个人角色（RBAC）

---

## 关键业务规则（系统硬约束，不可绕过）

1. **二审强制签字**: `economic_reviews.economist_signed_at IS NULL` → API 返回 403，前端入口禁用
2. **无限额领料单不发布**: `material_quota_sheet` 为 NULL → 图纸无法进入 `published` 状态
3. **多方案最低限制**: 二审录入方案数 < 2 → 拒绝提交
4. **三方签字顺序**: 项目经理未签 → 经济师签字请求 403；经济师未签 → 集团总监签字请求 403
5. **KPI 红线**: 年产值超 1 亿项目，年度创效额 < 50 万 → 看板红色预警，年度评优一票否决
6. **铁三角比例**: 集团 20% / 项目 50% / 提案人 30%，总比例硬编码为 100%，不允许前端修改

---

## 环境变量（参考 .env.example）

```
# 数据库
DATABASE_URL=postgresql://user:pass@localhost:5432/cad_db
REDIS_URL=redis://localhost:6379/0

# 文件存储
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=...
MINIO_SECRET_KEY=...

# 向量数据库
CHROMA_HOST=localhost
CHROMA_PORT=8000

# AI 服务
AI_SERVICE_URL=http://localhost:8001

# LLM API Keys（模型路由层从 DB 读取 api_key_env 后，从 OS 环境变量取值）
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
DEEPSEEK_API_KEY=...
# Ollama 无需 API Key，只需 base_url

# JWT
JWT_SECRET=...
JWT_EXPIRE_MINUTES=1440

# 通知（可选）
WECHAT_WEBHOOK_URL=...
```

---

## 快速启动

```bash
# 安装依赖
pnpm install

# 启动开发环境（PostgreSQL + AGE 扩展 + Redis + MinIO + Chroma）
docker compose up -d

# 运行数据库迁移
cd apps/api && alembic upgrade head
# 或直接执行 SQL 迁移脚本
psql $DATABASE_URL -f migrations/001_initial_schema.sql
psql $DATABASE_URL -f migrations/002_model_management.sql

# 启动前端
cd apps/web && pnpm dev

# 启动后端
cd apps/api && uvicorn main:app --reload

# 启动 AI 服务
cd apps/ai-review && python main.py

# 启动 Celery Worker
cd apps/api && celery -A core.celery worker --loglevel=info
```

---

## 参考文档

- 原始需求文档：`docs/source/全面推行图纸深化全过程管理体系_正式图表版.docx`
- 完整开发计划：`docs/PLAN.md`
- 系统架构：`docs/ARCHITECTURE.md`
- 产品需求：`docs/PRD.md`
