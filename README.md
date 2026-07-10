# 图纸深化全过程管理平台

> 基于《全面推行图纸深化全过程管理体系》构建的数字化管理平台，将图纸深化从"按图施工"升级为"以图创效"，目标挖掘年产值 **2%–3% 的隐性利润**。

---

## 当前版本：v0.6.1-dev（feat/batch-review-and-model-base）

当前最新开发线为 `feat/batch-review-and-model-base`。截至 2026-07-10，本轮继续推进**批量读图 / 整套审图（Phase 5）**、**工程 3D 模型基座（Phase 6）** 和 **3D 模型高精度重建（Phase 7）**，并把上海大歌剧院图纸验证中暴露的“固定三单体假设”升级为通用语义图谱、可审查候选、人工校正和 LOD 证据门控。

### v0.6.1-dev 更新摘要（2026-07-10）

| 类别 | 更新内容 |
|------|----------|
| 通用图纸语义识别 | 新增 `drawing_semantics`，按项目无关规则抽取 `building_unit`、`sub_zone`、`functional_space`、`construction_zone` 四类候选；A/B/C/D1/D2 区、剧厅、施工区、连通道等只作为证据候选，不再硬编码为固定单体 |
| 语义图谱数据结构 | 新增 migration `016_model_semantic_graph.sql`，包含 `model_semantic_nodes`、`model_semantic_evidence`、`model_semantic_assignments`、`model_semantic_operations`；历史 `model_building_units` 仅迁移为 `legacy_inference` 候选，不自动确认为真实单体 |
| 语义校正 API | 新增 `GET /projects/{project_id}/model/semantics`、`POST /projects/{project_id}/model/semantic-operations`、`GET /projects/{project_id}/model/rebuild-impact`，支持版本冲突 `409 SEMANTIC_VERSION_CONFLICT` 和层级错误 `422 INVALID_SEMANTIC_HIERARCHY` |
| 模型 scene V3 兼容字段 | `build_scene` 保留 V2 `buildings/floors/markers/stats`，新增 `semantic_tree`、`unassigned_drawings`、`semantic_version`，前端可展示和修正候选语义，不破坏旧模型读取 |
| LOD 证据门控 | 新增 `ModelScopeEvidence` / `LodCapability`；LOD200 作为 PDF 基线，LOD300 必须满足比例、轴网配准、尺寸、跨视图匹配、稳定构件边界、几何一致性等显式证据；效果图只作为视觉校准，不满足几何门槛 |
| 前端语义校正工作流 | 工程模型页新增语义树、候选审查队列、证据弹窗、确认/调整父级等操作；接口已对齐后端 `semantic-operations` 和 `rebuild-impact` |
| Docker 验证 | 本地 Docker `api/web` 已按最新代码重建，应用 migration 016；`http://127.0.0.1:8002/health` 返回 `{"status":"ok"}`，模型页 Playwright E2E 通过 |
| 测试验证 | 后端语义/模型相关聚合回归 `102 passed`；前端 `npm run build` 通过；`E2E_BASE_URL=http://127.0.0.1:3002 npx playwright test tests/e2e/model.spec.ts --project=chromium` 通过 |

### v0.6.0-dev 更新摘要

| 类别 | 更新内容 |
|------|----------|
| 动态单体识别 | 不再把“上海大歌剧院 / 南区 / 北区”视为固定事实；系统从图纸标题、图号、文件路径、OCR/人工标注中生成动态 `building_unit` 候选，支持 A/B/C/D1/D2 区、剧厅、连通道等图纸证据进一步校准 |
| 楼层归一化 | 按单体独立生成 story table，检测相邻楼层间距小于 `2.8m` 的异常并按默认层高校正，避免上海大歌剧院和南区楼层近乎重叠 |
| 未分层图纸 | 未识别楼层的图纸进入 `annotation_queue`，模型响应和独立 API 均可返回待人工识别队列、质量问题和候选单体 |
| 人工识别标注 | 新增 `drawing_model_annotations` 等迁移表和保存接口，支持人工指定单体、楼层、图纸类型、候选来源和置信度，后续重建优先使用人工标注 |
| 3D 模型质量面板 | 工程模型页面新增模型质量、待人工识别、低置信度单体和楼层冲突展示，单体下拉改为后端数据驱动，允许新增/重命名单体 |
| LOD 建模升级 | 新增动态 LOD100/LOD200 体量服务和效果图参考校准服务；效果图仅用于视觉校准，真实尺寸仍以图纸/CAD/IFC/人工标注为准 |
| 参考图校准 | 支持 `/Users/lionel/work/上海大歌剧院图纸/效果图` 下 jpg/png 参考图记录，保留相机预设和特征点结构，不直接作为尺寸来源 |
| 前端验证 | `npm run build` 通过，模型页面新增“审图骨架 / 建筑体量 / 实景近似”LOD 入口；实景近似可使用现有场景进入代理预览，并明确标记为“近似”，完整 LOD300 仍属于后续高精度建模阶段 |
| 后端测试 | `409 passed`，总覆盖率 `80.90%`，达到 `cov-fail-under=80` |

