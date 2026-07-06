# 工程 3D 模型基座 — 升级蓝图（Phase 6）

> 版本：V1.0 | 日期：2026-07-03 | 状态：待开发（依赖 Phase 5 批量审图完成）
> 目标：以导入的 CAD/PDF/IFC 图纸为数据源，生成项目级 3D 可视化模型基座；
> 平台所有成果（图纸、AI 审图问题、套图跨图发现、三审状态）挂接到模型上呈现，
> 使模型成为全平台成果展示的主通道之一。

## 1. 产品定位与务实边界

**不做**：从 2D 图纸自动重建真实 BIM 构件几何（研究级难题，无法保证工程可靠性）。

**做**：「工程数字模型基座」三层合成：
1. **楼层堆叠骨架**（核心，全数据源可用）：从图纸标题/图号/审图定位信息提取楼层 →
   楼层板片按标高堆叠成建筑体量；每张图纸渲染为位图贴在对应楼层面板上，点击可查看。
2. **真实几何层**（IFC 可用时）：IfcOpenShell 解析 IFC → glTF，叠加到场景（可选依赖，缺失优雅降级）。
3. **成果标记层**（平台数据挂接）：AI 审图问题（含会审 V4 location/严重度）→ 楼层上的 3D 标记；
   套图跨图发现（接口缺图/问题聚类）→ 跨层高亮；图纸三审状态 → 面板着色。

## 2. 架构

```
图纸(PDF/DXF/IFC) + ai_review_issues + review_batches
        │
tasks/model_build.py  build_project_model(project_id)   ← 手动触发 / 套图审查完成后自动触发
        │
services/model_builder.py  build_scene()
  ├─ floor_parser：楼层提取与排序（B2/B1/1F.../屋面/未分层）
  ├─ 贴图渲染：PDF 首页→PNG(PyMuPDF)；DXF→PNG(ezdxf.addons.drawing+matplotlib，失败降级线框占位)
  │   → MinIO projects/{pid}/model_assets/{drawing_id}.png
  ├─ IFC→glTF（IfcOpenShell，可选）→ MinIO .../model_assets/{drawing_id}.glb
  └─ 标记合成：issues.location_json(levels/axes) → floor_key + 稳定网格坐标
        │
project_models.scene (JSONB) ←—— GET /projects/{id}/model ——→ 前端 three.js Viewer
```

## 3. 数据库（migration 013_project_models.sql — 模块 D）

```sql
CREATE TABLE IF NOT EXISTS project_models (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id  UUID NOT NULL UNIQUE REFERENCES projects(id),
  status      VARCHAR(16) NOT NULL DEFAULT 'pending', -- pending|building|ready|failed
  version     INT NOT NULL DEFAULT 0,                 -- 每次重建 +1
  scene       JSONB,
  assets      JSONB,      -- {drawing_id:{image_key,width,height,parser}}
  error       TEXT,
  built_at    TIMESTAMPTZ,
  created_at  TIMESTAMPTZ DEFAULT now(),
  updated_at  TIMESTAMPTZ DEFAULT now()
);
```

## 4. scene JSON 契约（前后端核心契约，中文 key 与审图输出保持同风格的地方用中文，结构 key 用英文）

```json
{
  "project": {"id": "", "name": ""},
  "floors": [
    {"key": "B2", "label": "地下二层", "elevation": -2, "order": -2,
     "drawings": [
       {"drawing_id": "", "drawing_no": "", "title": "", "discipline": "structure",
        "status": "ai_done", "current_stage": "technical_review",
        "image_key": "projects/../model_assets/xx.png",  // 无贴图时 ""
        "issue_count": 3, "critical_count": 1}
     ]}
  ],
  "markers": [
    {"id": "issue:<issue_id>", "type": "issue",          // issue | cross
     "severity": "critical|major|minor|info",
     "floor_key": "B2", "x": 0.35, "y": 0.62,            // 0~1 归一化楼层平面坐标
     "title": "问题描述截断 80 字", "discipline_code": "JG",
     "ref": {"drawing_id": "", "issue_id": ""}}
  ],
  "cross_links": [                                        // 套图跨图发现（最近一次 batch）
    {"kind": "接口缺图|问题聚类|版本冲突|重复图号", "label": "", "floor_keys": [], "drawing_ids": []}
  ],
  "ifc_models": [{"drawing_id": "", "gltf_key": ""}],
  "stats": {"total_drawings": 0, "total_issues": 0,
            "by_severity": {}, "by_discipline": {}, "floors": 0},
  "generated_at": "ISO8601"
}
```

