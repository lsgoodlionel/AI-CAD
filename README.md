# 图纸深化全过程管理平台

> 基于《全面推行图纸深化全过程管理体系》构建的数字化管理平台，将图纸深化从"按图施工"升级为"以图创效"，目标挖掘年产值 **2%–3% 的隐性利润**。

---

## 核心能力

| 能力 | 说明 | 状态 |
|------|------|------|
| 三审三算工作流 | 技术规范化 → 经济最优化 → 结算合规化，经济师未签字系统层面硬拦截 | ✅ |
| AI 智能审图（四引擎）| 规则引擎 + 知识图谱 + LangGraph 三步推理 + YOLOv8 图元检测 | ✅ |
| 经济测算引擎 | GB50010-2010 钢筋翻样 + FFD+2-opt 下料优化（废料率 ≤ 1.5%）| ✅ |
| 规范知识库 | 三途径导入（手动/文件/外部 API），NLP 流水线，AGE 图谱，Chroma 语义搜索 | ✅ |
| 创效激励闭环 | 在线提案 → 商务测算 → 三方签字 → 铁三角分配 → 凭证 PDF | ✅ |
| 数据看板 | 集团级（KPI 预警/成本看板）+ 项目级（流转状态/活动 Timeline）| ✅ |
| 模型路由管理 | 运行时热切换 Claude/OpenAI/DeepSeek/Ollama，断路器保护，调用日志 | ✅ |
| PWA / 移动端 | Service Worker（Cache First 静态/Network First 页面）+ manifest | ✅ |
| K8s 生产部署 | Kustomize base + production overlay + Prometheus + Grafana | ✅ |
| CI/CD | GitHub Actions：pytest + bandit + tsc + build + Playwright E2E | ✅ |

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

### AI 审图（四引擎）
- **引擎 1 — 规则引擎**: YAML DSL（5 个专业：common/structure/architecture/mep/decoration）
- **引擎 2 — 知识图谱**: [Apache AGE](https://github.com/apache/age)（PostgreSQL 扩展，Cypher 查询 + SQL 降级）
- **引擎 3 — RAG + LangGraph**: [Chroma](https://github.com/chroma-core/chroma) 向量检索 + [LangGraph](https://github.com/langchain-ai/langgraph) 三步推理（graceful degradation）
- **引擎 4 — 视觉/OCR**: [ezdxf](https://github.com/mozman/ezdxf) + [PyMuPDF](https://github.com/pymupdf/PyMuPDF) + PaddleOCR + [YOLOv8](https://github.com/ultralytics/ultralytics)（graceful degradation）

### 数据与基础设施
- **主库**: PostgreSQL 16（含 Apache AGE 扩展，4 个迁移脚本）
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
│       │   │   └── orchestrator.py   # Vision串行 → [Rules/KG/RAG]并行
│       │   └── workflow/       # 三审状态机（transitions）
│       ├── routers/            # API 路由（15 个模块）
│       ├── services/           # 业务逻辑（报告/规范导入/奖金/凭证等）
│       ├── tasks/              # Celery 任务（ai_review/proposal_notice/regulation_import/regulation_api_sync）
│       ├── tests/              # pytest 测试套件（状态机/公式/API/规范同步）
│       ├── data/rules/         # YAML 规则文件（5 个专业）
│       └── migrations/         # SQL 迁移脚本（001~004）
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
- Node.js 20+ + pnpm 9+

### 1. 克隆并配置环境变量

```bash
git clone https://github.com/lsgoodlionel/AI-CAD.git
cd AI-CAD
cp .env.example .env
# 编辑 .env，填写 JWT_SECRET 和 LLM API Keys
```

### 2. 启动基础服务

```bash
cd infra
docker compose up -d
# PostgreSQL+AGE、Redis、MinIO（3 个桶：drawings/reports/atlases）、Chroma
# 等待所有服务健康（约 30 秒）
```

### 3. 初始化数据库

```bash
cd apps/api
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 执行全部迁移脚本
psql $DATABASE_URL -f migrations/001_initial_schema.sql
psql $DATABASE_URL -f migrations/002_model_management.sql
psql $DATABASE_URL -f migrations/003_economic_calc.sql
psql $DATABASE_URL -f migrations/004_regulation_api_sync.sql
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
pnpm install
pnpm dev
# 访问 http://localhost:3000
```

### 默认账号

数据库种子数据包含一个管理员账号（见 `migrations/001_initial_schema.sql`）：

| 字段 | 值 |
|------|----|
| 账号 | `admin@example.com` |
| 密码 | `Admin@123456` |
| 角色 | `group_admin` |

### 运行测试

```bash
# 后端单元测试
cd apps/api
pytest tests/ -v

# 前端 E2E 测试（需先启动 dev server）
cd apps/web
npx playwright test
```

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
| `/api/v1/technical-review` | 一审（技术规范化）|
| `/api/v1/economic-review` | 二审（经济最优化，含签字强制）|
| `/api/v1/settlement-review` | 三审（结算合规化）|
| `/api/v1/economic-calc` | 钢筋翻样 + 下料优化 |
| `/api/v1/incentive` | 创效提案全生命周期 |
| `/api/v1/regulations` | 规范知识库（书/条文/API源/搜索）|
| `/api/v1/dashboard` | 集团级 + 项目级看板 |
| `/api/v1/admin/providers` | LLM 提供商管理 |
| `/api/v1/admin/models` | LLM 模型管理 |
| `/api/v1/admin/engine-configs` | 引擎配置（模型/温度/tokens）|
| `/api/v1/admin/engine-params` | 引擎业务参数（KG + 经济测算）|
| `/api/v1/admin/call-logs` | 调用日志与成本统计 |

---

## 实现状态

| 模块 | 状态 | 说明 |
|------|------|------|
| JWT 认证 + RBAC | ✅ | Access 24h / Refresh 30d / 6 个权限维度 |
| 三审工作流状态机 | ✅ | transitions 库，二审强制签字 HTTP 403，100% 状态边界测试 |
| MinIO 文件存储 | ✅ | 预签名 URL，5min TTL，AES-256 |
| Celery 异步任务 | ✅ | AI 审图 / 规范导入 / 公示期推进 / 外部 API 同步（每小时 beat）|
| 四引擎 AI 审图框架 | ✅ | 规则/KG/RAG/视觉 Orchestrator，各引擎 30s 超时 |
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
