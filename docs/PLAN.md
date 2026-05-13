# 开发计划 — 图纸深化全过程管理平台

> 版本：V4.0 | 日期：2026-05-12 | 预计工期：6 个月
>
> **图例**: `[x]` 已完成 · `[~]` 部分完成 · `[ ]` 待实现

---

## 实施进度总览

| Phase | 模块 | 状态 | 完成率 |
|-------|------|------|--------|
| Phase 0 | 基础建设 + 模型管理 | 基本完成 | 80% |
| Phase 1 | 三审三算核心流程 | 基本完成 | 85% |
| Phase 2 | AI 智能审图四引擎 | 部分完成 | 95% |
| Phase 3 | 创效激励系统 | 基本完成 | 95% |
| Phase 4A | 规范知识库管理 | 已完成 | 100% |
| Phase 4B | 数据看板 | 已完成 | 100% |
| Phase 4C | UmiJS 路由配置 | 已完成 | 100% |
| Phase 4D | 收尾与上线 | 未开始 | 0% |

---

## 决策确认记录

| 事项 | 决策 |
|------|------|
| 开发模式 | 全部自行开发，整合 GitHub 开源代码为基础 |
| 财务系统 | Phase 1 预留接口，不做硬对接 |
| 规范库输入 | 页面手动录入 + 文件批量导入（PDF/Word/Excel）+ 外部网站 API 接入，自动结构化入库；管理后台支持增删改查 |
| 移动端 | Web 优先（PWA），保留后续原生 App 升级路径 |
| 团队 | 内建团队 |
| AI 审图架构 | 四引擎自建：规则引擎 + 知识图谱推理（AGE）+ RAG+LLM（LangChain）+ 视觉/OCR（PaddleOCR+YOLOv8）|
| 模型管理 | 所有 AI 引擎模型和参数均在管理后台可配置，运行时热切换（30s 生效） |

---

## 需求重述

基于《全面推行图纸深化全过程管理体系》分析报告，自主开发数字化管理平台，核心实现：

1. **三审三算强制流程**: 二审经济师未签字，系统层面阻止图纸流转，财务接口预留冻结逻辑
2. **AI 智能审图（四引擎）**: 规则引擎 + KG 推理 + RAG+LLM + 视觉/OCR，强条检出率 ≥ 95%
3. **模型路由层**: 所有 AI 引擎调用统一经过 ModelRouter，后台可随时切换模型（Claude/OpenAI/DeepSeek/Ollama）
4. **创效激励闭环**: 在线发起提案 → 商务测算 → 三方签字 → 分配兑现
5. **规范知识库**: 多途径导入（手动 / 文件 / API），知识图谱结构化存储，RAG 语义检索

---

## GitHub 开源组件清单

### 图纸解析层