### 上海大歌剧院图纸识别校准结论

对 `/Users/lionel/work/上海大歌剧院图纸` 的只读分析显示，原始图纸不能证明项目只有三个单体。真实图纸同时出现以下命名维度：

- `南区（大、中歌剧厅）`、`北区（小歌剧厅）`
- `A、B、C区`、`D1区`、`D2区`
- `大歌剧厅`、`中歌剧厅`、`小歌剧厅`、`台塔`、`台仓`、`观众厅`
- `世博文化园联通道`、`世界花艺园连通道`、车道联通道、围护施工分区

因此工程模型采用“动态候选单体 + 分区/功能空间线索 + 人工校正”的识别策略，不把当前界面显示的三个名称硬编码为唯一事实。

### v0.5.0-dev 更新摘要

### v0.5.0-dev 更新摘要

| 类别 | 更新内容 |
|------|----------|
| 批量读图 / 整套审图 | 新增审图批次、ZIP 整套导入、批量 AI 审图、跨图纸审查、批次完成汇总和前端批次详情页 |
| 会审审查 V4 | 在第 5 引擎基础上升级为六步控制链、五维审查、闭环不足判定和结构化处理建议 |
| 工程 3D 模型基座 | 新增项目模型接口、模型构建任务、实时进度、前端 Three.js 模型查看器和项目模型页面 |
| 3D 高精度重建 | 支持真实坐标重建、轴号配准统一源坐标点、真实标高、建筑外壳、单体构件切分和模型资产管理 |
| 图纸解析增强 | 支持真实竣工图文件名解析、ZIP 中文文件名乱码修复、楼层解析数值钳制、伪楼层过滤、标高符号约束和轴网离群裁剪 |
| DWG / 图元识别 | 增加 LibreDWG 自动转换接入点、ODA 转换提示、YOLO 图纸权重管线和 `drawing_elements.pt` 部署产物 |
| 稳定性 | 增加渲染卡死保护、DWG 支持失败降级、IFC/外部转换 graceful degradation 和大批量图纸数据质量修复 |
| 前端 E2E | 调整角色 smoke matrix 和图纸详情断言，兼容桌面/移动端差异；覆盖 chromium/firefox/webkit/mobile-chrome |
| 后端测试 | 新增批量审图、跨图纸、文件名解析、楼层解析、模型构建、项目模型路由、DWG 支持等测试 |

### 当前本地 Docker 验证结果（2026-07-10）

| 项目 | 结果 |
|------|------|
| Docker 服务 | 使用 `docker compose -p cad -f infra/docker-compose.yml -f infra/docker-compose.alt-ports.yml --profile app up -d --build api web` 重建；`cad_web` / `cad_api` / `cad_postgres` / `cad_redis` / `cad_minio` / `cad_chroma` 运行正常 |
| 数据迁移 | 已在本地 Docker PostgreSQL 执行 `apps/api/migrations/016_model_semantic_graph.sql` |
| 前端构建 | `npm run build` 通过 |
| 后端测试 | `102 passed`（语义图谱、语义 API、文件名解析、模型构建、LOD gate 相关聚合回归）；完整覆盖率套件仍沿用 v0.6.0-dev 的 80% 策略 |
| 模型页 E2E | `E2E_SKIP_SEED=1 E2E_BASE_URL=http://127.0.0.1:3002 npx playwright test tests/e2e/model.spec.ts --project=chromium` 通过，覆盖语义树、候选审查、LOD 质量、证据弹窗和语义操作 |
| 健康检查 | `http://127.0.0.1:8002/health` 返回 `{"status":"ok"}` |
| 登录验证 | `admin / admin123` 登录接口验证通过 |

本地 Docker 访问地址：

| 服务 | 地址 |
|------|------|
| 前端登录页 | <http://127.0.0.1:3002/login> |
| 后端健康检查 | <http://127.0.0.1:8002/health> |
| MinIO 控制台 | <http://127.0.0.1:9003> |

默认登录账号：

```text
账号：admin
密码：admin123
角色：group_admin / 系统管理员
```

---

## v0.4.0

`v0.4.0` 在 `v0.3.0` 之上接入**会审审查引擎（第 5 引擎）**——将 1909 条真实图纸会审/设计交底记录、覆盖 19 个专业的认知蒸馏协议工程化落地，把 AI 审图从"规范条文合规"扩展到"会审经验驱动的问题发现 + 闭环追问"。

