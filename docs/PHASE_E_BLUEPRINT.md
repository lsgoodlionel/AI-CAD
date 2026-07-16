# Phase E 升级蓝图与实施计划

> 版本 V2.0 ｜ 2026-07-16 ｜ 状态:执行中
> 范围:8 项需求(3 新功能 + 1 缺陷修复 + 3 验证优化 + 1 全局原则)+ **架构主线升级:图纸信息档案层**
> 事实基线:基于 main(含 Phase D)代码摸底 + 上海大歌剧院 2309 图实测数据(2026-07-15 重建,version 21)
>
> **V2 变更**:确立「图纸信息档案层」为全平台数据主线(见 §0.5)——把原 E1/E2/E4 收编成
> 「抽取一次 · 单一真相源 · 人审在环 · 分层消费」一条主线;E0 已完成。

---

## 0. 需求总览与优先级

| # | 需求 | 类型 | 优先级 | 泳道 |
|---|------|------|--------|------|
| 4 | 模型路由管理-健康状态 500 | 🐛 缺陷 | **P0**(已精确定位根因) | E0 |
| 6 | OCR 功能验证与优化 | 🔍 验证 | **P0**(当前镜像 OCR 后端缺失,链路降级) | E0 |
| 1 | 新模块「工程信息」 | ✨ 新功能 | **P1**(是 3/6 的数据基座) | E1 |
| 2 | 图纸管理内每张图可预览 | ✨ 新功能 | **P1** | E1 |
| 3 | 工程模型「轴网」显示层 | ✨ 新功能 | **P1**(依赖 E1 抽取持久化) | E2 |
| 5 | 大歌剧院模型内容缺口(外墙/围护/地下室/外立面/幕墙/钢构) | 🏗️ 增强 | **P2**(工作量最大) | E3 |
| 7 | VLM 大模型读图测试与优化 | 🔍 验证 | **P2** | E4 |
| 8 | 通用型系统原则 | 📐 原则 | 贯穿全部 | — |

**依赖关系**:E0 独立可立即做 → E1 的「抽取结果持久化基座」是 E2(轴网)和 E4(VLM/OCR 结果落库)的前提 → E3 建模增强消费 E1/E2 的数据并反哺工程信息。

---

## 0.5 架构主线:图纸信息档案层(V2 核心升级)

> 本节是 V2 新增,确立整个 Phase E 的组织原则。原 8 项需求在此主线下各归其位。

### 0.5.1 问题:读图信息各自为战、重复劳动、不一致

V1 摸底暴露的根因不止「产物不落库」,更深的是**同一张图被多个消费方各自重复读**、且**人工修正无法回流**:

- 建模对剖面图临时跑 OCR 取标高(`model_builder._section_levels_ocr_fallback`),**算完即弃**;
- 工程信息(E1)对全部图跑 OCR 落 `drawing_extracted_info`;
- 两者**重复 OCR 同一张图**,且建模那遍结果不落库、与工程信息页不一致;
- 人工在 `model_story_manual`(标高)、`model_semantic_evidence`(语义)各改各的,**无统一入口、无法反哺建模标高/轴网**。

### 0.5.2 方案:每图一份「信息档案」,做全平台单一真相源

**核心原则:抽取一次 · 单一真相源 · 人审在环 · 分层消费。**

图纸导入即抽取(OCR + VLM 读图 + 矢量文字 + 图签),整理成**每图一份信息档案**,固定入库、可查看、可人工复核修正;此后**工程信息、建模、审图、算量全部从档案读**,不再各自重跑。

```
 导入图纸(单/批量/ZIP)
      │ 完成事件(pipeline_events)
      ▼
┌──────────────────────────────────────────────────────┐
│  档案层(Foundation)  ——  单一真相源                     │
│  drawing_extracted_info(auto) + 人审修正(verified)        │
│  抽取器:矢量文字 · OCR · VLM读图 · 文件名/图签             │
│  档案状态:pending → extracting → ready → reviewed          │
└──────────────────────────────────────────────────────┘
      │  稳定读取契约(生效值:verified > 高置信 auto)
      ├────────────┬─────────────┬────────────┐
      ▼            ▼             ▼            ▼
   工程信息页    建模            审图         算量
  (查看/复核)  (标高/轴网/     (依据档案     (量基于
                语义 从档案读)   信息核查)     确认的模型)
      │ 人审修正回写档案 │  档案 verified 变更 → pipeline_events → 增量重建
      └─────────────────┘
```

