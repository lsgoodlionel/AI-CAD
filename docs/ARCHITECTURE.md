# 系统架构设计 — 图纸深化全过程管理平台

> 版本：V2.1 | 日期：2026-05-12
>
> **实现标注**: `✅ 已实现` · `🔶 部分实现` · `❌ 未实现`
>
> ### 已实现组件速查
>
> | 层次 | 已实现 | 待实现 |
> |------|--------|--------|
> | 客户端 | Web 应用（UmiJS Max）✅ | PWA 移动端 ❌、大屏看板 ❌ |
> | API 服务 | 三审 API、创效 API、Auth API、Admin API ✅ | 规范知识库 API ❌、看板 API ❌ |
> | AI 审图 | 四引擎框架、ModelRouter、断路器 ✅ | NLP 提取流水线 ❌、报告生成 ❌、经济测算引擎 ❌ |
> | 数据层 | PostgreSQL Schema（001+002 Migration）✅ | AGE 图建图 ❌ |
> | 文件存储 | MinIO（上传/presigned URL）✅ | — |
> | 向量存储 | Chroma 集成（优雅降级）✅ | 规范文本批量向量化 ❌ |

---

## 一、整体架构

```
┌──────────────────────────────────────────────────────────────────┐
│                           客户端层                                │
│  ┌───────────────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │  Web 应用（React）  │  │ 移动端（PWA）│  │  大屏看板          │  │
│  └───────────────────┘  └──────────────┘  └───────────────────┘  │
└─────────────────────────────┬────────────────────────────────────┘
                              │ HTTPS / WebSocket
┌─────────────────────────────▼────────────────────────────────────┐
│                          API 网关层                               │
│             Nginx（反向代理 + 限流 + SSL 终止）                     │
└─────────────────────────────┬────────────────────────────────────┘
                              │
┌─────────────────────────────▼────────────────────────────────────┐
│                         应用服务层                                │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │              核心 API 服务 (FastAPI)                        │  │
│  │  - 三审三算工作流 API    - 创效激励管理 API                  │  │
│  │  - 规范知识库 API        - 数据看板 API                     │  │
│  │  - 用户与权限 API        - 管理后台 API（模型/引擎/参数）     │  │
│  └────────────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │              AI 审图微服务 (Python)                          │  │
│  │  ┌──────────────────────────────────────────────────────┐  │  │
│  │  │               模型路由层（ModelRouter）                 │  │  │
│  │  │  DB 配置（30s 缓存）→ Provider → 断路器 → 回退链        │  │  │
│  │  │  调用日志异步写入（tokens/延迟/费用/成功率）              │  │  │
│  │  └──────────────────────────────────────────────────────┘  │  │
│  │  ├── 引擎 1：规则引擎（YAML DSL，零 LLM，毫秒级）            │  │  │
│  │  ├── 引擎 2：知识图谱推理（Apache AGE + Cypher）             │  │  │
│  │  ├── 引擎 3：RAG + LLM 引擎（LangChain + Chroma）          │  │  │
│  │  ├── 引擎 4：视觉/OCR 引擎（PaddleOCR + YOLOv8）           │  │  │
│  │  └── 经济测算层（钢筋翻样 + 下料优化 + 成本对比）             │  │  │
│  └────────────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │              消息 / 通知服务                                 │  │
│  │  - 审批节点推送（App/邮件/企业微信）- KPI 预警推送             │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────┬────────────────────────┬──────────────────────┘
                   │                        │
┌──────────────────▼──────────┐  ┌──────────▼──────────────────────┐
│          数据层              │  │          文件存储层               │
│  ┌──────────────────────┐   │  │  ┌────────────────────────────┐  │
│  │  PostgreSQL 16       │   │  │  │    MinIO 对象存储            │  │
│  │  + Apache AGE 扩展   │   │  │  │  - 图纸文件（加密存储）       │  │
│  │  （主业务 + 图数据库）  │   │  │  │  - 审查报告 PDF            │  │
│  └──────────────────────┘   │  │  │  - 标准图集                  │  │
│  ┌──────────────────────┐   │  │  └────────────────────────────┘  │
│  │  Redis 7             │   │  │  ┌────────────────────────────┐  │
│  │  缓存/队列/断路器状态  │   │  │  │    Chroma 向量数据库         │  │
│  └──────────────────────┘   │  │  │  规范语义检索（RAG 引擎）      │  │
└─────────────────────────────┘  │  └────────────────────────────┘  │
                                 └──────────────────────────────────┘
```