坐标规则：无真实空间坐标 → 稳定伪随机布点：`hash(axes文本 or issue_id) → (x,y)∈[0.1,0.9]²`，
同轴线问题共享坐标簇（axes 文本相同 → 同点位偏移 0.02 步进），保证重建后位置稳定。

## 5. 楼层解析（模块 D，`services/floor_parser.py`）

```python
def parse_floor(text: str) -> tuple[str, str, int] | None:
    """从图名/图号/location.levels 文本提取楼层。
    返回 (key, label, order)：B2→('B2','地下二层',-2)；3F/三层/3层→('F3','3层',3)；
    屋面→('RF','屋面',99)；基础→('FD','基础层',-98)。匹配不到→None。
    """
def floor_of_drawing(drawing: dict, issue_levels: list[str]) -> tuple[str, str, int]:
    """优先图纸 title/drawing_no，再取该图 issues 的 location levels 众数；都无 → ('UNZONED','未分层',0)。"""
```

## 6. 后端 API（模块 D，新 `routers/project_models.py`，main.py 注册 prefix `/api/v1/projects`）

- `POST /api/v1/projects/{project_id}/model/rebuild`
  校验项目存在；UPSERT project_models(status='building')；`build_project_model.delay(project_id)`；
  审计日志 action=`rebuild_project_model`；返回 `{project_id, status:'building', version}`。
- `GET /api/v1/projects/{project_id}/model`
  返回 `{status, version, built_at, error, scene}`；无记录 → 404 `MODEL_NOT_BUILT`。
- `GET /api/v1/projects/{project_id}/model/asset-url?key=...`
  校验 key 以 `projects/{project_id}/model_assets/` 开头（防越权），
  返回 `{url}`（core/storage.py presigned 5min，与图纸下载一致）。

**自动触发钩子**（模块 D 改 `tasks/batch_review.py`——Phase 5 Agent B 的产物，D 启动时已就绪）：
`finalize_batch_review` 完成汇总后，若该项目已存在 project_models 记录 → `build_project_model.delay(project_id)`
（函数内局部 import 防循环依赖；失败仅告警不影响 batch 状态）。

## 7. 模型构建（模块 D，`services/model_builder.py` + `tasks/model_build.py`）

- `async def build_scene(db, project_id) -> tuple[dict, dict]`：纯函数（贴图渲染在线程池），
  聚合 drawings + ai_review_issues(最新报告) + 最近一次 review_batches.cross_findings 组装 scene/assets。
- 贴图：PDF 首页 fitz 渲染 dpi≈110、最长边 1600px PNG；DXF 用 ezdxf.addons.drawing matplotlib 后端；
  DWG 复用 Phase 5 `dwg_support.ensure_dxf`；任何渲染失败 → image_key=""（前端线框占位），绝不抛异常中断整体。
- IFC：`ifcopenshell` + `ifcopenshell.geom`（settings USE_WORLD_COORDS）导出 glb；
  import 失败/未安装 → 跳过并在 scene.stats 记 `ifc_skipped:true`。
- `tasks/model_build.py`：Celery `build_project_model(project_id)`（bind、max_retries=2）：
  status building → 成功 ready（version+1、scene/assets/built_at 更新）/ 失败 failed（error 截断 500）。

## 8. 前端（模块 E 查看器 + 模块 F 平台关联）

依赖：`three` + `@types/three`（已由主会话预装）。不引入 react-three-fiber，自写轻量封装。

### 模块 E — 模型查看器
- `services/projectModel.ts`：`getProjectModel(projectId)`、`rebuildProjectModel(projectId)`、
  `getModelAssetUrl(projectId, key)` + scene 全量 TS 类型（对齐第 4 节契约）。
