# 批量读图与整套工程审图 — 升级蓝图（Phase 5）

> 版本：V1.0 | 日期：2026-07-03 | 状态：并行开发中
> 目标：图纸批量读取（PDF/DWG/DXF）→ 单张/多张/整套工程审图任务 → 套图级跨图汇总报告

## 1. 现状与缺口

| 能力 | 现状 | 缺口 |
|------|------|------|
| PDF 读取 | ✅ PyMuPDF + PaddleOCR 兜底 | — |
| DXF 读取 | ✅ ezdxf | — |
| DWG 读取 | ❌ ezdxf 不支持二进制 DWG，解析必失败（静默降级） | ODA File Converter 转换链路 |
| 上传 | 单文件 `POST /drawings` | 批量多文件 + ZIP 整套导入 + 文件名智能解析 |
| 审图触发 | 单张（上传时自动 / retry） | 批量触发、整套触发、套图任务追踪 |
| 审图汇总 | 单张报告 | 套图级跨图分析（界面一致性/接口缺图/版本冲突/问题聚类） |

## 2. 架构总览

```
批量上传(多文件/ZIP) ─→ 文件名解析器 ─→ drawings 落库(N) ─→ MinIO
                                             │
POST /review-batches (单张/多张/整套) ─→ review_batches 落库
                                             │
                          对每张图触发 run_ai_review.delay（复用现有五引擎）
                                             │
                          finalize_batch_review（轮询型 Celery 任务，重试等待）
                                             │
                    全部子报告终态 → cross_drawing.analyze_batch 跨图分析
                                             │
                    batch.summary + cross_findings 落库 → 前端套图报告页
```

设计原则：**复用现有单张审图任务**（`run_ai_review` 幂等、有进度、有重试），批量层只做编排与汇总，不改动五引擎内部。

## 3. 数据库（migration 012_review_batches.sql — 模块 B）

```sql
CREATE TABLE IF NOT EXISTS review_batches (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id   UUID NOT NULL REFERENCES projects(id),
  scope        VARCHAR(16) NOT NULL DEFAULT 'multi',   -- single | multi | full_set
  drawing_ids  JSONB NOT NULL DEFAULT '[]',            -- [uuid,...]
  status       VARCHAR(16) NOT NULL DEFAULT 'pending', -- pending|processing|done|partial_failed|failed
  summary      JSONB,   -- {total,done,failed,issues_total,critical_total,by_severity:{},by_discipline:{}}
  cross_findings JSONB, -- cross_drawing.analyze_batch 输出
  created_by   UUID REFERENCES users(id),
  created_at   TIMESTAMPTZ DEFAULT now(),
  completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_review_batches_project ON review_batches(project_id);
```

## 4. API 契约（统一信封由全局中间件处理，路由返回裸 dict 与现有端点一致）

### 4.1 批量上传（模块 A，`routers/drawings.py`）

`POST /api/v1/drawings/batch`（multipart）
- 字段：`project_id`(Form)、`items_meta`(Form, JSON 字符串)、`files`(List[UploadFile])、`auto_review`(Form bool, 默认 true)
- `items_meta = [{filename, drawing_no, discipline, version?, title?, work_zone_id?}]`，按 `filename` 与 files 配对；缺失的条目用文件名解析器兜底
- 单文件 ≤200MB；扩展名白名单 pdf/dwg/dxf/ifc；逐文件独立成败（一个失败不阻断其余）
- 返回：`{created:[{drawing_id,drawing_no,filename}], failed:[{filename,error}], review_triggered:int}`
- 每张成功图纸复用现有单张上传逻辑：MinIO 上传 → drawings 落库 → 报告占位 → 审计日志 → `auto_review` 时 `run_ai_review.delay`

`POST /api/v1/drawings/import-zip`（multipart：`project_id`、`file`(zip ≤500MB)、`auto_review`）
- 解压（防 zip-slip：拒绝路径穿越条目；跳过隐藏文件/非白名单扩展名）
- 每个条目经文件名解析器生成元数据，其余同 `/batch`；返回结构相同 + `skipped:[filename]`

### 4.2 文件名解析器（模块 A，`services/drawing_filename_parser.py`）