---

## 二、模型路由层架构

### 2.1 设计目标

- **运行时热切换**：管理员在后台变更引擎模型，30 秒内全量生效，无需重启
- **多提供商支持**：Anthropic Claude / OpenAI 兼容（DeepSeek/Qwen 等）/ Ollama 本地 / 自定义 HTTP
- **分布式容错**：Redis 断路器跨 Worker 共享状态，自动回退链
- **全量可观测**：每次 LLM 调用异步写入日志（tokens/延迟/费用/成功率）

### 2.2 核心组件

```
apps/api/core/llm/
├── providers/
│   ├── base.py           # LLMProvider 抽象基类 + ModelParams + LLMResponse
│   ├── anthropic_provider.py  # Anthropic SDK 封装（claude-3.5/4.x 系列）
│   ├── openai_compat.py  # OpenAI 兼容接口（DeepSeek/Qwen/GPT-4o 等）
│   ├── ollama_provider.py     # httpx 调用本地 Ollama
│   └── custom_http.py    # Jinja 请求模板 + JSONPath 响应提取
├── router.py             # ModelRouter 主控
└── circuit_breaker.py    # Redis 分布式断路器（CLOSED/OPEN/HALF_OPEN）
```

### 2.3 调用流程

```
engine.call(messages)
    │
    ▼
ModelRouter.route(engine_name, messages, task_type="primary")
    │
    ├─ 查询缓存（TTL 30s）
    │    └─ 未命中 → 读 DB engine_model_configs
    │
    ├─ 获取 Provider 实例（按 provider_type 工厂创建，内部缓存）
    │
    ├─ 断路器检查（Redis key: circuit:{engine}:{task_type}）
    │    ├─ CLOSED → 直接调用
    │    ├─ OPEN → 直接触发回退
    │    └─ HALF_OPEN → 试探性调用（成功 2 次 → CLOSED）
    │
    ├─ Provider.complete(messages, params)
    │    ├─ 成功 → 重置断路器计数，返回结果
    │    └─ 失败 → 断路器计数 +1（≥5 次 → OPEN）
    │              → 查找 fallback_1 配置
    │              → 递归调用（最深 fallback_2）
    │
    └─ asyncio.create_task(_log(...))  # 非阻塞写日志
```

### 2.4 提供商类型

| type | 适用 | 关键配置 |
|------|------|---------|
| `anthropic` | Claude 系列 | `api_key_env`（环境变量名，不存明文）|
| `openai_compat` | OpenAI / DeepSeek / Qwen / Moonshot | `base_url` + `api_key_env` |
| `ollama` | 本地部署大模型 | `base_url`（默认 http://localhost:11434）|
| `custom_http` | 自研模型 / 专业垂直模型 | `base_url` + `extra_params.request_template`（Jinja）+ `extra_params.response_path`（JSONPath）|

### 2.5 预定义引擎名称（13 个）

