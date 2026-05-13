# 图纸深化全过程管理平台

> 基于《全面推行图纸深化全过程管理体系》构建的数字化管理平台，将图纸深化从"按图施工"升级为"以图创效"。

---

## 项目简介

本平台覆盖建筑施工全周期，核心目标是通过数字化手段实现年产值 **2%-3% 的隐性利润挖掘**。

**核心能力**:

| 能力 | 说明 |
|------|------|
| 三审三算工作流 | 技术规范化 → 经济最优化 → 结算合规化，经济师未签字系统层面硬拦截 |
| AI 智能审图（四引擎）| 规则引擎 + 知识图谱 + RAG+LLM + 视觉/OCR，强条检出率目标 ≥ 95% |
| 规范知识库 | 三途径导入（手动/文件/API），结构化存储，语义搜索 |
| 创效激励闭环 | 在线提案 → 商务测算 → 三方签字 → 铁三角分配 → 凭证生成 |
| 模型路由管理 | 运行时热切换 Claude/OpenAI/DeepSeek/Ollama，断路器保护，调用日志 |

---

## 技术栈

### 前端
- **框架**: React 18 + TypeScript + [UmiJS Max](https://umijs.org/docs/max/introduce)
- **UI**: [Ant Design 5](https://ant.design) + [Pro Components](https://procomponents.ant.design)（ProTable/ProForm）
- **图纸预览**: react-pdf-viewer
- **构建**: Vite（UmiJS Max 内置）

### 后端
- **框架**: [FastAPI](https://fastapi.tiangolo.com) (Python 3.12+) + databases + asyncpg
- **工作流**: [transitions](https://github.com/pytransitions/transitions) 状态机（三审流程）
- **异步任务**: [Celery](https://docs.celeryq.dev) + Redis
- **文件存储**: [MinIO](https://min.io)（图纸/报告/图集，AES-256 加密，预签名 URL 5min TTL）

### AI 审图（四引擎）
- **规则引擎**: YAML DSL（common/structure/architecture/mep/decoration 五个专业）
- **知识图谱**: [Apache AGE](https://github.com/apache/age)（PostgreSQL 扩展，Cypher 查询）
- **RAG 引擎**: [Chroma](https://github.com/chroma-core/chroma) 向量检索 + ModelRouter
- **视觉/OCR**: [ezdxf](https://github.com/mozman/ezdxf) + [PyMuPDF](https://github.com/pymupdf/PyMuPDF) + PaddleOCR

### 数据与基础设施
- **主库**: PostgreSQL 16（含 Apache AGE 扩展）
- **缓存/队列**: Redis 7
- **向量库**: Chroma
- **容器**: Docker Compose（开发）→ Kubernetes（生产）

---

## 项目结构

```
CAD/
├── CLAUDE.md                  # 开发指南（技术决策/规范/快速启动）
├── .env.example               # 环境变量模板
├── apps/
│   ├── web/                   # 前端（UmiJS Max + React 18）
│   │   ├── config/routes.ts   # 路由配置
│   │   ├── .umirc.ts          # UmiJS 配置（代理/标题/布局）
│   │   └── src/
│   │       ├── access.ts      # RBAC 权限（6 个维度）
│   │       ├── app.tsx        # 全局状态 + 请求拦截
│   │       ├── services/      # API 调用封装
│   │       └── pages/
│   │           ├── Login/          # 登录页
│   │           ├── drawings/       # 图纸管理（列表/详情/AI报告/三审）
│   │           ├── incentive/      # 创效激励（提案/测算/签字/分配）
│   │           └── admin/          # 管理后台（模型/引擎/规范库）
│   └── api/                   # 后端（FastAPI + Celery）
│       ├── main.py            # 12 个路由注册
│       ├── core/
│       │   ├── auth.py        # JWT（Access 24h + Refresh 30d）
│       │   ├── storage.py     # MinIO 封装
│       │   ├── llm/           # 模型路由层（router/circuit_breaker/providers）
│       │   ├── ai_review/     # 四引擎（base/rules/kg/rag/vision/orchestrator）
│       │   └── workflow/      # 三审状态机（transitions）
│       ├── routers/           # API 路由（12 个模块）
│       ├── services/          # 业务逻辑（报告生成/规范导入/奖金计算等）
│       ├── tasks/             # Celery 任务（ai_review/proposal_notice/regulation_import）
│       ├── data/rules/        # YAML 规则文件（5 个专业）
│       └── migrations/        # SQL 迁移脚本
├── infra/
│   └── docker-compose.yml     # 开发环境（PG+AGE/Redis/MinIO/Chroma/minio-init）
└── docs/
    ├── PRD.md                 # 产品需求文档
    ├── PLAN.md                # 开发计划（含完成状态）
    └── ARCHITECTURE.md        # 系统架构设计
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
# 等待所有服务就绪（约 30 秒）
# PostgreSQL+AGE、Redis、MinIO（3 个桶：drawings/reports/atlases）、Chroma
```

### 3. 初始化数据库

```bash
cd apps/api
# 安装依赖
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 执行迁移脚本
psql $DATABASE_URL -f migrations/001_initial_schema.sql
psql $DATABASE_URL -f migrations/002_model_management.sql
```

### 4. 启动后端

```bash
# 在 apps/api 目录（已激活 venv）
uvicorn main:app --reload --port 8000

# 另开一个终端，启动 Celery Worker
celery -A core.celery_app worker --loglevel=info -Q default,ai_review,regulation_import
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

---

## API 文档

后端启动后访问：
- Swagger UI：`http://localhost:8000/docs`
- ReDoc：`http://localhost:8000/redoc`

### 主要 API 模块

| 前缀 | 说明 |
|------|------|
| `/api/v1/auth` | 登录/刷新 Token |
| `/api/v1/drawings` | 图纸上传/审图/报告下载 |
| `/api/v1/technical-review` | 一审（技术规范化）|
| `/api/v1/economic-review` | 二审（经济最优化，含签字强制）|
| `/api/v1/settlement-review` | 三审（结算合规化）|
| `/api/v1/incentive` | 创效提案全生命周期 |
| `/api/v1/regulations` | 规范知识库（书/条文/API源/搜索）|
| `/api/v1/admin/providers` | LLM 提供商管理 |
| `/api/v1/admin/models` | LLM 模型管理 |
| `/api/v1/admin/engine-configs` | 引擎配置（模型/温度/tokens）|
| `/api/v1/admin/engine-params` | 引擎业务参数 |
| `/api/v1/admin/call-logs` | 调用日志与成本统计 |

---

## 实现状态

| 模块 | 状态 | 说明 |
|------|------|------|
| JWT 认证 + RBAC | ✅ | Access 24h / Refresh 30d / 6 个权限维度 |
| 三审工作流状态机 | ✅ | transitions 库，二审强制签字 HTTP 403 |
| MinIO 文件存储 | ✅ | 预签名 URL，5min TTL |
| Celery 异步任务 | ✅ | AI 审图 / 规范导入 / 公示期推进 |
| 四引擎 AI 审图框架 | ✅ | 规则/KG/RAG/视觉 Orchestrator |
| YAML 规则引擎 | ✅ | 5 个专业（common/structure/architecture/mep/decoration）|
| 模型路由层 | ✅ | 4 种提供商 / 断路器 / 回退链 / 调用日志 |
| 管理后台（模型管理）| ✅ | 五标签页 + 引擎参数 Schema 驱动表单 |
| 创效激励系统 | ✅ | 铁三角分配 / 签字顺序约束 / 凭证 PDF |
| AI 审图报告生成 | ✅ | PyMuPDF 批注版 PDF + openpyxl Excel |
| 规范知识库管理 | ✅ | NLP 流水线 + 三途径导入 + 管理前端 |
| 经济测算引擎 | ✅ | GB50010-2010 La/LaE/Ll + FFD+2-opt 下料优化（废料率≤1.5%）|
| 外部规范 API 定时同步 | ✅ | Celery beat 每小时，支持 api_key/basic/自定义 response_path |
| 数据看板 | ✅ | 集团级（KPI 预警 / LLM 成本）+ 项目级（流转状态 / 活动 Timeline）|
| 测试套件 | ✅ | pytest（状态机/公式/API）+ Playwright E2E（登录/图纸/激励/看板）|
| PWA | ✅ | manifest.json + Service Worker（Cache First / Network First）|
| CI/CD | ✅ | GitHub Actions：pytest + bandit + tsc + build + Playwright E2E |

---

## 关键业务规则（硬约束）

1. **二审强制签字**：`economic_reviews.economist_signed_at IS NULL` → API 返回 `403 ECONOMIC_REVIEW_NOT_SIGNED`，前端入口禁用
2. **无限额领料单不发布**：`material_quota_sheet` 为 NULL → 图纸无法进入 `published` 状态（`403 QUOTA_SHEET_MISSING`）
3. **三方签字顺序**：项目经理未签 → 经济师签字请求 403；经济师未签 → 集团总监请求 403
4. **铁三角比例**：集团 20% / 项目 50% / 提案人 30%，代码层编译期断言保证总比例不可修改
5. **状态机守卫**：所有状态变更写入 `audit_logs`，非法跳转由 transitions 库在 DB 约束层双重拦截

---

## 开发指南

### 添加新的 LLM 引擎

1. 在 `core/ai_review/` 下创建继承 `BaseEngine` 的类
2. 实现 `async def analyze(ctx: DrawingContext) -> list[AIIssue]`
3. 在 `orchestrator.py` 中注册到并行引擎组
4. 在管理后台"引擎配置"中为新引擎添加模型配置

### 添加规范规则

在 `apps/api/data/rules/` 下编辑对应专业的 YAML 文件，规则格式：

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

## 待实现功能（下一步）

1. **前端 PDF 预览升级**：react-pdf-viewer 替换 iframe，支持图纸批注标注
2. **YOLOv8 图元检测**：钢筋符号 / 预留洞标识训练，提升视觉引擎检出率
3. **LangGraph 多轮推理**：RAG 引擎升级为 Agent 多步推理，提升复杂条文理解
4. **性能优化**：慢查询分析 / Redis 缓存策略 / 图纸 CDN 加速
5. **K8s 生产部署**：`infra/k8s/`（Nginx + Prometheus + Grafana）

---

## 贡献指南

### 分支策略

- `main`：稳定主分支
- `feat/xxx`：功能开发分支
- `fix/xxx`：缺陷修复分支

### 提交规范

```
feat: 添加经济测算引擎
fix: 修复二审签字状态未更新问题
refactor: 重构规范导入流水线
```

### 代码规范

- 前端：TypeScript 严格模式，`eslint` + `prettier`
- 后端：`ruff` linting，函数 < 50 行，文件 < 800 行
- 所有状态变更必须写入 `audit_logs`（只追加，不修改）
- 禁止在代码中硬编码 API Key 或密码

---

## License

MIT