```python
def parse_drawing_filename(filename: str) -> dict:
    """返回 {drawing_no, discipline, title, version}；解析不出的字段给安全默认值。
    规则（按序）：
    1. 专业前缀：结施/GS→structure 建施/JS→architecture 水施|电施|暖施|机施/SS|DS|NS→mep
       装施/ZS→decoration；无法判断→general
    2. 图号：r'[A-Za-z一-龥]{1,4}[-_ ]?\d{1,4}' 首个匹配；无→文件名主干
    3. 版本：r'[Vv]?([A-Z])(?:版|$)' 或 _A/_B 后缀；无→'A'
    4. title = 去除图号/版本后的剩余主干
    """
```

### 4.3 DWG 支持（模块 A，`core/ai_review/dwg_support.py` + `vision_engine.py` 接入）

```python
def ensure_dxf(data: bytes, file_ext: str) -> tuple[bytes, str, str | None]:
    """DWG → DXF。返回 (data, effective_ext, warning)。
    - file_ext != 'dwg' → 原样返回
    - 检测 DWG 魔数（b'AC10'开头）→ 尝试 ezdxf.addons.odafc（需 settings.oda_converter_path
      指向 ODA File Converter 可执行文件；经临时文件转换）
    - ODA 未配置/转换失败 → 返回原 data + warning 文本（vision_engine 转为 INFO issue，
      提示"DWG 需安装 ODA File Converter 或上传 DXF/PDF 版本"），不再盲目走 ezdxf.read 报错
    """
```
`core/config.py` 新增 `oda_converter_path: str = ""`（环境变量 `ODA_CONVERTER_PATH`）。
`vision_engine._sync_extract` DWG 分支：先 `ensure_dxf`，成功则按 DXF 解析，`metadata["parser"]="oda+ezdxf"`。

### 4.4 套图审查（模块 B，新 `routers/review_batches.py`，main.py 注册 prefix `/api/v1/review-batches`）

`POST /api/v1/review-batches`
- body：`{project_id, drawing_ids?: [uuid], scope?: 'single'|'multi'|'full_set'}`
- `drawing_ids` 缺省/空 → `full_set`：选该项目所有 `status IN ('draft','ai_done')` 的图纸（`ai_reviewing` 跳过）
- 校验：项目存在、图纸都属于该项目、非空集合（空 → 400 `NO_REVIEWABLE_DRAWINGS`）
- 行为：落库 review_batches(status='processing') → 每张图 `run_ai_review.delay`（并把 drawings.status 置 ai_reviewing、确保有 pending/processing 报告，与单张上传路径一致）→ `finalize_batch_review.delay(batch_id)`
- 返回：`{batch_id, scope, total, triggered}`
- 审计日志 action=`create_review_batch`

`GET /api/v1/review-batches?project_id=&limit=&offset=` → `{items:[batch 概要], total}`

`GET /api/v1/review-batches/{batch_id}` →
```json
{"batch": {...review_batches 行...},
 "items": [{"drawing_id","drawing_no","title","discipline","report_status","total_issues","critical_issues"}],
 "progress": {"total":N, "done":n1, "failed":n2, "processing":n3}}
```

### 4.5 汇总任务（模块 B，`tasks/batch_review.py`）

```python
@celery_app.task(bind=True, max_retries=180, default_retry_delay=10)
def finalize_batch_review(self, batch_id: str) -> dict:
    """轮询型汇总：查 batch 内所有图纸最新 ai_review_reports 状态；
    - 存在非终态（pending/processing）→ self.retry（10s 间隔，上限 ~30min）
    - 全部终态 → 聚合 summary + cross_drawing.analyze_batch → 更新 review_batches
      status: 全部 done→'done'；部分 failed→'partial_failed'；全部 failed→'failed'
    """
```

### 4.6 跨图分析（模块 B，`core/ai_review/cross_drawing.py`，纯 SQL+Python，无 LLM）