### 0.5.3 四个关键设计决策(已拍板)

**① 导入即抽取 → 异步 + 档案状态机**
OCR 单图 10–30s,批量导入不可同步阻塞上传。档案状态:
`pending(刚导入) → extracting(抽取中) → ready(自动完成) → reviewed(人工核过)`。
上传秒返回,档案后台填充(复用 E1 扇出任务模式)。

**② 人审修正层:auto 与 verified 分离,verified 永远赢且不被重抽覆盖**(正确性地基)
每条信息带 `source ∈ {auto, verified}` + `is_active`。人工修正写 verified 并置原 auto 行 `is_active=false`(**留痕:AI 原读成啥 / 人改成啥,供审图追责**)。**生效值 = verified 优先,其次按 confidence 排的 active auto**。下游只读生效值,永不见脏值。重抽只覆盖 auto 行,verified 不动。

**③ 单一真相源消费契约:下游通过稳定 API 读档案,不碰抽取器**
```
GET /drawings/{id}/archive               → 单图完整档案(生效值 + 状态)
GET /projects/{id}/archive/elevations    → 全项目生效标高(建模 section-z 读此,不再自跑 OCR)
GET /projects/{id}/archive/axes          → 全项目生效轴网(轴网层/配准读此)
```

**④ 档案粒度:per-drawing 档案是「聚合视图」,不新增重复存储**
`drawing_extracted_info` 行级表继续做底座;「一张图一份档案」= 按 drawing_id 聚合 + 合并 verified 后的呈现层。

### 0.5.4 事件驱动的顺序推进(综合建模→审图→算量)

人在工程信息页改一条标高 → 写 verified → 触发 `pipeline_events`(migration 027 已有)→ 建模增量重建 → 算量/审图跟随更新。这就是「综合建模、综合审图、算量一系列顺序推进」的落地机制。

### 0.5.5 现状对照:基座已有 ~60%,散着待收敛

| 能力 | 现状 | 缺口 |
|------|------|------|
| 每图信息档案表 | ✅ `drawing_extracted_info`(E1) | per-item 行,缺人审层 + 档案状态 + 聚合视图 |
| 导入即触发 | ⚠️ 手动 `POST /info/extract` | 挂到上传/批量导入完成事件 |
| 人工复核修正 | ⚠️ 散在 `model_story_manual`/`model_semantic_evidence`/标注队列 | 收敛到档案层统一入口 |
| 事件驱动下游重建 | ✅ `pipeline_events`(027) | 接「档案 verified 变更 → 重建」 |
| 建模消费档案 | ❌ 建模自跑 OCR | **E2-consume 核心改造** |
| VLM 读图入档案 | ⚠️ 仅喂 section-z | schema 已留 `extractor='vlm'`,接上落档 |

---

## 1. 现状事实基线(摸底结论)

### 1.1 大歌剧院模型现状(2026-07-15 重建后)

```
楼层 13 / 单体 3 / 总图纸 2309(mep 1289 / architecture 500 / structure 403 / general 117)
构件:columns 3089 / walls 697 / beams 576 / slabs 16(严重欠识别) / pipes 5791 / equipment 1813
未分层图纸 1061 张(46%)
```

### 1.2 关键事实(决定方案走向)