| 引擎名称 | 所属系统 | 典型模型选择 |
|---------|---------|------------|
| `regulation_classifier` | 规范知识图谱 NLP | Claude Haiku 4.5（批量高效）|
| `regulation_extractor` | 规范知识图谱 NLP | Claude Sonnet 4.6（深度提取）|
| `kg_compliance_reasoning` | KG 引擎合规推理 | Claude Sonnet 4.6 |
| `kg_suggestion_generator` | KG 引擎建议生成 | Claude Haiku 4.5 |
| `kg_diff_analyzer` | KG 引擎版本差异分析 | DeepSeek Chat |
| `rag_qa` | RAG 规范问答 | Claude Haiku 4.5 |
| `rag_rewriter` | RAG 查询改写 | Claude Haiku 4.5 |
| `rebar_annotation_parser` | 钢筋标注解析 | Claude Sonnet 4.6 |
| `cost_explanation_writer` | 经济测算说明 | Claude Haiku 4.5 |
| `optimization_hint_writer` | 优化建议撰写 | Claude Haiku 4.5 |
| `report_summary_writer` | 审图报告摘要 | Claude Sonnet 4.6 |
| `drawing_visual_analyzer` | 图纸视觉分析 | Claude Sonnet 4.6（支持视觉）|
| `incentive_description_writer` | 创效描述生成 | Claude Haiku 4.5 |

---

## 三、AI 审图四引擎详细设计

> **实现状态**:
> - 引擎 1 规则引擎：✅ `core/ai_review/rules_engine.py`（YAML DSL，条件评估器；`common.yaml`+`structure.yaml` 已完成，architecture/mep/decoration 待补充）
> - 引擎 2 KG 引擎：🔶 `core/ai_review/kg_engine.py`（AGE Cypher 查询 + SQL 降级已实现；规范 NLP 提取流水线待实现）
> - 引擎 3 RAG 引擎：🔶 `core/ai_review/rag_engine.py`（Chroma 检索 + ModelRouter `rag_qa` 已实现；LangGraph Agent 待实现）
> - 引擎 4 视觉引擎：🔶 `core/ai_review/vision_engine.py`（ezdxf/fitz/PaddleOCR 已实现；YOLOv8 图元检测待实现）
> - 编排器：✅ `core/ai_review/orchestrator.py`（Vision 串行 → [Rules/KG/RAG] 并行，各 30s 超时）
> - Celery 任务：✅ `tasks/ai_review.py`（DrawingContext 构建 + 引擎调度 + 结果写库 + 状态更新）

### 3.1 引擎 2 — 知识图谱推理引擎

#### 规范本体（Ontology）

```
RegBook（规范文件）
  ├── id, title, std_no, version, discipline
  └── Chapter（章节）
       └── Article（条文）
            ├── obligation_level: MUST | SHOULD | MAY | MUST_NOT
            ├── is_mandatory: bool（强制性条文）
            └── Condition（适用条件）
                 └── Requirement（具体要求）
                      └── Parameter（量化参数）
```

#### 规范 NLP 提取流水线

```
PDF/Word 规范文件
    ↓ pymupdf4llm → Markdown
    ↓ 段落分割（章节 / 条文 / 款项）
    ↓ Haiku 批量分类（classify_batch_size=20）
    │   → 类型标签：simple_rule / conditional_rule / cross_ref / definition
    ↓ Sonnet 深度提取（extract_confidence_min=0.7）
    │   → 实体：{条文号, 义务等级, 条件触发词, 量化参数}
    │   → 关系：APPLIES_TO / REQUIRES / DEPENDS_ON / CONTRADICTS
    ↓ Apache AGE 图存储（Cypher INSERT）
    ↓ bge-m3 向量化 → Chroma（语义检索备用）
```

#### 合规推理（Cypher 示例）

```cypher
MATCH (rule:Article {is_mandatory: true})-[:APPLIES_TO]->(scope)
WHERE scope.discipline = $discipline
  AND rule.condition IS NULL OR $drawing_params @> rule.condition
RETURN rule.article_no, rule.requirement, rule.obligation_level
ORDER BY rule.obligation_level DESC
```

### 3.2 经济测算层 — 钢筋翻样规则 ❌（待实现）

> 引擎业务参数 Schema 已在 `engine_params` 表中定义（`scope='economic'` 的 18 个参数），但计算引擎本体（钢筋翻样 + 遗传算法下料优化）尚未实现。