```python
async def analyze_batch(db, project_id: str, drawing_ids: list[str]) -> dict:
    """返回：
    {"重复图号": [{drawing_no, drawing_ids:[]}],
     "版本冲突": [{drawing_no, versions:[]}],          # 同图号多版本同时在审
     "接口缺图": [{missing_discipline, referenced_by:[{drawing_no, interface}]}],
       # review 引擎 issue.interface_related 中出现、但套图内无对应粗专业图纸
     "问题聚类": [{location_key, count, drawings:[], disciplines:[]}],
       # 按 location_json 的 楼层+轴线 归一化 key 聚合 ≥2 张图共有问题
     "高频对象聚合": [{name, count}],                   # review_method.优先对象 汇总
     "严重度分布": {critical,major,minor,info},
     "专业分布": {discipline: issue_count}}
    接口专业名→粗专业映射复用 services/reviewAudit 同款 19 专业中文名（结构/建筑/给排水/电气/暖通/消防…）。
    """
```

## 5. 前端（模块 C）

- `services/drawings.ts`：`batchUploadDrawings(fd)`、`importDrawingsZip(fd)`、`createReviewBatch(body)`、`listReviewBatches(params)`、`getReviewBatch(id)`
- `DrawingList/index.tsx`：
  - ProTable `rowSelection` + 工具栏「批量 AI 审图」按钮（调 createReviewBatch，drawing_ids=选中）+「整套审图」按钮（不传 drawing_ids，需先选项目筛选）
  - 上传 Modal 支持 `multiple`：文件列表表格（每行 文件名/图号/专业/版本 可编辑，前端用与后端同规则的简版文件名预解析预填），提交走 `/drawings/batch`
- 新页面 `pages/drawings/ReviewBatch/`：
  - `index.tsx` 套图任务列表（状态 Badge、进度 x/y、创建时间）
  - `Detail.tsx` 任务详情：进度条（轮询 GET /{id}，5s）、每张图状态表（链到图纸详情）、跨图发现（重复图号/版本冲突/接口缺图/问题聚类 卡片）、严重度与专业分布
- `config/routes.ts`：图纸管理菜单下新增 `/drawings/review-batches`（名称「套图审查」）与 `/drawings/review-batches/:id`（hideInMenu）

## 6. 测试要求（TDD，全量 pytest 覆盖率 ≥80% 门槛不许跌破）

| 模块 | 测试文件 | 要点 |
|------|---------|------|
| A | `tests/test_filename_parser.py` | 专业前缀/图号/版本/兜底各分支 |
| A | `tests/test_drawings_batch.py` | 批量上传成功/部分失败/超限/zip-slip 拒绝；mock MinIO(`routers.drawings.upload_file`) 与 `run_ai_review.delay` |
| A | `tests/test_dwg_support.py` | 非 dwg 透传/魔数检测/ODA 未配置降级 warning |
| B | `tests/test_review_batches.py` | 创建（显式列表/full_set/空集 400/跨项目图纸 400）、列表、详情进度聚合；FakeDB |
| B | `tests/test_cross_drawing.py` | 重复图号/版本冲突/接口缺图/问题聚类/优先对象聚合，FakeDB 喂数据 |
| B | `tests/test_batch_finalize.py` | 全 done→done；含 failed→partial_failed；未完成→retry |
| C | tsc | 本次改动文件 0 类型错误（全量 tsc 有既有 umi 环境报错，过滤本次文件） |

## 7. 并行开发分工与文件所有权（避免冲突的硬边界）

| Agent | 范围 | 独占文件 |
|-------|------|---------|
| A 后端-批量读图 | 批量上传/ZIP/文件名解析/DWG | `routers/drawings.py`、`services/drawing_filename_parser.py`、`core/ai_review/dwg_support.py`、`core/ai_review/vision_engine.py`、`core/config.py`、tests A |
| B 后端-套图审图 | batch 表/router/任务/跨图分析 | `migrations/012_review_batches.sql`、`routers/review_batches.py`、`tasks/batch_review.py`、`core/ai_review/cross_drawing.py`、`main.py`、tests B |
| C 前端 | 批量上传 UI/批量触发/套图页面 | `services/drawings.ts`、`pages/drawings/DrawingList/index.tsx`、`pages/drawings/ReviewBatch/*`、`config/routes.ts` |

公共约定：JWT `Depends(get_current_user)`、审计日志 `write_audit`、错误码大写蛇形、中文 docstring/注释风格与现有代码一致、文件 ≤800 行、函数 ≤50 行。