### v0.4.0 更新摘要

| 类别 | 更新内容 |
|------|----------|
| 会审审查第 5 引擎 | 新增 `ReviewAuditEngine`，接入四引擎 Orchestrator（复用 vision OCR 文本，与规则/KG/RAG 并行）；专业体系全量扩展到 **19 个专业**（ZH/JG/WH/JZ/ZJ/RF/GJG/JDQ/GPS/ZS/DQ/NT/MQ/SWT/JGUAN/JN/JK/RD/XF），保留到 5 粗专业映射 |
| 独立图纸会审模块 | 新增 `/api/v1/drawing-review` 文本审查模块：对会审记录/设计交底/问题单纯文本做结构化审查，产出闭环问题单（audit / audit-batch / records / document）|
| V2 四维输出 | **对象识别**（部位级/系统级/节点级 + 推定依据）、**场景识别**（图间冲突>施工落地>验收风险>正常审图）、**问题包**（主问题/补充问题/证据缺口）、**文书化输出**（会审纪要口径 + 设计答复口径）|
| 知识资产 | `data/review_protocol/`：disciplines / question_templates / concern_keywords / location_patterns / scenario_templates / question_pack_templates / document_templates（逐专业誊录自认知蒸馏，非杜撰）|
| 前端 | 新增「图纸会审」独立录入页（对象/场景/问题包/文书 Tab）；图纸详情 AIReviewPanel 新增「会审审查」分区（按专业分组 + 风险/场景标签 + 问题单导出）|
| 报告 | AI 审图 Excel 报告新增「会审问题单」工作表（专业/风险/场景/对象/主问题/补充问题/接口/证据缺口）|
| 数据迁移 | 新增 `007_review_audit.sql`（issues +10 列、新表 review_audit_records/findings）与 `008_review_audit_v2.sql`（issues/findings +7 V2 列）|
| 测试 | `tests/test_review_audit_*`：87 passed，review_audit 包覆盖率 89.4% |

> 设计原则：模板填空为默认，无 pyyaml / 无 LLM / 无文本均优雅降级，绝不阻断既有四引擎；V1 字段全部向后兼容。会审引擎做初步归类/证据组织/接口前置/闭环表达，最终结论仍以图纸 + 设计答复 + 专业负责人确认为准。

### v0.3.0 更新摘要

| 类别 | 更新内容 |
|------|----------|
| 项目管理 | 新增项目档案、项目成员、工作分区管理；项目列表展示组织、负责人、成员数、图纸数和状态 |
| 人员管理 | 新增人员 CRUD、启用/停用、重置密码、组织架构管理 |
| 项目级权限 | 新增 `project_members`，非管理员按项目成员关系查看项目；项目经理/项目总工可维护项目成员和分区 |
| 业务改造 | 图纸上传、创效提案提交、项目看板改为项目下拉选择，不再要求手填项目 UUID |
| 数据迁移 | 新增 `006_project_user_management.sql`，扩展 `projects/users` 字段并补齐既有项目成员关系 |

### v0.2.0 更新摘要

`v0.2.0` 完成了本地 Docker 生产式部署、模型路由管理、规范知识库 PDF 自动导入、测试覆盖率提升、前端依赖安全治理和 E2E 种子数据等一组基础能力增强。

| 类别 | 更新内容 |
|------|----------|
| Docker 部署 | API/Web 增加生产式 Dockerfile，依赖安装移入镜像构建阶段；Compose app profile 使用生产式启动；Web nginx 支持 Docker DNS 动态解析，避免 API 重建后代理 502 |
| CI / 镜像安全 | GitHub Actions 增加 Docker Buildx 缓存和 Trivy 镜像扫描，减少重复构建耗时并补齐镜像安全检查 |
| 后端测试覆盖率 | 覆盖 router/task 集成测试与核心服务测试，后端覆盖率达到 80% 以上 |
| 前端依赖安全 | 处理 npm audit 的 critical/high 漏洞链路；剩余 moderate/low 归入 Umi 框架升级批次评估 |
| E2E 测试 | 增加种子数据、图纸详情稳定用例和 admin/pm/designer/economist 角色 smoke matrix |
| 模型路由管理 | 修复提供商健康检查、调用日志 500、Ollama 本地模型发现；模型管理可从已配置供应商中选择模型，本地 Ollama 可显示已安装模型 |
| 引擎配置 | 引擎名称下拉 hover 显示中文说明；选择引擎后自动带入推荐模型和推荐参数；推荐模型不可用时回退到本地可用 Ollama 模型；重复配置返回中文提示而非 500 |
| 规范知识库 | 支持 PDF 一键上传自动建档，自动识别规范名称、编号、版本、专业、发布机构、实施日期，并触发条文导入 |
| 规范导入稳定性 | 增加无 LLM 时的本地条文分类/提取兜底；修复导入状态、审计 JSON、日期字段、Celery 队列路由和 DB adapter 问题 |