1. **抽取器已齐但产物不落库**:OCR 全文 token(`core/model3d/ocr`)、DXF TEXT/MTEXT(`geometry_extractor.texts`)、轴网(`grid_anchor_extractor.GridSystem`)、VLM 读图(`vlm_read.VlmReadResult`)全部**仅内存中转**,派生结论散落 scene JSONB。→ 工程信息模块必须先建持久化层。
2. **现成的溯源范式**:`model_semantic_evidence` 表(migration 016)= drawing_id FK + source_value + extractor + confidence + location_json,「每条信息链接回源图纸」直接照此建模。
3. **「章」在数据模型中不存在**:图纸组织维度 = project_id + discipline + 版次 + 审查批次(ReviewBatch)。「每一章」应落地为**套图批次详情/图纸列表的每行可预览**。
4. **预览现状**:`PdfViewer.tsx` = 纯 iframe(仅 PDF);presigned URL 端点 `GET /drawings/{id}/download-url` 已有;DXF 无预览,但 `project_models.assets` 里已有模型构建时渲染的 `image_key`(PNG)可复用。
5. **轴网**:识别已有(GridSystem{label,coord}),但 scene JSON 无 axes 字段,前端 `elementFilter + applyVisibility` 显隐机制现成,补数据+补渲染即可。
6. **健康状态 500 根因**(已复现):
   - `GET /admin/llm/logs/daily` → `call_logs.py:61` SQL `($1 || ' days')::interval` 传 int → asyncpg `TypeError: expected str, got int`
   - `GET /admin/llm/logs/circuit-breakers` → `call_logs.py:112` redis 客户端 `decode_responses=True` 返回 str,又调 `key.decode()` → `AttributeError`
7. **OCR**:当前镜像**未装** rapidocr/paddleocr(requirements-ocr.txt 是独立 extra),链路运行在 backend=none 优雅降级。aarch64 稳定路径 = `rapidocr-onnxruntime==1.4.4` 一行。
8. **VLM 配置**:`drawing_semantic_vlm` primary=qwen-vl-max(DashScope,连通 ✗)/ fallback_1=qwen2.5vl:7b(Ollama 本地,连通 ✓)。
9. **建模缺口根因**(第 5 项):
   - 分类法锚定 9 类(column/beam/slab/wall/door/window/pipe/equipment/axis),**无幕墙/钢构独立类**;
   - scene 只落 6 类几何(door/window/axis 识别了但不进场景);
   - **立面图完全不进构件提取管线**(`pick_element_drawings` 只有 structure/beam/mep 三桶)→ 幕墙/外立面零贡献;
   - 钢结构在两个图层 YAML 中**零覆盖**;
   - 墙识别对非标图层间距上限 0.4m,外墙/厚墙被结构性丢弃(仅当两线均命中墙图层才放宽到 1.0m);
   - 板 slabs=16 严重欠识别(文档 P1-1 已承认);
   - 46% 图纸未分层 → 大量构件源头就丢了。

---

## 2. 泳道 E0:缺陷修复 + OCR 启用(P0,~1 天)

### E0-1 健康状态 500 修复

**文件**:`apps/api/routers/admin/call_logs.py`

- `daily_cost`(L61):`($1 || ' days')::interval` → `make_interval(days => $1)`(参数保持 int,类型安全);
- `cb_status`(L112):`key.decode()` → `key if isinstance(key, str) else key.decode()`(兼容 decode_responses 两种配置);顺带把循环内 `import json` 提到模块顶。

**测试**:`tests/test_admin_call_logs.py` 新增两用例(daily 带/不带 engine_name、cb_status 有/无 cb:* 键)。TDD:先写测试复现 500,再修。

**验收**:健康看板三卡片(连通性/断路器/7 日成本)全部渲染,无 500。

### E0-2 OCR 后端入镜像

**文件**:`apps/api/Dockerfile`(或独立 `Dockerfile.ocr` 层)

- aarch64 开发环境:`pip install rapidocr-onnxruntime==1.4.4`(轻依赖,onnxruntime 已在主依赖树);
- x86_64 生产:安装完整 `requirements-ocr.txt`(paddle 优先,rapid 回退——service.py 已内置有序回退);
- wheelhouse 离线方案沿用本次重建经验(host 预下 aarch64 wheel → `--find-links` 离线装)。

**验收**:容器内 `run_ocr` backend≠none;`scripts/model3d/ocr_drawing.py` 对歌剧院剖面图实测复现「13 标高候选,置信 0.96~1.00」基线(docs/MODEL_OCR.md 已记录)。

---

## 3. 泳道 E1:工程信息模块 + 图纸预览(P1,~5 天)

### E1-1 抽取结果持久化基座(核心,先行)

**新表** `migrations/029_drawing_extracted_info.sql`(照 `model_semantic_evidence` 范式):