#### 锚固长度计算（GB50010-2010）

```python
# 基本锚固长度
LaE = ζaE × α × (fy / ft) × d

# 参数说明
ζaE: 抗震系数（从 DB economic 参数读取）
    一级 / 二级 = 1.15
    三级 = 1.05
    四级 = 1.00

α: 钢筋外形系数（光圆 = 0.16，带肋 = 0.14）
fy: 钢筋抗拉强度设计值（MPa）
ft: 混凝土抗拉强度设计值（MPa）
d: 钢筋直径（mm）
```

#### 搭接长度计算

```python
# 搭接修正系数（从 DB economic 参数读取）
ζl: 搭接百分率 ≤25% → 1.20
    搭接百分率  50% → 1.40
    搭接百分率 100% → 1.60

Ll = ζl × LaE
```

#### 弯钩增加长度

| 弯钩类型 | 增加长度 |
|---------|---------|
| 180° 半圆弯钩 | 6.25d |
| 90° 直弯钩 | 4d |
| 135° 斜弯钩（箍筋） | 11.9d |

#### 下料优化算法

```
输入: 构件钢筋列表（规格 + 下料长度 + 数量）
配置: standard_bar_lengths（从 DB 读取，如 9000/10000/12000）
     target_waste_rate（目标废料率，默认 1.5%）

算法: 遗传算法（Generation=200, Population=100）
     - 染色体：每根原料的下料排列方案
     - 适应度：max(利用率) + min(废料根数)
     - 约束：单根原料总长 ≤ 原料长度；接头位置满足规范

输出: 下料单（原料根数 + 废料量 + 对比原方案节约率）
```

---

## 四、数据库 Schema 设计（完整）

### 4.1 核心业务表（Migration 001）

```sql
-- 图纸主表
CREATE TABLE drawings (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id      UUID NOT NULL REFERENCES projects(id),
  drawing_no      VARCHAR(100) NOT NULL,
  discipline      VARCHAR(50) NOT NULL,  -- 建筑/结构/机电/装修
  version         VARCHAR(20) NOT NULL,
  status          VARCHAR(50) NOT NULL,  -- draft/in_review/approved/published
  current_stage   VARCHAR(50),           -- ai_review/technical/economic/settlement
  created_by      UUID NOT NULL REFERENCES users(id),
  created_at      TIMESTAMPTZ DEFAULT now(),
  updated_at      TIMESTAMPTZ DEFAULT now()
);

-- 二审强制签字表（核心约束）
CREATE TABLE economic_reviews (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  drawing_id        UUID NOT NULL REFERENCES drawings(id),
  alternatives      JSONB NOT NULL,        -- 多方案对比数据
  selected_option   VARCHAR(10) NOT NULL,  -- 选中方案
  economist_id      UUID REFERENCES users(id),
  economist_signed_at TIMESTAMPTZ,         -- NULL = 未签字，图纸锁定
  total_saving      NUMERIC(15,2),         -- 预估节约额
  created_at        TIMESTAMPTZ DEFAULT now()
);

-- 创效提案表
CREATE TABLE incentive_proposals (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id      UUID NOT NULL REFERENCES projects(id),
  drawing_id      UUID REFERENCES drawings(id),
  proposer_id     UUID NOT NULL REFERENCES users(id),
  proposal_type   VARCHAR(10) NOT NULL,  -- A/B
  description     TEXT NOT NULL,
  raw_saving_est  NUMERIC(15,2),         -- 提案人粗估
  status          VARCHAR(50) NOT NULL,  -- draft/calculating/signing/approved/paid
  net_saving      NUMERIC(15,2),         -- 商务核算净节约额
  created_at      TIMESTAMPTZ DEFAULT now()
);
```

### 4.2 模型路由管理表（Migration 002）