### 版本回滚

每个发布版本使用语义化 Git tag 标记。回滚到本版本：

```bash
git fetch --tags
git checkout v0.3.0
cd infra
docker compose --profile app up -d --build
```

---

## 核心能力

| 能力 | 说明 | 状态 |
|------|------|------|
| 三审三算工作流 | 技术规范化 → 经济最优化 → 结算合规化，经济师未签字系统层面硬拦截 | ✅ |
| AI 智能审图（五引擎）| 规则引擎 + 知识图谱 + LangGraph 三步推理 + YOLOv8 图元检测 + 会审审查引擎 | ✅ |
| 会审审查引擎（19 专业）| 1909 条会审经验蒸馏：专业路由 + 对象/场景识别 + 问题包 + 会审纪要/设计答复文书化输出 | ✅ |
| 经济测算引擎 | GB50010-2010 钢筋翻样 + FFD+2-opt 下料优化（废料率 ≤ 1.5%）| ✅ |
| 规范知识库 | 手动录入 / PDF 自动导入 / 外部 API，同步回填字段，AGE 图谱，Chroma 语义搜索 | ✅ |
| 创效激励闭环 | 在线提案 → 商务测算 → 三方签字 → 铁三角分配 → 凭证 PDF | ✅ |
| 项目与人员管理 | 组织架构、人员账号、项目档案、项目成员、工作分区 | ✅ |
| 数据看板 | 集团级（KPI 预警/成本看板）+ 项目级（流转状态/活动 Timeline）| ✅ |
| 模型路由管理 | 热切换 Claude/OpenAI/DeepSeek/Ollama，Ollama 本地模型发现，断路器保护，调用日志 | ✅ |
| PWA / 移动端 | Service Worker（Cache First 静态/Network First 页面）+ manifest | ✅ |
| K8s 生产部署 | Kustomize base + production overlay + Prometheus + Grafana | ✅ |
| CI/CD | GitHub Actions：pytest + bandit + tsc + build + Playwright E2E + Docker 缓存 + Trivy 扫描 | ✅ |

---

## 技术栈