```sql
CREATE TABLE IF NOT EXISTS drawing_extracted_info (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  drawing_id UUID NOT NULL REFERENCES drawings(id) ON DELETE CASCADE,
  category VARCHAR(40) NOT NULL,     -- design_note|general_note|annotation|dimension|elevation|axis|room_name|title_block|component_section|space_label|other
  content TEXT NOT NULL,             -- 原文
  value_json JSONB,                  -- 解析值:{elevation_m}|{axis:{label,coord,dir}}|{dim_mm}|{title_block:{...}}
  location_json JSONB,               -- bbox/坐标(页面点或图纸单位)+页码
  extractor VARCHAR(40) NOT NULL,    -- ocr|dxf_text|vlm|grid_anchor|section_level|filename|title_block
  confidence NUMERIC(4,3),
  extraction_version INT NOT NULL DEFAULT 1,  -- 重跑覆盖用
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_dei_project_cat ON drawing_extracted_info(project_id, category);
CREATE INDEX IF NOT EXISTS idx_dei_drawing ON drawing_extracted_info(drawing_id);
```

**写入服务** `services/drawing_info_extractor.py`(新):
- 编排既有抽取器(全部已有 `to_dict()`,零重写):`geometry_extractor.texts` → dxf_text;`run_ocr` tokens(带 kind 分类)→ ocr;`grid_anchor_extractor` → axis;`section_level_extractor` → elevation;`vlm_read` → vlm 候选;`drawing_filename_parser`/`vlm_semantics.title_block_fields` → title_block;
- **两个触发点**:① 模型构建管线内(`model_builder` 已逐图跑抽取器,顺手双写,零额外解析开销);② 独立 Celery 任务 `tasks/drawing_info_extract.py`(单图/全项目重跑,extraction_version+1 幂等覆盖)。

**设计说明/文字说明的识别**:OCR `classify.py` 现有 kind 缺 `note` 的强规则——补「设计说明/说明/注:/技术要求」标题锚定 + 说明页判定(文字密度高、几何稀疏 → `drawing_view_classifier` 补 `note_page` 信号),整页文本按段落入库 category=design_note。

### E1-2 工程信息 API

**新路由** `routers/project_info.py`:
- `GET /api/v1/projects/{id}/info/summary` — 按 category 计数 + 抽取覆盖率(已抽取图纸数/总数);
- `GET /api/v1/projects/{id}/info/items?category=&discipline=&q=&page=` — 分页明细,联表 drawings 带出 drawing_no/title/discipline(信息→源图纸链接的数据来源);
- `GET /api/v1/projects/{id}/info/axes` — 轴网专用聚合(轴号/方向/坐标/来源图),供第 3 项工程模型消费;
- `POST /api/v1/projects/{id}/info/extract` — 触发全项目重抽(入队 Celery,复用 progress 上报范式)。
- 统一信封格式;JWT + 项目权限校验。

### E1-3 工程信息前端页

**路由**:`config/routes.ts` 顶层新增 `{ name:'工程信息', path:'/project-info', component:'./project/Info' }`(挂在「图纸管理」与「工程模型」之间);项目级深链 `/projects/:id/info` hideInMenu。

**页面** `pages/project/Info/index.tsx`(照 ProjectModel 的 ProjectPicker → Workspace 模式):
- 左侧 category 导航(设计说明/文字说明/标注/标高/轴线/房间/图签…,带计数);
- 主区 ProTable:内容/解析值/置信度/来源图纸(**点击 = 打开该图预览**,复用 E1-4 组件)/抽取器;支持按专业筛选、全文搜索;
- 顶部:抽取覆盖率进度条 + 「重新抽取」按钮;
- **每行「来源」列强制非空**——链接行为:抽屉内嵌预览 + 「跳转图纸详情」。

**服务** `services/projectInfo.ts`(照 modelManagement.ts 极简 CRUD 风格)。

### E1-4 图纸预览全覆盖(第 2 项)