```sql
-- 提供商（支持 4 种类型）
CREATE TABLE llm_providers (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name          VARCHAR(100) NOT NULL UNIQUE,
    provider_type VARCHAR(50) NOT NULL
        CHECK (provider_type IN ('anthropic','openai_compat','ollama','custom_http')),
    base_url      TEXT,
    api_key_env   VARCHAR(100),          -- 环境变量名，不存明文
    timeout_sec   INTEGER DEFAULT 120,
    is_active     BOOLEAN DEFAULT true,
    metadata      JSONB DEFAULT '{}',
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- 模型（关联提供商）
CREATE TABLE llm_models (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_id         UUID NOT NULL REFERENCES llm_providers(id) ON DELETE CASCADE,
    model_id            VARCHAR(200) NOT NULL,      -- "claude-sonnet-4-6"
    display_name        VARCHAR(200) NOT NULL,
    context_window      INTEGER,
    supports_vision     BOOLEAN DEFAULT false,
    input_price_per_1m  NUMERIC(10,4) DEFAULT 0,   -- USD/百万 token，本地填 0
    output_price_per_1m NUMERIC(10,4) DEFAULT 0,
    benchmark_score     NUMERIC(5,2),
    is_active           BOOLEAN DEFAULT true,
    created_at          TIMESTAMPTZ DEFAULT now(),
    UNIQUE (provider_id, model_id)
);

-- 引擎模型配置（每引擎 × 任务类型 → 独立配置）
CREATE TABLE engine_model_configs (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    engine_name             VARCHAR(100) NOT NULL,
    task_type               VARCHAR(50) NOT NULL
        CHECK (task_type IN ('primary','fallback_1','fallback_2','batch')),
    model_id                UUID NOT NULL REFERENCES llm_models(id),
    temperature             NUMERIC(4,2) DEFAULT 0.10,
    max_tokens              INTEGER DEFAULT 2048,
    top_p                   NUMERIC(4,2) DEFAULT 1.00,
    frequency_penalty       NUMERIC(4,2) DEFAULT 0.00,
    prompt_template_version VARCHAR(50),
    extra_params            JSONB,         -- custom_http 请求模板等
    is_enabled              BOOLEAN DEFAULT true,
    updated_at              TIMESTAMPTZ DEFAULT now(),
    updated_by              UUID REFERENCES users(id),
    UNIQUE (engine_name, task_type)
);

-- 引擎业务参数（KG / 经济测算 / AI审查 / 钢筋翻样）
CREATE TABLE engine_params (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scope       VARCHAR(50) NOT NULL,   -- 'kg' | 'economic' | 'ai_review' | 'rebar'
    param_key   VARCHAR(100) NOT NULL,
    param_value JSONB NOT NULL,
    description TEXT,
    updated_at  TIMESTAMPTZ DEFAULT now(),
    updated_by  UUID REFERENCES users(id),
    UNIQUE (scope, param_key)
);

-- 调用日志（按月分区）
CREATE TABLE llm_call_logs (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    engine_name       VARCHAR(100),
    model_db_id       UUID REFERENCES llm_models(id),
    prompt_tokens     INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    latency_ms        INTEGER DEFAULT 0,
    cost_usd          NUMERIC(12,8) DEFAULT 0,
    success           BOOLEAN NOT NULL,
    error_type        VARCHAR(200),
    created_at        TIMESTAMPTZ DEFAULT now()
) PARTITION BY RANGE (created_at);

-- 提示词模板版本管理
CREATE TABLE prompt_templates (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    engine_name VARCHAR(100) NOT NULL,
    task_type   VARCHAR(100) NOT NULL,
    version     VARCHAR(50) NOT NULL,
    template    TEXT NOT NULL,
    is_active   BOOLEAN DEFAULT false,
    created_at  TIMESTAMPTZ DEFAULT now(),
    created_by  UUID REFERENCES users(id),
    UNIQUE (engine_name, task_type, version)
);

-- 唯一索引：同一 engine+task_type 只有一个 active 提示词模板
CREATE UNIQUE INDEX idx_pt_active ON prompt_templates (engine_name, task_type)
    WHERE is_active = true;
```