| 库 | Stars | 用途 |
|----|-------|------|
| [ezdxf](https://github.com/mozman/ezdxf) | 1.3k | DWG/DXF 格式解析，读取几何实体、图层、标注 |
| [IfcOpenShell](https://github.com/IfcOpenShell/IfcOpenShell) | 2.5k | IFC/BIM 模型解析与几何引擎，碰撞检测基础 |
| [ifc-pipeline](https://github.com/AECgeeks/ifc-pipeline) | 173 | IFC 处理队列参考架构（Docker + Flask 模式可移植） |
| [speckle-server](https://github.com/specklesystems/speckle-server) | 805 | 3D BIM Web 查看器（可嵌入图纸预览） |

### PDF 处理层

| 库 | Stars | 用途 |
|----|-------|------|
| [PyMuPDF](https://github.com/pymupdf/PyMuPDF) | 9.7k | PDF 解析、批注标注（审查报告批注版生成核心） |
| [pymupdf4llm](https://github.com/pymupdf/pymupdf4llm) | 1.7k | PDF 转 Markdown 用于 RAG 规范入库和 KG NLP 流水线 |
| [react-pdf-viewer](https://github.com/react-pdf-viewer/react-pdf-viewer) | 2.6k | 前端 PDF 图纸预览组件 |
| [pdfme](https://github.com/pdfme/pdfme) | 4.3k | 前端 PDF 生成（审查报告、兑现凭证） |

### AI / 规范问答层

| 库 | Stars | 用途 |
|----|-------|------|
| [langchain](https://github.com/langchain-ai/langchain) | 136k | LLM 编排框架，RAG 规范问答流水线 |
| [langgraph](https://github.com/langchain-ai/langgraph) | 31k | AI 审图多步推理 Agent 编排 |
| [chroma](https://github.com/chroma-core/chroma) | 27k | 向量数据库（规范文本语义检索，RAG 引擎核心）|

### 视觉 / OCR 层

| 组件 | 来源 | 用途 |
|------|------|------|
| PaddleOCR | 百度开源 | 扫描版 PDF / 图纸中文字识别（支持工程字体）|
| YOLOv8 | Ultralytics | 图元目标检测（钢筋符号、预留洞标识、坡度箭头）|
| Apache AGE | PostgreSQL 扩展 | 规范知识图谱存储（Cypher 查询，条件合规推理）|

### 工作流 / 任务层

| 库 | Stars | 用途 |
|----|-------|------|
| [transitions](https://github.com/pytransitions/transitions) | 6.5k | Python 状态机（三审工作流状态转移核心） |
| [fastapi-celery](https://github.com/GregaVrbancic/fastapi-celery) | 667 | FastAPI + Celery + Redis 异步任务参考架构 |

### 前端框架层

| 库 | Stars | 用途 |
|----|-------|------|
| [ant-design-pro](https://github.com/ant-design/ant-design-pro) | 38k | 管理后台基座（规范库后台、看板、审批界面、模型管理）|
| [pro-components](https://github.com/ant-design/pro-components) | 4.7k | ProTable / ProForm 高级表单组件 |

---

## 技术栈（最终确认）

```
前端:     React 18 + TypeScript + UmiJS Max（已实现）
          Ant Design 5 + ProComponents（ProTable/ProForm）
          react-pdf-viewer（图纸预览，待集成）
          pdfme（报告生成，待集成）
          PWA（移动端，待实现）

后端:     FastAPI (Python 3.12+)（已实现）
          databases + asyncpg（已实现）
          transitions（三审工作流状态机，已实现）
          Celery + Redis（异步任务队列，已实现）

AI 审图（四引擎）:
  引擎1   YAML 规则引擎（已实现：common.yaml + structure.yaml；architecture/mep/decoration 待补充）
  引擎2   Apache AGE（Cypher 查询 + SQL 降级，已实现；NLP 提取流水线待实现）
  引擎3   Chroma + ModelRouter（RAG 链路已实现；LangGraph Agent 待实现）
  引擎4   ezdxf/PyMuPDF/PaddleOCR（视觉提取已实现；YOLOv8 图元检测待实现）

模型路由: ModelRouter（已完整实现：30s 缓存 + 4 种提供商 + 断路器 + 回退链 + 日志）
          管理后台：五标签页模型管理 + 引擎业务参数配置（已实现）

数据库:   PostgreSQL 16 + AGE（Schema 已设计；AGE 扩展按需启用）
          Redis 7（Celery + 断路器）
          Chroma（向量存储）

文件存储: MinIO（已集成，图纸上传/下载/预签 URL）

容器化:   Docker Compose（开发，待配置）→ Kubernetes（生产，待部署）
```

---

## 风险识别（V4.0）

| 风险 | 概率 | 影响 | 应对策略 |
|------|------|------|---------|
| DXF/DWG 复杂格式兼容性 | 高 | 高 | ezdxf 覆盖 R12-R2018；复杂 DWG 先转 PDF/DXF；提前做兼容矩阵 |
| KG 引擎规范条件逻辑抽取质量 | 高 | 高 | Sonnet 提取 + 人工抽样审核（每批 5%）；置信度低于 0.7 的条文标记为"待审核" |
| AI 审图强条检出率不足 95% | 中 | 高 | 四引擎互补；规则引擎保底；KG 条件推理；RAG 语义兜底；迭代优化 |
| Apache AGE 与 PostgreSQL 版本兼容 | 中 | 中 | 锁定 AGE 1.5.0 + PG 16；CI 环境固定版本 Docker 镜像 |
| 模型路由缓存竞争（多 Worker） | 低 | 中 | DB 为单一数据源（30s 缓存）；cache 失效时并发回源有幂等性 |
| 规范版权（自动抓取外部 API） | 中 | 中 | 优先住建部官方开放接口；文件导入由用户自行获取合规文件 |
| 三审状态机边界条件漏洞 | 低 | 极高 | transitions 库 + DB 约束双重保护；100% 状态边界测试覆盖 |

---

## 实施路线图（V4.0）

### Phase 0：基础建设（第 1 个月）

**目标**: 开发环境 + 数据库 Schema + 项目骨架 + 开源库集成验证

**任务清单**:

- [~] Monorepo 初始化（pnpm workspaces）
  - `apps/web` — React 前端（已创建）
  - `apps/api` — FastAPI 后端（已创建）
  - `apps/ai-review` — AI 审图微服务（目录预留）
  - `packages/shared-types` — 共享 TypeScript 类型（待创建）
- [ ] Docker Compose 开发环境
  - PostgreSQL 16 + Apache AGE 扩展
  - Redis 7
  - MinIO
  - Chroma
  - Ollama（可选，本地模型测试）
- [x] 数据库迁移脚本
  - **Migration 001**: 核心业务表（users/projects/drawings/economic_reviews/incentive_proposals/regulations/audit_logs）
  - **Migration 002**: 模型路由管理表（llm_providers/llm_models/engine_model_configs/engine_params/llm_call_logs/prompt_templates）+ 种子数据（4 提供商 + 8 模型）
- [x] FastAPI 骨架（路由结构 / 错误处理 / JWT 认证 / RBAC 中间件）
- [x] React + UmiJS Max 前端骨架（完整 scaffold + 路由 + 全局状态）
- [x] **模型路由层完整实现**:
  - `core/llm/providers/` — 4 种提供商适配（Anthropic/OpenAI-compat/Ollama/CustomHTTP）
  - `core/llm/router.py` — ModelRouter（30s 缓存 + 回退链 + 调用日志）
  - `core/llm/circuit_breaker.py` — Redis 分布式断路器（CLOSED/OPEN/HALF_OPEN）
  - `routers/admin/` — 5 个管理 API 模块（providers/models/engine_configs/engine_params/call_logs）
- [x] **管理后台 UI**:
  - `ModelManagement/` — 五标签页（健康看板/提供商/模型/引擎配置/日志）
  - `EngineParams/` — 引擎业务参数 Schema 驱动表单
- [ ] **开源库集成验证**（关键风险前置，未系统性验证）:
  - ezdxf 解析 5 种不同版本 DWG
  - IfcOpenShell 解析标准 IFC 文件，验证碰撞检测接口
  - PyMuPDF 解析工程 PDF，验证坐标系与批注写入
  - Apache AGE 建图 + Cypher 查询验证
  - PaddleOCR 识别工程图纸中文字识别率测试
  - transitions 状态机跑通三审流程 Demo
  - Chroma 向量化规范文本，验证语义检索相关性
  - ModelRouter 调用 Claude API 和 Ollama 端对端测试
- [ ] CI/CD 管道（GitHub Actions：lint + test + build）

**交付成果**: 可运行骨架系统 ✅ + 模型管理后台 ✅ + 开源库集成可行性报告（待）

---

### Phase 1：三审三算核心流程（第 2-3 个月）

**目标**: 三审工作流完整闭环，二审强制约束在系统层面不可绕过

**任务清单**:

#### 后端（Week 1-4）

- [x] 图纸上传 API（MinIO 分片上传 + 元数据入库 + 触发 AI 审图 Celery 任务）
- [x] 三审状态机（transitions 库）
  - 状态：`draft → ai_reviewing → ai_done → technical_review → economic_review → settlement_review → published`
  - 守卫函数：前置条件检查（二审签字、限额领料单等）
  - 状态变更全部写入 `audit_logs`
- [x] 一审 API（项目总工）+ 二审 API（经济师，含签字 + 403 硬拦截）+ 三审 API（限额领料单 PDF）
- [~] 重大变更自动升级（`estimated_impact >= 500000` 触发集团级审批）
  - 规则引擎 CMN-004 已检出预警；集团级多人审批路径待实现
- [ ] 审批通知（企业微信 Webhook 预留）

#### 前端（Week 3-6）

- [x] 图纸列表页（ProTable）+ 图纸详情页（iframe PDF 预览 + 审批时间轴）
  - 注：react-pdf-viewer 待替换 iframe，实现图纸标注功能
- [x] 一审/二审/三审操作面板（角色控制 + 二审未签字灰色禁用）
  - 二审 403 `ECONOMIC_REVIEW_NOT_SIGNED` 错误码已处理
  - 三审 403 `QUOTA_SHEET_MISSING` 错误码已处理
- [x] 权限控制（RBAC：按角色控制每个操作入口的可见性/可操作性）
- [ ] PWA 配置（Service Worker + Manifest）

**验收标准**:
- 完整跑通 10 条图纸深化流程（含 3 条重大变更）
- 二审绕过测试：直接调用三审 API 返回 403，前端入口禁用 ✅（已实现）

---

### Phase 2：AI 智能审图（第 3-4 个月，与 Phase 1 后半段并行）

**目标**: 四引擎 AI 审图微服务，规范符合性 + 碰撞检测自动化

#### 2A — 引擎 1：规则引擎（Week 1-2）

- [x] YAML DSL 规范规则设计（`data/rules/common.yaml`、`data/rules/structure.yaml`）
  - condition 类型：regex/empty/not_empty/in/not_in/gte/lte/gt/lt/eq/neq/contains/and/or + negate 参数
- [~] 几何/阈值检查引擎（ezdxf 实体提取已实现；消防分区面积/疏散距离等几何计算待实现）
- [x] ezdxf 几何实体提取 + 规则匹配（文字/属性字段级别）
- [x] discipline YAML 规则文件（已完成全部 5 个：`common.yaml`、`structure.yaml`、`architecture.yaml`、`mep.yaml`、`decoration.yaml`）
- [ ] 单图 ≤ 500ms 性能验证

#### 2B — 引擎 2：知识图谱推理引擎（Week 2-5）

- [x] Apache AGE 图查询（Cypher）+ SQL 降级（`regulation_articles` JOIN `regulation_books`）
- [x] 规范 NLP 提取流水线（Haiku 批量分类 + Sonnet 深度提取 + AGE Cypher INSERT）
  - `services/regulation_importer.py`：pymupdf4llm → 段落切分 → Haiku 分类 → Sonnet 提取 → DB/AGE/Chroma，优雅降级
- [x] 合规推理 Cypher 查询（`_MANDATORY_REFS` 强制性条文集）
- [ ] 导入 30 本核心规范测试图谱质量

#### 2C — 引擎 3：RAG + LLM 引擎（Week 3-5）

- [x] 规范文本向量化（Chroma HttpClient，向量检索，优雅降级）
- [x] RAG 链路（Chroma 语义检索 → 上下文 → ModelRouter `rag_qa` 引擎 → JSON 结构化输出）
- [x] LangGraph Agent 三步推理（identify → lookup → synthesize，graceful degradation 自动降级）
- [ ] 与 KG 引擎结果合并（KG 高置信度优先，RAG 补充低置信度条文）

#### 2D — 引擎 4：视觉/OCR 引擎（Week 4-6）

- [x] PaddleOCR 集成（扫描版 PDF 文字识别，作为 PyMuPDF 失败后的降级）
- [x] YOLOv8 图元检测（`yolo_detector.py`，graceful degradation，标题栏检查 + 钢筋密度预警）
- [x] VisionEngine 完整实现：ezdxf（DXF/DWG）+ PyMuPDF fitz（PDF）+ PaddleOCR（扫描图）
  - 标题栏完整性检查（图纸编号/设计人/审核/日期/比例/工程名称）
  - 结果填充 `ctx.extracted_text` + `ctx.ocr_metadata` 供下游引擎使用

#### 2E — 经济测算层（Week 4-7）

- [x] 钢筋翻样计算器（`core/economic/rebar_calculator.py`，GB50010-2010 La/LaE/Ll 公式，从 `engine_params` 读取抗震系数和搭接率系数）
- [x] 下料优化算法（FFD + 2-opt 局部搜索，实测废料率 ≤ 1.5%，比遗传算法更快且稳定）
- [x] `routers/economic_calc.py` API（POST/GET，结果持久化至 `drawing_economic_calcs`）
- [x] 前端 `EconomicCalcPanel.tsx`（钢筋录入表 + 锚固长度结果 + 切割方案 + 节约汇总 + 创效提案跳转）
- [ ] `rebar_annotation_parser` 引擎调用（解析图纸钢筋标注，自动填入钢筋列表）

#### 2F — 集成与报告（Week 6-8）

- [x] Celery 任务驱动四引擎编排（`tasks/ai_review.py`）
- [x] 四引擎 Orchestrator（VisionEngine 串行 → [RulesEngine/KGEngine/RAGEngine] 并行，各 30s 超时）
- [x] 批注版 PDF 生成（`services/ai_report_generator.py`：PyMuPDF 彩色圆圈标注 + 图例）
- [x] 清单版 Excel 生成（openpyxl：Summary + 按严重程度分页，颜色编码）
- [x] 前端 AI 报告展示（`AIReviewPanel.tsx`：Tab 过滤 + 统计行 + PDF/Excel 下载）
- [ ] AI 报告与一审流程联动

**性能验收**:
- 50 张测试图纸：强条检出率 ≥ 95%，单图 ≤ 3 分钟，漏检率 ≤ 5%

---

### Phase 3：创效激励系统（第 4-5 个月）

**目标**: 创效提案发起到奖金凭证生成的完整数字化闭环

#### 后端

- [x] 创效提案 CRUD（提交/列表/详情/拒绝）
- [x] 净节约额自动计算（A - B - C，`bonus_calculator.py`，Decimal 精确计算，计算过程留存 `cost_snapshot`）
- [x] 三方签字 API（顺序约束：项目经理 → 经济师；各环节 403 强制）
- [x] 铁三角奖金分配（集团 20% / 项目 50% / 提案人 30%，compile-time assertion 保证总比例 100%）
- [x] 公示期管理（`tasks/proposal_notice.py`：Celery beat 扫描到期公示，自动推进至 distributing）
- [x] 兑现凭证 PDF 生成（`services/certificate_generator.py`：PyMuPDF A4，蓝色标题 + 铁三角分配表 + 签字确认区）
- [ ] 创效 KPI 统计 + 年度红线预警（年度创效额 < 50 万 → 红色告警）

#### 前端

- [x] 提案列表（漏斗状态标签）+ 发起表单
- [x] 商务测算界面 + 三方签字引导 + 奖金分配界面（铁三角展示）
- [ ] 年度创效看板（项目经理 + 集团两个视角）

---

### Phase 4：规范知识库 + 管理后台 + 上线（第 5-6 个月）

**目标**: 规范库多途径导入、管理后台完善、整体上线

#### 4C — UmiJS 路由配置（已完成）

- [x] 完整 UmiJS Max 项目 scaffold（package.json / tsconfig / .umirc.ts / 全局状态）
- [x] 路由配置（`config/routes.ts`）将所有页面串联到菜单
  - 图纸管理（列表 + 详情）
  - 创效激励（提案列表 + 详情）
  - 系统管理（模型路由管理 + 引擎参数配置，仅管理员可见）
- [x] `src/app.tsx`（getInitialState + request 拦截器 + ProLayout 配置）
- [x] `src/access.ts`（RBAC 访问控制 6 个权限维度）
- [x] `src/pages/Login/index.tsx`（登录页 + JWT 存储 + redirect 处理）
- [x] `src/pages/404.tsx`

#### 4A — 规范知识库（已完成）

- [x] 输入方式 1：页面手动录入（`ArticleList.tsx` 表单 → 自动向量化 + AGE 图入库）
- [x] 输入方式 2：文件批量导入（`BookList.tsx` Upload.Dragger → MinIO → `regulation_import.py` Celery → NLP 流水线）
- [x] 输入方式 3：外部 API 接入（`ApiSourceList.tsx` + `/api-sources` CRUD；定时同步 Celery beat 待接入）
- [x] 规范管理后台：规范书 CRUD / 发布下线控制 / 条文级 CRUD / 规范搜索（4 个前端组件）

#### 4B — 数据看板（已完成）

- [x] 集团级看板（年度创效总额 / KPI 预警 / 图纸 AI 覆盖率 / 一审通过率 / 规范知识库统计 / LLM 30日成本）
- [x] 项目级看板（图纸流转状态 / AI 审图质量 / 提案漏斗 / 近期活动 Timeline）
- [x] KPI 红线预警 Alert（年产值 ≥1 亿且年度创效 <50 万）
- [x] 路由配置（`/dashboard/group` isAdmin + `/dashboard/project` 全员可见）

#### 4D — 收尾与上线（部分完成）

- [x] 前端 PDF 预览升级（`@react-pdf-viewer/core` 内嵌预览，替换 `window.open`，`PdfViewer.tsx`）
- [x] 测试套件（Pytest 单元测试 + Playwright E2E）
  - [x] 三审状态机：100% 状态边界覆盖（含非法跳转，`tests/test_workflow.py`）
  - [x] 经济测算公式：GB50010-2010 手算验证（`tests/test_economic.py`）
  - [x] 规范 API 同步：HTTP 响应解析 / 鉴权类型离线 Mock（`tests/test_regulation_sync.py`）
  - [x] 数据看板 API：权限检查 + 结构校验（`tests/test_dashboard.py`）
  - [x] Playwright E2E：登录 / 图纸列表 / 创效激励 / 看板（`apps/web/tests/e2e/`）
- [x] Docker Compose 开发环境（`infra/docker-compose.yml`：PG+AGE / Redis / MinIO 3桶 / Chroma / minio-init）
- [x] PWA 配置（`public/manifest.json` + `public/sw.js` Service Worker + app.tsx 注册）
- [x] CI/CD（`.github/workflows/ci.yml`：pytest + bandit + tsc + build + Playwright E2E）
- [ ] 性能优化（慢查询 / 缓存策略 / 图纸 CDN）
- [ ] 安全审计（依赖漏洞扫描 / SQL 注入测试 / 日志审查）
- [ ] 用户操作手册（管理员 / 项目总工 / 经济师 / 设计师 / 施工员 / 班组）
- [x] 生产环境 K8s 部署配置（`infra/k8s/`：base Kustomize + production overlay + Prometheus + Grafana）
- [ ] 试点上线（2 个项目：高层住宅 + 大型公建）
- [ ] 培训（分角色，≥ 3 场，每场 ≥ 4 学时）

---

## 近期待办（下一步优先实现）

按优先级排列（已完成项已移除）：

1. ~~**Phase 2E 经济测算层**~~ ✅ 已完成（2026-05-13）
2. ~~**Phase 4B 数据看板**~~ ✅ 已完成（2026-05-13）
3. ~~**外部规范 API 定时同步**~~ ✅ 已完成（2026-05-13）
4. ~~**测试套件**~~ ✅ 已完成（2026-05-13）
5. ~~**PWA 配置**~~ ✅ 已完成（2026-05-13）
6. ~~**CI/CD**~~ ✅ 已完成（2026-05-13）
7. ~~**K8s 生产部署**~~ ✅ 已完成（2026-05-13）
8. ~~**PDF 预览升级**~~ ✅ 已完成（2026-05-13）
9. ~~**YOLOv8 图元检测**~~ ✅ 已完成（2026-05-13）
10. ~~**LangGraph 多轮推理**~~ ✅ 已完成（2026-05-13）

---

## 已完成工作记录

### 第二轮（2026-05-12 ~ 2026-05-13）

- ✅ `data/rules/architecture.yaml`（ARC-001~008，建筑专业：防火分区/疏散/走廊净宽等）
- ✅ `data/rules/mep.yaml`（MEP-001~008，机电专业：管道材质/消防/电气接地等）
- ✅ `data/rules/decoration.yaml`（DEC-001~007，装修专业：防火等级/面层厚度等）
- ✅ `services/ai_report_generator.py`（PyMuPDF 批注版 PDF + openpyxl 多页 Excel，260 行）
- ✅ `routers/drawings.py` 新增 AI 报告 API（`/issues`、`/report-pdf`、`/report-excel`，MinIO 缓存）
- ✅ `pages/drawings/DrawingDetail/AIReviewPanel.tsx`（253 行，Tab 过滤/统计/下载/强条红色预警）
- ✅ `services/certificate_generator.py`（PyMuPDF A4 兑现凭证 PDF，177 行）
- ✅ `tasks/proposal_notice.py`（Celery beat 扫描到期公示 → 自动推进至 distributing，60 行）
- ✅ `services/regulation_importer.py`（规范 NLP 提取流水线，417 行：pymupdf4llm → Haiku 分类 → Sonnet 提取 → DB/AGE/Chroma 优雅降级）
- ✅ `tasks/regulation_import.py`（规范文件异步导入 Celery 任务，MinIO → NLP → DB）
- ✅ `routers/regulations.py`（522 行，规范书/条文/API源 CRUD + 文件导入 + 搜索）
- ✅ `services/regulations.ts`（前端 API 封装，14 个函数）
- ✅ `pages/admin/RegulationManagement/`（BookList / ArticleList / ApiSourceList / RegulationSearch 4 个组件）
- ✅ `config/routes.ts` 新增规范知识库路由
- ✅ `infra/docker-compose.yml`（PG+AGE / Redis / MinIO 3桶 / Chroma / minio-init 健康检查）

### 本轮收尾（2026-05-13）

- ✅ `README.md`（完整入门文档：项目简介/快速启动/API文档/实现状态/开发指南）
- ✅ `.gitignore`（Python + Node/pnpm + 构建产物 + 密钥文件）
- ✅ `.env.example`（完整环境变量模板）
- ✅ CLAUDE.md / PLAN.md 同步更新至最新实现状态
- ✅ Git 初始化 + 推送 `https://github.com/lsgoodlionel/AI-CAD.git`（105 文件，14533 行）

### Sprint 1 — 经济测算引擎（2026-05-13）

- ✅ `core/economic/__init__.py` + `rebar_calculator.py`（GB50010-2010 La/LaE/Ll，FFD + 2-opt 下料优化，210 行）
- ✅ `routers/economic_calc.py`（POST /economic-calc + GET /economic-calc，参数从 engine_params 读取）
- ✅ `migrations/003_economic_calc.sql`（`drawing_economic_calcs` 表，per-drawing upsert）
- ✅ `main.py` 注册第 13 个路由
- ✅ `services/drawings.ts` 新增 `runEconomicCalc` / `getEconomicCalc`
- ✅ `DrawingDetail/EconomicCalcPanel.tsx`（钢筋录入 / 锚固长度表 / 切割方案表 / 节约汇总 / 创效提案跳转）
- ✅ `DrawingDetail/index.tsx` 二审后阶段挂载 EconomicCalcPanel

### Sprint 2 — 数据看板（2026-05-13）

- ✅ `routers/dashboard.py`（集团看板 + 项目看板两端点，聚合查询，~160 行）
- ✅ `main.py` 注册第 14/15 个路由（dashboard_router）
- ✅ `services/dashboard.ts`（getGroupDashboard / getProjectDashboard 2 个前端调用封装）
- ✅ `pages/dashboard/GroupDashboard/index.tsx`（KPI预警 Alert / 四指标卡 / 提案漏斗表 / 图纸状态分布 / LLM成本表）
- ✅ `pages/dashboard/ProjectDashboard/index.tsx`（项目选择器 / KPI红线 / 四指标卡 / 图纸状态 + 专业分布 / 提案漏斗 / 近期活动 Timeline）
- ✅ `config/routes.ts` 新增 `/dashboard/group`（isAdmin）和 `/dashboard/project` 路由

### Sprint 4 — K8s + PDF 预览 + YOLOv8 + LangGraph（2026-05-13）

- ✅ `infra/k8s/base/` 生产 Kustomize 基础层（namespace/configmap/secret.example/postgres/redis/minio/chroma/api/web/celery/ingress/monitoring）
- ✅ `infra/k8s/overlays/production/` 生产 overlay（3副本/registry镜像/ConfigMap合并）
- ✅ `infra/k8s/base/monitoring/` Prometheus（RBAC + scrape_configs）+ Grafana（Datasource ConfigMap + Deployment + Service）
- ✅ `core/ai_review/yolo_detector.py`（YOLOv8 图元检测，graceful degradation，标题栏/钢筋密度校验）
- ✅ `core/ai_review/vision_engine.py` 集成 yolo_detector（PDF/图像文件自动触发）
- ✅ `core/ai_review/langgraph_agent.py`（三步推理 StateGraph：identify→lookup→synthesize，graceful degradation）
- ✅ `core/ai_review/rag_engine.py` 集成 langgraph_agent（替换单次 LLM 调用）
- ✅ `apps/web/src/pages/drawings/DrawingDetail/PdfViewer.tsx`（@react-pdf-viewer + defaultLayoutPlugin，内嵌预览）
- ✅ `apps/web/src/pages/drawings/DrawingDetail/index.tsx` 升级（查看/收起切换 + 内嵌 PdfViewer Card）
- ✅ `apps/web/package.json` 新增 @react-pdf-viewer/core + @react-pdf-viewer/default-layout + pdfjs-dist
- ✅ `requirements.txt` 新增 langgraph/langchain-core 可选注释（graceful degradation）

### Sprint 3 — 测试套件 + PWA + CI/CD（2026-05-13）

- ✅ `tasks/regulation_api_sync.py`（外部规范 API 定时同步，支持 api_key/basic/none，自定义 response_path，500条上限，自动向量化）
- ✅ `migrations/004_regulation_api_sync.sql`（regulation_api_sources 增加 last_sync_count/last_sync_error；regulation_books 增加 api_source_id；regulation_articles 增加 embedding/chapter_no）
- ✅ `core/celery_app.py` 注册 sync_due_sources_task beat（每小时 :05 执行）
- ✅ `routers/regulations.py` 增加 `POST /api-sources/{id}/sync` 手动触发端点
- ✅ `apps/api/tests/` 测试套件（conftest + 4 个测试文件）
  - `test_workflow.py`：三审状态机 100% 状态边界覆盖，含非法跳转 / 驳回路径 / escalate
  - `test_economic.py`：GB50010-2010 公式手算验证（HRB400/C30/d20 全流程）
  - `test_regulation_sync.py`：HTTP 鉴权 / response_path / 错误处理离线 Mock 测试
  - `test_dashboard.py`：权限检查 + 数据结构校验
- ✅ `pytest.ini`（asyncio_mode=auto，cov-fail-under=80）
- ✅ `requirements.txt` 增加 pytest/pytest-asyncio/pytest-cov 测试依赖
- ✅ `apps/web/tests/e2e/` Playwright E2E（login / drawings / incentive + dashboard）
- ✅ `apps/web/playwright.config.ts`（4 项目：chromium/firefox/webkit/mobile-chrome）
- ✅ `apps/web/public/manifest.json`（PWA Web App Manifest，快捷方式 / 图标 / 主题色）
- ✅ `apps/web/public/sw.js`（Service Worker：Cache First 静态 / Network First 页面 / 离线降级）
- ✅ `apps/web/src/app.tsx` 注册 Service Worker（生产环境）
- ✅ `apps/web/config/config.ts` 注入 manifest link / meta 标签
- ✅ `.github/workflows/ci.yml`（4 job：backend-test / backend-security / frontend-lint / frontend-build / e2e）

---

## 开发顺序优先级（V4.0）

```
第 1 优先: 三审工作流状态机 + 二审强制约束（Phase 1 核心，业务价值最高）✅ 已完成
第 2 优先: 模型路由层 + 管理后台（Phase 0，横切所有 AI 引擎）✅ 已完成
第 3 优先: AI 审图规则引擎兜底（5 专业 YAML + 报告生成）✅ 已完成
第 4 优先: 规范知识库（三途径导入 + NLP 流水线 + 管理前端）✅ 已完成
第 5 优先: 创效激励系统（铁三角 + 签字 + 凭证 + 公示）✅ 已完成（KPI 看板待）
第 6 优先: KG 引擎（条件合规推理）[~] 部分完成（查询实现；导入 30 本规范测试待）
第 7 优先: RAG 引擎 [~] 部分完成（基础链路实现；LangGraph 多轮待）
第 8 优先: 经济测算层（钢筋翻样 + FFD+2-opt 下料）✅ 已完成
第 9 优先: 数据看板（集团 + 项目两视角）✅ 已完成
```

---

## 资源需求

| 类别 | 说明 | 估算 |
|------|------|------|
| 开发服务器（开发阶段） | 2-4 核 / 8-16G RAM / SSD | 约 2000 元/月 |
| 生产 K8s 集群 | 含 AI 服务（GPU 可选） | 约 8000-15000 元/月 |
| MinIO 存储 | 图纸文件存储 | 按使用量 |
| LLM API（Claude/DeepSeek 等） | RAG + KG 推理，模型路由层统一管理 | 视调用量；可通过 Ollama 替换降本 |
| 向量数据库（Chroma）| 开源，自托管，零授权费 | 含在服务器成本内 |
| 图数据库（Apache AGE）| PostgreSQL 扩展，开源，零授权费 | 含在 DB 服务器成本内 |
| **AI 采购成本** | 全部使用开源，无需采购 | **0 元** |

---

## 等待确认（已全部解决，记录归档）

| 事项 | 状态 | 决策 |
|------|------|------|
| AI 审图采购 vs 自建 | ✅ 已确认 | 全部自建，整合 GitHub 开源代码 |
| 财务系统对接 | ✅ 已确认 | Phase 1 预留接口 |
| 规范库来源 | ✅ 已确认 | 三途径输入，管理后台维护 |
| 移动端形态 | ✅ 已确认 | PWA，保留原生 App 升级路径 |
| 团队模式 | ✅ 已确认 | 内建团队 |
| AI 引擎架构 | ✅ 已确认 | 四引擎（规则 + KG + RAG + 视觉/OCR）+ 经济测算层 |
| 模型管理方式 | ✅ 已确认 | 管理后台可配置，运行时热切换（30s），支持 Ollama/Cloud 混用 |