**统一预览组件** `components/DrawingPreviewModal.tsx`(新,全站复用):
- 输入 drawing_id → 调 `getDownloadUrl` → 按扩展名分流:PDF → 现有 iframe PdfViewer;**DXF/DWG → 后端预览图**;图片直显;
- **DXF 预览后端**:新端点 `GET /drawings/{id}/preview-image` — 优先复用 `project_models.assets[drawing_id].image_key`(模型构建已渲染过的 PNG);miss 则按需渲染(ezdxf → matplotlib/PyMuPDF 光栅化,写回 MinIO 缓存 key `previews/{drawing_id}.png`);
- 失败兜底:提示「暂不支持在线预览」+ 下载按钮。

**接入点**(三处):
1. `DrawingList/index.tsx` 操作列加「预览」(不离开列表);
2. `ReviewBatch/Detail.tsx` items 表每行加「预览」(覆盖「每一章上传的图纸」诉求——套图分章即批次/专业分组);
3. 工程信息页来源列(E1-3)。

**验收**:歌剧院任意 PDF/DXF 图纸,三个入口 3 秒内出预览;无 image_key 的 DXF 首次按需渲染后二次秒开。

---

## 4. 泳道 E2:工程模型「轴网」显示层(P1,~2 天,依赖 E1-1)

### E2-1 后端:scene 携带轴网

**文件**:`services/model_builder.py`、`services/model_elements.py`
- 构建时把每层/每单体的 `GridSystem`(现已算出用于配准,算完即弃)写入 scene:
```json
floor.axes = { "x": [{"label":"1","coord":0.0},...], "y": [{"label":"A","coord":0.0},...],
               "extent": {...}, "confidence": 0.87, "source_drawing_id": "..." }
```
- 同步双写 `drawing_extracted_info`(category=axis,E1-1 已建);
- schema_version 不变(增量字段,向后兼容,前端判空)。

### E2-2 前端:轴网渲染 + 独立显隐

**文件**:`pages/model/ProjectModel/elementsBuilder.ts`、`modes/elementFilterOptions.ts`、`services/projectModel.ts`
- `elementsBuilder` 新增 `buildFloorAxes()`:轴线 = `LineSegments`(细虚线,跨该层平面范围);轴号 = 端部圆圈 + `Sprite` 文字标签(CanvasTexture,按需生成,注意 InstancedMesh 内存经验);`userData.elementType='axes'`;
- `elementFilterOptions(scene)` 增「轴网」选项(scene 有 axes 数据才出现);**`ModelViewer.applyVisibility` 通用机制无需改动**——checkbox 勾选/取消即整层轴网显隐;
- `SceneFloorElements` 类型补 `axes?` 字段。

**验收**:歌剧院模型浏览模式勾选「轴网」→ 各层显示轴线+轴号(位置与图纸一致,抽查 3 层×5 轴);取消勾选即隐藏;不与楼层隔离/专业筛选冲突。

---

## 5. 泳道 E3:建模内容覆盖增强(P2,~10 天,第 5 项)

> 原则(第 8 项):所有增强走**配置化图层映射 + 通用几何规则**,严禁歌剧院专有硬编码;歌剧院只作验证集。

### E3-0 缺口审计(先测后改,~1.5 天)

- 脚本 `scripts/model3d/audit_element_coverage.py`(新):对歌剧院全部 DXF 统计**实际图层名清单**×现有 `layer_conventions.yaml`/`layer_class_map.yaml` 命中率,输出未命中 TOP 图层(按实体数排序);
- 抽样 20 张关键图(外墙平面/立面/幕墙详图/地下室结构/钢构布置)人工核对识别结果 → 形成量化缺口清单(哪类丢在图层未命中、哪类丢在规则阈值、哪类丢在图纸根本没进管线);
- 46% 未分层图纸按 `drawing_view_classifier` 证据分桶,确认可挽回比例。

### E3-1 分类法与图层配置扩展(~2 天)