### 4.3 实体关系概览

```
Organization (集团组织架构)
  ├── Company (分公司)
  │    └── Project (项目)
  │         └── WorkZone (工作面)
  └── User (用户) └── UserRole (角色)

Drawing (图纸)
  ├── DrawingVersion (版本)
  ├── ReviewTask (审批任务)
  │    ├── TechnicalReview (一审)
  │    ├── EconomicReview (二审)  ← economist_signed_at 强制签字
  │    └── SettlementReview (三审)
  └── AiReviewReport (AI 审查报告) └── AiReviewIssue (问题条目)

IncentiveProposal (创效提案)
  ├── CostCalculation (成本测算)
  ├── ProposalApproval (三方签字)
  └── BonusDistribution (奖金分配) └── BonusPayment (发放记录)

[模型路由]
LLMProvider (提供商)
  └── LLMModel (模型)
       └── EngineModelConfig (引擎配置 × 任务类型)
            └── LLMCallLog (调用日志，按月分区)

EngineParams (引擎业务参数，scope × param_key)
PromptTemplates (提示词版本，唯一 active)
```

---

## 五、管理后台 API 设计

### 5.1 模型管理 API（`/api/v1/admin/llm`）

```
# 提供商
GET    /providers                    # 列表（含健康状态）
POST   /providers                    # 创建
PATCH  /providers/{id}               # 更新
DELETE /providers/{id}               # 删除（级联禁用关联模型）
POST   /providers/{id}/health-check  # 单个提供商健康检查
GET    /providers/health-all         # 全量健康检查

# 模型
GET    /models?provider_id=          # 列表（按提供商筛选）
POST   /models                       # 创建
PATCH  /models/{id}                  # 更新（价格/上下文窗口/是否激活）
DELETE /models/{id}                  # 删除

# 引擎配置
GET    /engine-configs?engine_name=  # 列表（按引擎筛选）
GET    /engine-configs/engines       # 返回所有引擎名称列表
GET    /engine-configs/summary       # 健康看板摘要（每引擎当前模型状态）
POST   /engine-configs               # 创建
PATCH  /engine-configs/{id}          # 更新（含温度/tokens/模型切换）
DELETE /engine-configs/{id}          # 删除

# 调用日志
GET    /logs/summary?start_date=&engine_name=  # 7日/30日成本汇总
GET    /logs/daily?days=&engine_name=          # 每日调用量/费用趋势
GET    /logs/errors?limit=&engine_name=        # 最近错误日志
GET    /logs/circuit-breakers                  # 断路器状态
```

### 5.2 引擎业务参数 API（`/api/v1/admin/engine-params`）

```
GET    /schema/{scope}               # 返回参数 Schema（前端动态渲染表单）
GET    /{scope}                      # 返回当前值（合并 DB 值与默认值）
GET    /{scope}/value/{param_key}    # 单个参数当前值（供引擎代码内部调用）
PUT    /{scope}/{param_key}          # 更新单个参数
POST   /{scope}/reset/{param_key}    # 重置为默认值（删除 DB 记录）
```

---

## 六、API 设计原则

### 统一响应格式

```json
{
  "success": true,
  "data": { },
  "error": null,
  "meta": {
    "total": 100,
    "page": 1,
    "limit": 20
  }
}
```

### 主要业务端点