- `pages/model/ProjectModel/ModelViewer.tsx`：three.js 封装组件（Props: scene, focusDrawingId?, filters）
  - 楼层 = 半透明 BoxGeometry 板片按 order 堆叠（层高固定 3 单位）；图纸 = 楼层上方 PlaneGeometry
    贴图（TextureLoader 加载 presigned url；无贴图画 EdgesGeometry 线框 + 图号 Sprite 文本）
  - 标记 = SphereGeometry，severity 颜色 critical=#f5222d major=#fa8c16 minor=#faad14 info=#8c8c8c
  - OrbitControls（three/examples/jsm）；Raycaster 点击：图纸面板 → onSelectDrawing(id)，标记 → onSelectMarker
  - 楼层隔离模式（点击楼层树条目 → 其余楼层透明度 0.06）
  - 资源释放：组件卸载 dispose geometry/material/renderer
- `pages/model/ProjectModel/index.tsx`（路由 /model/:projectId? ）
  - 无 projectId：项目卡片列表（复用 listDrawings 的项目下拉数据源或 dashboard 项目接口，二选一以现有 services 为准）
  - 有 projectId：左侧楼层树 + 过滤器（专业 checkbox、严重度 checkbox、标记类型开关）；
    中央 ModelViewer；右侧 Drawer：点图纸 → 图纸信息 + issue 列表 + 「进入图纸详情」；
    点标记 → 问题详情（standard_question/风险/处理建议摘要）+「查看图纸」
  - 顶部：状态 Badge + version + built_at + 「重建模型」按钮（rebuild 后轮询 5s 直至 ready/failed）+ stats 统计条
  - status=building 轮询；MODEL_NOT_BUILT(404) → 空态引导「立即生成模型」
- `config/routes.ts`：一级菜单「工程模型」`/model`（icon: 'build'）与 `/model/:projectId`（hideInMenu）。

### 模块 F — 平台关联（模型成为成果主通道）
- `pages/drawings/DrawingDetail/index.tsx`：头部操作区加「在工程模型中查看」按钮 →
  `/model/{project_id}?focus={drawing_id}`（ModelViewer 收到 focusDrawingId 后相机对准该图纸面板并高亮）。
- `pages/drawings/ReviewBatch/Detail.tsx`：完成态（done/partial_failed）显示「在模型中查看跨图发现」入口。
- `pages/dashboard/ProjectDashboard/index.tsx`：项目概览加「工程模型」入口卡片/按钮。
- `pages/drawings/DrawingList/index.tsx`：工具栏加「工程模型」按钮（需已选项目筛选）。

## 9. 测试要求（全量 pytest ≥80% 门槛不许跌破）

| 模块 | 测试 | 要点 |
|------|------|------|
| D | tests/test_floor_parser.py | B2/3F/三层/屋面/基础/无法识别 各分支 |
| D | tests/test_model_builder.py | FakeDB 喂 drawings+issues → scene 契约字段齐全；渲染失败降级 image_key=""；坐标稳定性（同输入同坐标） |
| D | tests/test_project_models_router.py | rebuild/get/asset-url 越权 key 403、404 MODEL_NOT_BUILT |
| E/F | tsc 过滤 | 相关文件 0 报错 |

## 10. 并行分工与文件所有权（第二波，Phase 5 合并后启动）

| Agent | 独占文件 |
|-------|---------|
| D 后端模型构建 | migrations/013、services/floor_parser.py、services/model_builder.py、tasks/model_build.py、routers/project_models.py、main.py（仅注册两行）、tasks/batch_review.py（仅加触发钩子）、tests D |
| E 前端查看器 | services/projectModel.ts、pages/model/ProjectModel/*、config/routes.ts |
| F 前端平台关联 | pages/drawings/DrawingDetail/index.tsx、pages/drawings/ReviewBatch/Detail.tsx、pages/dashboard/ProjectDashboard/index.tsx、pages/drawings/DrawingList/index.tsx |

公共约定同 Phase 5（JWT/审计/错误码/中文注释/文件 ≤800 行）。