新增 2 个顶层类别:`curtain_wall`(幕墙)、`steel_member`(钢构件)。协同改动(摸底已列全):
1. `core/model3d/dataset/auto_label.py:46` VALID_CATEGORIES;
2. `core/model3d/layer_conventions.py` `_KIND_ORDER` + `data/layer_conventions.yaml`(幕墙:MQ/幕墙/CURTAIN/GLAZ;钢构:GG/钢/STEEL/S-STL/型钢/桁架/网架/劲性——按 E3-0 审计的真实图层名补,写进通用 YAML);
3. `data/model3d/layer_class_map.yaml` 同步;
4. `core/model3d/element_recognizer.py`:`_find_curtain_wall`(图层强命中的线链→立面板带)、`_find_steel_members`(图层命中的线/多段线→型钢中线,截面查 `model_component_sections`);
5. `core/model3d/types.py::FloorElements` + `services/model_elements.py`(EMPTY_ELEMENTS/_KIND_TO_CATEGORY/tasks 路由);
6. 下游:`model_qto*.py`(新类别不计混凝土量,幕墙计面积/钢构计长度)、`model_ifc_builder.py`(IfcCurtainWall/IfcMember)、前端 `elementFilterOptions` + `elementsBuilder` 渲染(幕墙半透明蓝、钢构深灰)。

### E3-2 外墙/围护闭合(~2 天)

- `_find_parallel_pairs` 增强:图层命中「外墙/围护/DWQ/挡土/人防」时单线也可成墙(沿线偏置默认厚度,厚度查 component_sections);
- 新增「外轮廓闭合」步骤:每层取建筑平面最大闭合环(已有 `_find_slabs` 的最大环逻辑可复用)→ 环上未被已识别墙覆盖的段落 → 补 `wall(kind=exterior)`,置信度降档并打 provenance(可在审校队列人工确认——复用 C-15 语义审校闭环);
- 地下室:E3-0 审计确认 B1/B2 图纸归层情况;`floor_parser` 负层解析已有,重点修「地下室图纸未分层/未进结构桶」——`_STRUCTURE_TITLE_RE` 与 `drawing_model_annotations` 人工标注通道兜底。

### E3-3 立面图进管线(~2 天)

- `pick_element_drawings` 新增 **elevation 桶**(view_type=elevation 或图名命中「立面」);
- 新服务 `services/model_facade.py`:消费 `elevation_opening_extractor`(现只服务 z 恢复)+ 幕墙图层线材 → 产外立面板带(按轴跨分段、洞口开窗)挂到对应朝向的外墙面;
- 立面信息(洞口/标高/幕墙分格)同步双写 `drawing_extracted_info`。

### E3-4 板恢复 + 降噪(~1.5 天)

- slabs=16 修复:板识别优先级重排(图层命中多边形 → 楼面结构平面的板边界线闭合 → 最大环兜底),对「结构平面图」强制产板;
- 管线降噪(P1-4):引线/标注线过滤(短线段+文字邻接判定)。

### E3-5 验证闭环(~1 天)

- 重建歌剧院模型,对照 E3-0 缺口清单逐项复核;
- 更新 `docs/MODEL_EVAL_SGOH.md`(v7)与两本手册能力边界章节(「边开发边更新」约定);
- 明确仍不做的:自由曲面外壳/异形屋面(方法论边界,文档如实声明)。

**验收指标**(相对基线):slabs 16 → ≥300;walls 697 → ≥1500(含外墙闭合);新增 curtain_wall/steel_member 非零且抽查 10 处与图纸一致;未分层率 46% → ≤25%;pipes 误识别抽查下降 ≥50%。

---

## 6. 泳道 E4:OCR/VLM 测试优化闭环(P2,~4 天,第 6+7 项)

### E4-1 OCR 评测与调优(第 6 项,依赖 E0-2)

- 用 `scripts/model3d/ocr_eval.py`(已有)+ 歌剧院抽样 50 图(每专业按比例)建金标准小集(标高/轴号/房间名人工核对);
- 指标:token 召回/精度 by kind;分块参数(tile/overlap/dpi)网格微调;
- 把 OCR 三馈线(`elevation_candidates/axis_anchors/space_labels`)在 model_builder 的消费效果量化(标高源命中率提升多少)——Phase D 已接 section-z,补配准/语义两处 wiring;
- OCR 结果双写 `drawing_extracted_info`(E1-1)→ 工程信息页直接可见 = 人工验证界面。

### E4-2 VLM 读图测试(第 7 项)