### 前端
- **框架**: React 18 + TypeScript + [UmiJS Max](https://umijs.org/docs/max/introduce)
- **UI**: [Ant Design 5](https://ant.design) + [Pro Components](https://procomponents.ant.design)
- **图纸预览**: [@react-pdf-viewer/core](https://react-pdf-viewer.dev)（内嵌 PDF 查看，替代 window.open）
- **PWA**: Service Worker + Web App Manifest + 离线降级策略
- **E2E 测试**: [Playwright](https://playwright.dev)（chromium/firefox/webkit/mobile-chrome）

### 后端
- **框架**: [FastAPI](https://fastapi.tiangolo.com) (Python 3.12+) + databases + asyncpg
- **工作流**: [transitions](https://github.com/pytransitions/transitions) 状态机（三审流程）
- **异步任务**: [Celery](https://docs.celeryq.dev) + Redis（AI 审图/规范导入/公示期推进/API 定时同步）
- **文件存储**: [MinIO](https://min.io)（图纸/报告/图集，AES-256，预签名 URL 5min TTL）
- **测试**: pytest + pytest-asyncio + pytest-cov（cov-fail-under=80）

### AI 审图（五引擎）
- **引擎 1 — 规则引擎**: YAML DSL（5 个专业：common/structure/architecture/mep/decoration）
- **引擎 2 — 知识图谱**: [Apache AGE](https://github.com/apache/age)（PostgreSQL 扩展，Cypher 查询 + SQL 降级）
- **引擎 3 — RAG + LangGraph**: [Chroma](https://github.com/chroma-core/chroma) 向量检索 + [LangGraph](https://github.com/langchain-ai/langgraph) 三步推理（graceful degradation）
- **引擎 4 — 视觉/OCR**: [ezdxf](https://github.com/mozman/ezdxf) + [PyMuPDF](https://github.com/pymupdf/PyMuPDF) + PaddleOCR + [YOLOv8](https://github.com/ultralytics/ultralytics)（graceful degradation）
- **引擎 5 — 会审审查**: 1909 条真实会审记录蒸馏的 19 专业审查协议（`core/ai_review/review_audit/`，模板填空 + 可选 LLM 润色），复用 vision 抽取文本，产出对象/场景/问题包/文书化输出

### 数据与基础设施
- **主库**: PostgreSQL 16（含 Apache AGE 扩展，8 个迁移脚本）
- **缓存/队列**: Redis 7（Celery + 断路器分布式状态）
- **向量库**: Chroma（规范语义检索）
- **容器**: Docker Compose（开发）→ Kubernetes + Kustomize（生产）
- **监控**: Prometheus + Grafana（在 K8s cad 命名空间内部署）

---

## 项目结构

```
CAD/
├── CLAUDE.md                   # 开发指南（技术决策/规范/快速启动）
├── .env.example                # 环境变量模板
├── apps/
│   ├── web/                    # 前端（UmiJS Max + React 18）
│   │   ├── config/
│   │   │   └── routes.ts       # 路由配置
│   │   ├── public/
│   │   │   ├── manifest.json   # PWA Manifest
│   │   │   └── sw.js           # Service Worker
│   │   ├── tests/e2e/          # Playwright E2E 测试（login/drawings/incentive）
│   │   └── src/
│   │       ├── access.ts       # RBAC 权限（6 个维度）
│   │       ├── app.tsx         # 全局状态 + 请求拦截 + SW 注册
│   │       ├── services/       # API 调用封装（drawings/regulations/dashboard）
│   │       └── pages/
│   │           ├── Login/           # 登录页
│   │           ├── drawings/        # 图纸管理
│   │           │   ├── DrawingList/ # ProTable 列表
│   │           │   └── DrawingDetail/
│   │           │       ├── PdfViewer.tsx         # @react-pdf-viewer 内嵌预览
│   │           │       ├── AIReviewPanel.tsx      # AI 审图报告（Tab/下载）
│   │           │       ├── TechnicalReviewPanel.tsx
│   │           │       ├── EconomicReviewPanel.tsx
│   │           │       ├── SettlementReviewPanel.tsx
│   │           │       └── EconomicCalcPanel.tsx  # 钢筋翻样 + 下料优化
│   │           ├── incentive/       # 创效激励（提案/测算/签字/分配）
│   │           ├── dashboard/       # 数据看板（集团级 + 项目级）
│   │           └── admin/           # 管理后台（模型/引擎/规范库）
│   └── api/                    # 后端（FastAPI + Celery）
│       ├── main.py             # 15 个路由注册
│       ├── core/
│       │   ├── auth.py         # JWT（Access 24h + Refresh 30d）
│       │   ├── storage.py      # MinIO 封装
│       │   ├── celery_app.py   # Celery 配置 + Beat 调度（含规范 API 同步）
│       │   ├── economic/       # 经济测算引擎（rebar_calculator.py）
│       │   ├── llm/            # 模型路由层（router/circuit_breaker/providers）
│       │   ├── ai_review/      # 四引擎
│       │   │   ├── base.py           # DrawingContext / AIIssue / BaseEngine
│       │   │   ├── rules_engine.py   # YAML DSL
│       │   │   ├── kg_engine.py      # Apache AGE Cypher + SQL 降级
│       │   │   ├── rag_engine.py     # Chroma + LangGraph 三步推理
│       │   │   ├── vision_engine.py  # ezdxf + fitz + PaddleOCR + YOLO
│       │   │   ├── yolo_detector.py  # YOLOv8 图元检测（graceful degradation）
│       │   │   ├── langgraph_agent.py # LangGraph 三步推理代理
│       │   │   ├── review_audit/      # 会审审查第5引擎（19专业蒸馏协议）
│       │   │   │   ├── engine.py             # audit_text + ReviewAuditEngine
│       │   │   │   ├── discipline_router.py  # 19 专业识别/路由
│       │   │   │   ├── object_identifier.py  # 对象识别（部位/系统/节点级）
│       │   │   │   ├── scenario_router.py    # 场景识别（图间冲突/施工落地/验收风险/正常审图）
│       │   │   │   ├── question_pack_builder.py # 问题包（主/补充/证据缺口）
│       │   │   │   └── document_writer.py    # 会审纪要口径 + 设计答复口径
│       │   │   └── orchestrator.py   # Vision串行 → [Rules/KG/RAG/Review]并行
│       │   └── workflow/       # 三审状态机（transitions）
│       ├── routers/            # API 路由（15 个模块）
│       ├── services/           # 业务逻辑（报告/规范导入/奖金/凭证等）
│       ├── tasks/              # Celery 任务（ai_review/proposal_notice/regulation_import/regulation_api_sync）
│       ├── tests/              # pytest 测试套件（状态机/公式/API/规范同步）
│       ├── data/rules/         # YAML 规则文件（5 个专业）
│       ├── data/review_protocol/ # 会审审查知识资产（19 专业：路由/模板/场景/问题包/文书）
│       ├── docs/skills/        # drawing-review-auditor 技能资产（SKILL/prompt-template）
│       └── migrations/         # SQL 迁移脚本（001~008）
├── infra/
│   ├── docker-compose.yml      # 开发环境（PG+AGE/Redis/MinIO/Chroma/minio-init）
│   └── k8s/
│       ├── base/               # K8s 基础层（namespace/configmap/所有 Deployment/Service/Ingress）
│       │   ├── postgres/       # StatefulSet + Headless Service（Apache AGE）
│       │   ├── redis/          # StatefulSet + Headless Service
│       │   ├── minio/          # StatefulSet + initContainer（建 3 桶）
│       │   ├── chroma/         # StatefulSet + Service
│       │   ├── api/            # Deployment + Service + HPA（min=2/max=8）
│       │   ├── web/            # Deployment + Service
│       │   ├── celery/         # Worker（replicas=2）+ Beat（replicas=1）
│       │   ├── monitoring/     # Prometheus（RBAC）+ Grafana（Datasource ConfigMap）
│       │   └── ingress.yaml    # Nginx Ingress + cert-manager TLS + 200m body size
│       └── overlays/production/ # 3 副本 + 生产镜像标签 ${IMAGE_TAG}
└── docs/
    ├── PRD.md                  # 产品需求文档（V3.1，含实现状态）
    ├── PLAN.md                 # 开发计划（V4.0，含完成标记）
    └── ARCHITECTURE.md         # 系统架构设计（V2.2）
```

---

## 快速启动

### 前置条件

- Docker Desktop（含 Compose v2）
- Python 3.12+
- Node.js 20+ + npm 10+

### 1. 克隆并配置环境变量

```bash
git clone https://github.com/lsgoodlionel/AI-CAD.git
cd AI-CAD
cp .env.example .env
# 编辑 .env，填写 JWT_SECRET 和 LLM API Keys
```

### 2. 启动本地 Docker 部署

```bash
cd infra
docker compose --profile app up -d --build
# PostgreSQL+AGE、Redis、MinIO、Chroma、FastAPI、Celery Worker、Celery Beat、Web nginx
# Web: http://127.0.0.1:3000
# API: http://127.0.0.1:8000
```

### 3. 初始化数据库（首次部署或新库）

```bash
cd apps/api
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 执行全部迁移脚本
psql $DATABASE_URL -f migrations/001_initial_schema.sql
psql $DATABASE_URL -f migrations/002_model_management.sql
psql $DATABASE_URL -f migrations/003_economic_calc.sql
psql $DATABASE_URL -f migrations/004_regulation_api_sync.sql
psql $DATABASE_URL -f migrations/005_regulation_import_status.sql
psql $DATABASE_URL -f migrations/006_project_user_management.sql
psql $DATABASE_URL -f migrations/007_review_audit.sql        # 会审审查引擎 V1
psql $DATABASE_URL -f migrations/008_review_audit_v2.sql     # 会审审查引擎 V2（须在 007 之后）
```

### 4. 启动后端

```bash
# apps/api 目录（已激活 venv）
uvicorn main:app --reload --port 8000

# 另开终端：Celery Worker
celery -A core.celery_app worker --loglevel=info -Q default,ai_review,regulation_import

# 另开终端：Celery Beat（定时任务）
celery -A core.celery_app beat --loglevel=info
```

### 5. 启动前端

```bash
cd apps/web
npm install
npm run dev
# 访问 http://localhost:3000
```

### 默认账号

数据库种子数据包含一个管理员账号（见 `migrations/001_initial_schema.sql`）：

| 字段 | 值 |
|------|----|
| 账号 | `admin` |
| 密码 | `admin123` |
| 角色 | `group_admin` |

E2E 种子脚本还会创建：

| 账号 | 角色 |
|------|------|
| `pm` | `project_manager` |
| `economist` | `economist` |
| `designer` | `designer` |

### 运行测试

```bash
# 后端单元测试与覆盖率
cd apps/api
pytest tests/ -v
pytest tests/ --cov=. --cov-report=term-missing

# 前端 E2E 测试（需先启动 dev server）
cd apps/web
npx playwright test
```

### 规范 PDF 自动导入

管理员进入 `系统管理 → 规范知识库 → 规范文件`，点击 **上传 PDF 自动导入**。系统会：

1. 从 PDF 文本识别规范名称、编号、版本、专业、发布机构、实施日期。
2. 自动创建规范文件，状态置为 `processing`。
3. 将 PDF 上传到 MinIO。
4. 触发 Celery 规范导入任务。
5. 优先使用模型路由做条文分类和提取；模型不可用时使用本地规则兜底。
6. 导入完成后状态置为 `active`，可在条文列表中查看结构化条文。

### 模型路由与本地 Ollama

管理员进入 `系统管理 → 模型路由管理`：

- **提供商管理**：配置 Claude/OpenAI/DeepSeek/Ollama 等供应商，支持健康检查。
- **模型列表**：选择已配置供应商新增模型；Ollama 本地供应商会读取本机 `http://host.docker.internal:11434/api/tags`，显示本地已安装模型。
- **引擎配置**：为不同业务引擎配置主模型、备用模型和批量模型。引擎名称下拉会显示中文说明，并自动带入推荐模型和参数。
- **调用日志**：查看调用成本、错误、断路器状态和按日统计。

---

## API 文档

后端启动后访问：
- Swagger UI：`http://localhost:8000/docs`
- ReDoc：`http://localhost:8000/redoc`

### 主要 API 模块

| 前缀 | 说明 |
|------|------|
| `/api/v1/auth` | 登录/刷新 Token |
| `/api/v1/drawings` | 图纸上传/审图/AI 报告（PDF/Excel）|
| `/api/v1/drawing-review` | 会审审查（文本审查 audit/audit-batch/records/document）|
| `/api/v1/technical-review` | 一审（技术规范化）|
| `/api/v1/economic-review` | 二审（经济最优化，含签字强制）|
| `/api/v1/settlement-review` | 三审（结算合规化）|
| `/api/v1/economic-calc` | 钢筋翻样 + 下料优化 |
| `/api/v1/incentive` | 创效提案全生命周期 |
| `/api/v1/regulations` | 规范知识库（书/条文/API源/搜索）|
| `/api/v1/dashboard` | 集团级 + 项目级看板 |
| `/api/v1/projects` | 项目档案、项目成员、工作分区管理 |
| `/api/v1/admin/users` | 人员管理、启停账号、重置密码 |
| `/api/v1/admin/organizations` | 组织架构管理 |
| `/api/v1/admin/llm/providers` | LLM 提供商管理 |
| `/api/v1/admin/llm/models` | LLM 模型管理 |
| `/api/v1/admin/llm/engine-configs` | 引擎配置（模型/温度/tokens）|
| `/api/v1/admin/engine-params` | 引擎业务参数（KG + 经济测算）|
| `/api/v1/admin/llm/logs` | 调用日志与成本统计 |

---

## 实现状态

| 模块 | 状态 | 说明 |
|------|------|------|
| JWT 认证 + RBAC | ✅ | Access 24h / Refresh 30d / 6 个权限维度 |
| 三审工作流状态机 | ✅ | transitions 库，二审强制签字 HTTP 403，100% 状态边界测试 |
| MinIO 文件存储 | ✅ | 预签名 URL，5min TTL，AES-256 |
| Celery 异步任务 | ✅ | AI 审图 / 规范导入 / 公示期推进 / 外部 API 同步（每小时 beat）|
| 五引擎 AI 审图框架 | ✅ | 规则/KG/RAG/视觉/会审 Orchestrator，各引擎 30s 超时 |
| 会审审查引擎（19 专业）| ✅ | 专业路由 + 对象/场景识别 + 问题包 + 文书化输出；测试 87 passed，覆盖率 89.4% |
| 独立图纸会审模块 | ✅ | `/api/v1/drawing-review` 文本审查 + 闭环问题单导出 + 会审纪要/设计答复生成 |
| YAML 规则引擎 | ✅ | 5 个专业（common/structure/architecture/mep/decoration）|
| LangGraph 三步推理 | ✅ | identify → lookup → synthesize，graceful degradation |
| YOLOv8 图元检测 | ✅ | 标题栏缺失/钢筋密度预警，graceful degradation |
| 模型路由层 | ✅ | 4 种提供商 / 断路器（Redis）/ 回退链 / 调用日志 |
| 管理后台（模型管理）| ✅ | 五标签页 + 引擎参数 Schema 驱动表单 |
| 创效激励系统 | ✅ | 铁三角分配 / 签字顺序约束 / 兑现凭证 PDF |
| AI 审图报告生成 | ✅ | PyMuPDF 批注版 PDF + openpyxl Excel（多页/颜色编码）|
| 经济测算引擎 | ✅ | GB50010-2010 La/LaE/Ll + FFD+2-opt 下料优化（废料率 ≤ 1.5%）|
| 规范知识库管理 | ✅ | NLP 流水线（Haiku+Sonnet）+ 三途径导入 + 管理前端 |
| 外部规范 API 定时同步 | ✅ | Celery beat 每小时，支持 api_key/basic/自定义 response_path |
| 数据看板 | ✅ | 集团级（KPI 预警/LLM 成本）+ 项目级（流转状态/活动 Timeline）|
| PDF 内嵌预览 | ✅ | @react-pdf-viewer，查看/收起切换，presigned URL 懒加载 |
| 测试套件 | ✅ | pytest（状态机/公式/API/规范同步）+ Playwright E2E（4 浏览器）|
| PWA | ✅ | manifest.json + Service Worker（Cache First / Network First）|
| CI/CD | ✅ | GitHub Actions：pytest + bandit + tsc + build + Playwright E2E |
| K8s 生产部署 | ✅ | Kustomize base/production + HPA + Ingress（cert-manager）+ Prometheus + Grafana |

---

## 关键业务规则（硬约束）

1. **二审强制签字**：`economic_reviews.economist_signed_at IS NULL` → API 返回 `403 ECONOMIC_REVIEW_NOT_SIGNED`，前端入口禁用
2. **无限额领料单不发布**：`material_quota_sheet` 为 NULL → 图纸无法进入 `published` 状态（`403 QUOTA_SHEET_MISSING`）
3. **三方签字顺序**：项目经理未签 → 经济师签字请求 403；经济师未签 → 集团总监请求 403
4. **铁三角比例**：集团 20% / 项目 50% / 提案人 30%，代码层编译期断言保证总比例不可修改
5. **状态机守卫**：所有状态变更写入 `audit_logs`，非法跳转由 transitions 库在 DB 约束层双重拦截
6. **KPI 红线**：年产值超 1 亿的项目，年度创效额 < 50 万 → 看板红色预警，年度评优一票否决

---

## 待完善事项

当前平台核心功能已完整实现，以下为后续优化方向：

| 优先级 | 事项 | 说明 |
|--------|------|------|
| 高 | 规范数据录入 | 初始化 ≥ 200 本规范（平台能力就绪，数据需人工导入）|
| 高 | AI 检出率验证 | 用真实图纸验证强条检出率 ≥ 95%，调优引擎参数 |
| 中 | 性能压测 | 100 并发压测，慢查询优化，图纸 CDN 加速 |
| 中 | 专业交叉检测 | 建筑-结构碰撞、结构-机电开孔冲突检测（F-AI-002）|
| 中 | 提示词模板管理 | 版本历史 + 一键切换 + 版本对比（F-ADMIN-006）|
| 中 | 重大变更多人审批 | 集团工程研究院 + 集团商务总监多人审批路径 |
| 低 | 标准图集管理 | 企业级图集版本管理（F-KB-002）|
| 低 | 错漏碰缺案例库 | AI 相似性检索 + 年度评优（F-KB-003）|
| 低 | 安全审计 | 依赖漏洞扫描 / SQL 注入测试 / 日志审查 |
| 低 | 数据备份策略 | 每日全量备份，保留 30 天 |
| 低 | 用户操作手册 | 分角色文档（管理员/总工/经济师/设计师）|

---

## 生产部署（K8s）

```bash
# 配置 secret（不纳入版本控制）
kubectl apply -f infra/k8s/base/secret.yaml   # 参考 secret.example.yaml 填写

# 应用生产配置（3 副本 + 生产镜像）
IMAGE_TAG=v1.0.0
kustomize build infra/k8s/overlays/production | \
  envsubst '${IMAGE_TAG}' | kubectl apply -f -

# 验证
kubectl get pods -n cad
kubectl get ingress -n cad
```

监控面板访问 `https://cad.example.com/grafana/`（初始密码来自 JWT_SECRET）。

---

## 开发指南

### 添加新的 LLM 引擎

1. 在 `core/ai_review/` 下创建继承 `BaseEngine` 的类
2. 实现 `async def analyze(ctx: DrawingContext, db) -> list[AIIssue]`
3. 在 `orchestrator.py` 中注册到并行引擎组
4. 在管理后台"引擎配置"中为新引擎添加模型配置

### 添加规范规则

在 `apps/api/data/rules/` 下编辑对应专业的 YAML 文件：

```yaml
- id: ARC-009
  name: 新规则名称
  discipline: architecture
  std_no: GB50016-2014
  clause: 5.5.1
  obligation: MUST
  severity: critical
  condition:
    type: gte
    field: fire_zone_area
    value: 2500
  message: "防火分区面积超限：{fire_zone_area} m²，规范限值 2500 m²"
```

### 环境变量说明

所有 LLM API Key 通过环境变量注入（见 `.env.example`），模型路由层从数据库读取 `api_key_env` 字段名，再从 OS 环境变量取值，实现秘钥与配置分离。

---

## 贡献指南

### 分支策略

- `main`：稳定主分支
- `feat/xxx`：功能开发分支
- `fix/xxx`：缺陷修复分支

### 提交规范

```
feat: 添加 LangGraph 三步推理代理
fix: 修复二审签字状态未更新问题
refactor: 重构规范导入流水线
```

### 代码规范

- 前端：TypeScript 严格模式，函数 < 50 行，文件 < 800 行
- 后端：`ruff` linting，Repository Pattern 封装数据库操作
- 所有状态变更必须写入 `audit_logs`（只追加，不修改）
- 禁止在代码中硬编码 API Key 或密码

---

## License

MIT