```
# 图纸管理（✅ 已实现）
POST   /api/v1/drawings                    # 上传图纸（MinIO + Celery AI 任务）
GET    /api/v1/drawings                    # 图纸列表（分页 + 状态过滤）
GET    /api/v1/drawings/{id}               # 获取图纸详情
GET    /api/v1/drawings/{id}/download-url  # 获取 presigned 下载 URL（5min 有效）

# AI 审图（🔶 部分实现）
POST   /api/v1/drawings/{id}/ai-review     # 触发 AI 审查（Celery 任务）✅
GET    /api/v1/drawings/{id}/ai-review     # 获取 AI 审查结果（问题列表）✅

# 三审（✅ 已实现，含 403 强制约束）
POST   /api/v1/technical-reviews           # 提交一审（项目总工）
GET    /api/v1/technical-reviews/{drawing_id}  # 获取一审结果
POST   /api/v1/economic-reviews            # 提交二审（多方案）
POST   /api/v1/economic-reviews/{id}/sign  # 经济师签字（403 约束上游）
POST   /api/v1/economic-reviews/{id}/approve   # 通过二审（检查签字状态）
POST   /api/v1/economic-reviews/{id}/reject    # 驳回二审
POST   /api/v1/settlement-reviews          # 提交三审（含结算节点）
POST   /api/v1/settlement-reviews/{id}/quota-sheet  # 上传限额领料单（403 约束发布）
POST   /api/v1/settlement-reviews/{id}/approve      # 发布图纸（检查限额领料单）

# 创效激励（✅ 已实现）
POST   /api/v1/incentive/proposals            # 发起创效提案
GET    /api/v1/incentive/proposals            # 提案列表
GET    /api/v1/incentive/proposals/{id}       # 提案详情
POST   /api/v1/incentive/proposals/{id}/calculate  # 商务测算（net_saving）
POST   /api/v1/incentive/proposals/{id}/sign       # 三方签字（顺序约束）
POST   /api/v1/incentive/proposals/{id}/distribute # 铁三角奖金分配
POST   /api/v1/incentive/proposals/{id}/reject     # 拒绝提案

# 规范知识库（❌ 待实现）
GET    /api/v1/regulations/search          # 规范搜索
POST   /api/v1/regulations/query           # 自然语言问答

# 项目看板（❌ 待实现）
GET    /api/v1/projects/{id}/drawings      # 项目图纸列表
GET    /api/v1/projects/{id}/dashboard     # 项目看板数据
```

---

## 七、工作流状态机

### 图纸深化状态流转

```
[草稿] → [AI审查中] → [AI审查完成]
                             ↓
                       [一审待处理] → [一审驳回（返修）]
                             ↓
                       [二审待处理] → [二审驳回（返修）]
                             ↓
                       [三审待处理] → [三审驳回（返修）]
                             ↓
                         [已发布]
```

### 二审强制约束（系统层面）

```python
def can_progress_to_settlement_review(drawing: Drawing) -> bool:
    economic_review = get_economic_review(drawing.id)
    return (
        economic_review is not None
        and economic_review.economist_signed_at is not None
    )
```

---

## 八、部署架构（生产环境）

```
Kubernetes Cluster
├── Namespace: cad-prod
│   ├── Deployment: cad-api (2 replicas)
│   ├── Deployment: cad-ai-review (3 replicas, GPU 可选)
│   ├── Deployment: cad-notification (1 replica)
│   ├── StatefulSet: postgresql (主从复制，含 AGE 扩展)
│   ├── StatefulSet: redis (哨兵模式，断路器共享状态)
│   ├── StatefulSet: minio (3节点)
│   └── StatefulSet: chroma (1节点，RAG 向量存储)
└── Ingress: Nginx + cert-manager (自动 TLS)
```

---

## 九、安全架构

- **认证**: JWT（Access Token 24h + Refresh Token 30d）
- **授权**: RBAC，权限粒度到项目级别
- **文件安全**: 上传前扫描（ClamAV）+ 存储加密（AES-256）+ 下载签名 URL（5分钟有效）
- **API 安全**: 限流（Nginx）+ SQL 注入防护（ORM 参数化）+ XSS 防护
- **审计日志**: 所有操作写入只追加（append-only）审计表，180 天保留
- **模型 API 密钥**: 只存环境变量名（`api_key_env`），运行时从 OS 环境变量读取明文，数据库不存储密钥