- **先修连通**:DashScope(qwen-vl-max)health=✗ → 核对 API key 环境变量/额度;Ollama 本地(qwen2.5vl:7b)health=✓ 可立即测;
- 批测脚本:`scripts/model3d/vlm_read_drawing.py`(已有)扩 batch 模式,对歌剧院抽样 100 图跑判专业/读标高/识构件;
- 金标准:专业 = drawings.discipline(已知),标高 = E4-1 金标集交叉;
- 指标:判专业准确率(目标 ≥95%)、标高候选命中率、平均延迟/成本(经 ModelRouter 日志,健康看板 E0-1 修复后可视化);
- 调优:prompt 版本迭代(`prompt_templates` 表 + engine_configs 热切换,不改代码);本地/远程模型对比报告 → 确定 primary/fallback 推荐配置;
- VLM 候选双写 `drawing_extracted_info`(extractor=vlm)。

**验收**:产出 `docs/PHASE_E_VLM_EVAL.md`(两模型×三任务指标表 + 推荐路由配置);判专业 ≥95%;VLM 标高兜底在矢量/OCR 不足图上有效(维持「绝不虚高」原则)。

---

## 7. 第 8 项:通用性原则(贯穿约束)

1. **零项目硬编码**:图层名/关键词/阈值一律进 `data/*.yaml` 配置(E3-1 的新增图层映射写通用词表,歌剧院特有命名以别名形式进配置而非代码);
2. **测试项目隔离**:歌剧院/E2E 项目 id 不得出现在任何业务代码;评测脚本以参数传入 project_id;
3. **优雅降级**:所有新能力(轴网/幕墙/钢构/OCR/VLM)缺数据时静默降级不报错,能力边界写入手册;
4. **回归防线**:E2E 合成套图(`tests/e2e/test_phase_b_demo.py` 范式)为准入,歌剧院实测为验证——新构件类别需先在合成图集绿灯;
5. **文档同步**:每泳道收口须更新 `MODEL_MANUAL_USER/ADMIN.md` + 版本历史(既有约定)。

---

## 8. 实施排期与里程碑(V2 重排:档案层为主线)

| 阶段 | 泳道 | 内容 | 预估 | 状态 |
|------|------|------|------|------|
| E0 | 缺陷+OCR | 健康看板 500 修复 + OCR 入镜像(RapidOCR) | 1d | ✅ 完成 |
| E1 | 档案存储底座 | `drawing_extracted_info` + 抽取编排 + 工程信息 API/页 + 全站预览 | 5d | ✅ 完成 |
| **E1.5** | **档案层升级(V2 新增)** | ①人审 verified 层(migration 030)②导入即触发抽取 ③档案读取契约 API(archive/elevations/axes)④工程信息页人审修正 UI | 2.5d | ⏳ 进行中 |
| **E2-consume** | **建模消费档案** | 建模 section-z 标高/轴网改从档案读(去重,不再自跑 OCR);轴网 3D 显隐(前端已完成) | 2d | ⏳ 后端待改 |
| **E-pipeline** | **事件驱动** | 档案 verified 变更 → `pipeline_events` → 建模增量重建 → 算量/审图跟随 | 1d | 待做 |
| E3 | 建模覆盖增强 | 审计→分类法(幕墙/钢构)→外墙闭合→立面进管线→板恢复→验证 | 10d | 待做 |
| E4 | OCR/VLM 评测 | OCR 调优 + VLM 本地/远程批测(VLM 作为档案的一个抽取器落档) | 4d | 待做 |
| 收口 | 全链路回归 | 歌剧院档案→建模→审图→算量全链路 + 手册/评测文档 + 验收 Demo | 1d | 待做 |

**里程碑**:
- **M-E1**(已达成):工程信息模块上线,任意图纸信息可溯源可预览;轴网前端可显隐。
- **M-E-archive**(E1.5+E2-consume 完成):图纸导入即建档案 → 人工可复核修正 → 建模/工程信息/审图/算量共用同一档案单一真相源;OCR 不再重复劳动;人审标高反哺建模。
- **M-E2**(E3/E4 完成):歌剧院模型覆盖指标达标(§5 验收);OCR/VLM 评测报告落档;健康看板零 500。

**测试要求**(全局约定):TDD,新增代码 80%+ 覆盖;migration 幂等(IF NOT EXISTS);三审状态机等既有边界不动。

---

## 8.5 档案层实施细则(E1.5 / E2-consume,V2 新增)

### E1.5-1 人审 verified 层(migration 030)

在 `drawing_extracted_info` 增列(幂等 ALTER):
```sql
ALTER TABLE drawing_extracted_info
  ADD COLUMN IF NOT EXISTS source_kind VARCHAR(10) NOT NULL DEFAULT 'auto',  -- auto | verified
  ADD COLUMN IF NOT EXISTS is_active   BOOLEAN     NOT NULL DEFAULT true,     -- 被 verified 推翻的 auto 行置 false 留痕
  ADD COLUMN IF NOT EXISTS reviewed_by UUID,
  ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS supersedes  UUID;   -- verified 行指向它修正的 auto 行(可回溯 AI 原值)
```
新增 `drawing_archive_status` 表(每图档案状态机):
```sql
CREATE TABLE IF NOT EXISTS drawing_archive_status (
  drawing_id UUID PRIMARY KEY REFERENCES drawings(id) ON DELETE CASCADE,
  project_id UUID NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending|extracting|ready|reviewed
  extractors_done JSONB, extraction_version INT, updated_at TIMESTAMPTZ DEFAULT now()
);
```
**生效值规则**(所有读取契约统一):同 (drawing_id, category, 归一化 key) 取 `source_kind='verified'` 优先,否则 `is_active AND source_kind='auto'` 里 confidence 最高。重抽只 upsert auto 行(先删旧 auto、保留 verified)。

### E1.5-2 导入即触发抽取

`routers/drawings.py` 上传/批量/ZIP 导入成功后,对每条新 drawing 发 `extract_single_drawing_info.delay(id)`(已有任务);置 `drawing_archive_status=pending→extracting`。**已导入的存量图**用一次性 `extract_project_drawing_info` 扇出回填(歌剧院 2309 图)。

### E1.5-3 档案读取契约 API

`routers/drawing_archive.py`(新):
- `GET /drawings/{id}/archive` — 单图档案(生效值 by category + 状态 + 有无人审)
- `GET /projects/{id}/archive/elevations` — 生效标高列表(建模消费)
- `GET /projects/{id}/archive/axes` — 生效轴网(建模/配准消费)
- `POST /drawings/{id}/archive/verify` — 人审修正:写 verified 行 + 置原 auto `is_active=false` + audit + 发 `pipeline_events`(档案变更)

### E1.5-4 工程信息页人审修正 UI

工程信息明细表每行加「修正」入口(改值/确认/否定),写 `/archive/verify`;verified 行高亮「已人工核对」。

### E2-consume 建模改读档案

- `model_builder._recover_section_z`:从 `GET archive/elevations`(生效标高)读,**删除 `_section_levels_ocr_fallback` 自跑 OCR 分支**(档案已含 OCR 结果);无档案时优雅降级回原矢量路径(向后兼容未建档项目)。
- `cross_view_registration`:轴号锚点从 `archive/axes` 读。
- 效果:建模不再为 OCR 卡 1–2 小时(查库 ms 级);建模标高 = 工程信息页所见;人审修正即时反哺。

---

## 9. 风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| OCR 全项目 2309 图重抽耗时长 | E1 数据完整性延迟 | 双触发设计:构建管线顺手写 + 增量任务分批;工程信息页显示覆盖率而非等全量 |
| DXF 按需渲染预览慢/占内存 | E1-4 体验 | 优先命中 model assets 缓存;渲染限并发+超时,写 MinIO 缓存 |
| 外墙闭合误补(把内廊当外轮廓) | E3-2 假构件 | 置信降档 + provenance 标记 + 审校队列人工确认(复用 C-15) |
| 钢构/幕墙图层命名千差万别 | E3-1 泛化差 | E3-0 审计驱动词表;通用词 + 项目别名分层配置;审校回流数据飞轮(C-16 范式) |
| DashScope key/额度不可用 | E4-2 只剩本地模型 | Ollama qwen2.5vl:7b 已连通可先行;报告中分别给本地/远程结论 |
| scene 体积膨胀(轴网+新构件) | 前端内存回退 | 沿用 115MB 优化经验:轴网 LineSegments 合批、标签按需生成、折叠卸载 |
