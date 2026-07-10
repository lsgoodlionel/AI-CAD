# Phase A（展示级增强）任务分解

> 版本 V1.0 | 2026-07-10 | 来源：`docs/AI_READING_TO_3D_MODEL.md` 第四章 Phase A + 第二章四层架构
>
> 目标：把现有 2.5D 贴图挤出升级为**合规 IFC 数据底座** + **百万构件级 Web 性能** + **VLM 语义读表/判专业** + **图层约定强化构件识别**。低风险、确定性优先，1–2 个月。
>
> 本文只覆盖 **Phase A**。算量级（跨视图 z 恢复 / 构件拓扑 / QTO）见 Phase B，BIM 级（符号识别学习模型 / 自建数据集）见 Phase C，均由其它 agent 另行产出。依赖处以「见 Phase B/C」标注。

---

## 0. 现状基线（本分解据此制定，精确到代码位置）

| 资产 | 路径 | Phase A 触点 |
|---|---|---|
| 场景组装主流程 `build_scene` | `apps/api/services/model_builder.py`（906 行） | 已有 `_ifc_to_glb_sync`（IFC→glb 渲染）、`_build_assets`、`scene.ifc_models[]`；A 阶段在此挂 **程序化 IFC 建模** 分支 |
| 几何提取 | `apps/api/core/model3d/geometry_extractor.py` | **未采集图层名/块名**——A-14 补 |
| 构件识别（确定性） | `apps/api/core/model3d/element_recognizer.py` | `_find_columns`「柱必须 filled」漏检（第 274–289 行）；无图层约定——A-16 修 |
| 几何/构件数据结构 | `apps/api/core/model3d/types.py` | `DrawingGeometry` / `FloorElements`——A-15 扩展携带 layer |
| 构件层组装 | `apps/api/services/model_elements.py` | `build_floor_elements` 接线点 |
| 模型路由 | `apps/api/core/llm/router.py` + `routers/admin/engine_configs.py::ENGINE_NAMES`（13 引擎） | A-10 注册 VLM 引擎；A-09 补 providers 视觉消息 |
| LLM Provider | `apps/api/core/llm/providers/`（anthropic/openai_compat/ollama/custom_http） | 当前仅文本消息，A-09 加 image content |
| 构建任务 | `apps/api/tasks/model_build.py` → `build_project_model` | 无需改结构，A-03/A-13 顺延 |
| 模型持久化 | `project_models` 表（`migrations/013`~`016`，当前最高 016） | A-05 新增 `017` 迁移 |
| 前端三维 | `apps/web/src/pages/model/ProjectModel/`（`index.tsx`/`ModelViewer.tsx`/`sceneBuilder.ts`/`elementsBuilder.ts`，`three@0.185`） | A-06~A-08 引入 Fragments 加载器，three.js 挤出保留为 fallback |
| `model_ifc_builder.py` | `apps/api/services/model_ifc_builder.py` | **由并行「任务1」创建中**（IfcOpenShell 建模器）；Phase A 在其之上封装映射逻辑（A-02 前置依赖） |
| 已装依赖 | `requirements.txt`：`ifcopenshell==0.8.5`、`ezdxf==1.3.4`、`ultralytics==8.3.202`（`paddleocr` 注释未启用） | 后端 IFC 依赖已就绪；前端 That Open 依赖待加 |

---

## 1. 任务清单

> 字段：ID / 标题 / 描述 / 交付物 / 涉及文件 / 依赖 / 开源件(许可证) / 工作量(人天,S≤1 / M 2–3 / L 4–5) / 验收标准 / 风险。

### WS0 — 准备与依赖

#### A-01 · 依赖与特性开关准备
- **描述**：确认后端 IFC 相关依赖可用，加入前端 That Open 依赖，建立 Phase A 灰度特性开关（`MODEL_IFC_ENABLED`、`WEB_FRAGMENTS_ENABLED`、`VLM_SEMANTIC_ENABLED`），保证增量上线可回退。
- **交付物**：更新后的依赖清单；`core/config.py` 三个开关默认 `false`；前端 `.umirc.ts`/env 对应开关；一页《Phase A 环境说明》。
- **涉及/新增文件**：`apps/api/requirements.txt`（校验 `ifcopenshell`/`ezdxf`）、`apps/api/core/config.py`、`apps/web/package.json`、`apps/web/config/`。
- **依赖**：无。
- **开源件**：`@thatopen/components`(MIT)、`@thatopen/fragments`(MIT)、`web-ifc`(MPL-2.0，需法务确认——弱 copyleft、按文件级，通常商用可接受)、`ifcopenshell`(LGPL-3.0)、`ezdxf`(MIT)。
- **工作量**：S（1）。
- **验收标准**：`pip install -r requirements.txt` 与 `npm install` 均通过 CI；三个开关关闭时行为与现网完全一致（回归无差异）。
- **风险**：web-ifc 许可证为 MPL-2.0（非 MIT），须写进法务核验清单；LGPL 的 IfcOpenShell 以「独立进程/动态链接」方式使用，不污染主代码库许可。

---

### WS1 — IfcOpenShell 程序化建模（替换私有挤出）

#### A-02 · FloorElements → IFC 映射器
- **描述**：基于并行创建的 `model_ifc_builder.py`，实现从 `FloorElements`（柱/墙/梁/板轮廓 + 米坐标 + 楼层标高）到 `IfcWall/IfcColumn/IfcBeam/IfcSlab` 的映射，构件挂到对应 `IfcBuildingStorey`，几何用 `IfcExtrudedAreaSolid`（与现有挤出逻辑 1:1）。缺失标高/厚度时沿用现有默认常量并在 IFC `Pset` 标注 `is_estimated=true`（真实标高恢复见 Phase B）。
- **交付物**：`build_ifc_from_scene(scene) -> bytes(.ifc)` 函数；产出可被 BlenderBIM/That Open 打开的合规 IFC4。
- **涉及/新增文件**：`apps/api/services/model_ifc_builder.py`（扩展）、新增 `apps/api/services/ifc_mapping.py`（FloorElements→IFC 实体映射，控制在 <400 行）。
- **依赖**：A-01；`model_ifc_builder.py` 骨架（并行「任务1」）。
- **开源件**：`ifcopenshell.api`(LGPL-3.0)。
- **工作量**：M（3）。
- **验收标准**：给定一张已识别构件的矢量图，产出的 `.ifc` 用 `ifcopenshell.open()` 校验通过、`IfcProject→Site→Building→Storey` 层级完整、构件数量与 `FloorElements` 计数一致；空间断言用 pytest 固定样例。
- **风险**：楼层标高在 Phase A 仍是估算，须显式标 `is_estimated`，绝不伪装成实测（与 Phase B 边界一致）；单体（building_unit）分组需复用 `model_elements.group_buildings` 结果，避免重复分组逻辑。

#### A-03 · model_builder 集成 IFC 建模 + 存储 + scene 注册
- **描述**：在 `build_scene` 中新增「程序化 IFC」分支：当 `MODEL_IFC_ENABLED` 且楼层有确定性构件时，调用 A-02 生成 IFC，上传 MinIO，并在 `scene` 写入 `ifc_key` / `build_mode`。与现有 `_ifc_to_glb_sync`（用户上传的 IFC 原件）互不冲突：程序化 IFC 走独立 key 命名空间。渲染放线程池，失败降级为现有贴图/挤出模式（绝不中断构建）。
- **交付物**：`build_scene` 输出 `scene.model_ifc = {ifc_key, build_mode, is_estimated, generated_at}`；MinIO `projects/{id}/model_ifc/{building_key}.ifc`。
- **涉及/新增文件**：`apps/api/services/model_builder.py`（新增 `_build_programmatic_ifc` + 主流程接线）。
- **依赖**：A-02。
- **开源件**：`ifcopenshell`(LGPL-3.0)、MinIO（已集成）。
- **工作量**：M（2）。
- **验收标准**：构建一个含≥1 层确定性构件的项目，`project_models.scene` 出现 `model_ifc.ifc_key` 且 MinIO 可下载；开关关闭时该字段不出现、行为回归无差异；任一步失败 `scene` 仍成功产出（降级路径有单测覆盖）。
- **风险**：大项目多单体 IFC 体积；沿用 `MAX_TEXTURES_PER_PROJECT` 同类上限思路加 `MAX_IFC_BUILDINGS` 保护。

#### A-04 · IFC → Fragments(.frag) 离线转换
- **描述**：新增离线转换步骤，把 A-03 产出的 `.ifc` 转成 `.frag`（That Open Fragments 格式，2GB IFC→~80MB，百万构件 60fps）。以 Node 脚本/独立小服务运行 `@thatopen/fragments` 的 IfcImporter（web-ifc 解析），产物上传 MinIO，`scene` 写 `frag_key`。glTF 路线（现有 `_ifc_to_glb_sync`）保留作轻量预览兜底。
- **交付物**：`scripts/ifc_to_fragments.mjs`（或 `apps/model-convert/` 轻服务）；`scene.model_ifc.frag_key`；Celery 内以子进程调用。
- **涉及/新增文件**：`scripts/ifc_to_fragments.mjs`、`apps/api/services/fragments_convert.py`（子进程封装 + 上传）、`apps/api/tasks/model_build.py`（转换接线）。
- **依赖**：A-03。
- **开源件**：`@thatopen/fragments`(MIT)、`web-ifc`(MPL-2.0)、Node 20+。
- **工作量**：M（3）。
- **验收标准**：给定合规 `.ifc`，脚本产出 `.frag` 且能被前端 Fragments 加载器解析显示；转换失败时 `frag_key` 缺省、前端回退 glTF/挤出（有降级测试）；1 万构件样例转换 <30s。
- **风险**：Node 侧新增运行时（镜像需装 Node）；若不愿引入 Node，退路是「前端直接用 web-ifc 加载 `.ifc`」——但会牺牲 Fragments 的性能优势，需评审取舍（建议保留 A-04）。

#### A-05 · project_models 迁移：IFC/Fragments 字段
- **描述**：新增迁移 `017`，为 `project_models` 增列或在 `scene` JSON 约定字段（优先 JSON，避免频繁 DDL）：记录 `build_mode`（texture/elements/ifc）、`ifc_key`、`frag_key`、`is_estimated`。若走列式，补索引。
- **交付物**：`migrations/017_project_model_ifc.sql`；字段约定文档更新到 `MODEL_BASE_BLUEPRINT.md`。
- **涉及/新增文件**：`apps/api/migrations/017_project_model_ifc.sql`、`docs/MODEL_BASE_BLUEPRINT.md`（第 4/7 节增补）。
- **依赖**：A-03。
- **开源件**：PostgreSQL（已用）。
- **工作量**：S（1）。
- **验收标准**：`alembic upgrade head` / 直接 SQL 均可执行；回滚脚本可用；旧数据读取兼容（字段缺省不报错）。
- **风险**：低。若全用 `scene` JSON 承载则本任务可并入 A-03，视团队 DDL 规范决定。

---

### WS2 — Web 端 Fragments 加载

#### A-06 · 前端 Fragments 加载器封装
- **描述**：引入 `@thatopen/components` + `@thatopen/fragments` + `web-ifc`，封装一个与现有 three.js 场景共存的 `useFragmentsLoader` hook / `FragmentsScene` 组件：从 `frag_key` 拉取 `.frag`，初始化 That Open world，挂载相机/光照/网格。复用现有 `ModelViewer` 的容器与交互约定。
- **交付物**：`FragmentsScene.tsx` + `useFragmentsLoader.ts`；WASM（web-ifc）本地托管配置。
- **涉及/新增文件**：`apps/web/src/pages/model/ProjectModel/FragmentsScene.tsx`、`.../useFragmentsLoader.ts`、`apps/web/src/services/projectModel.ts`（补 frag_key 类型）。
- **依赖**：A-01、A-04（需要 `.frag` 样例产物，可先用固定样例文件解耦联调）。
- **开源件**：`@thatopen/components`(MIT)、`@thatopen/fragments`(MIT)、`web-ifc`(MPL-2.0)、`three`(MIT，已在)。
- **工作量**：M（3）。
- **验收标准**：给定一个 `.frag` 样例，页面流畅加载并可旋转/缩放；WASM 从本站加载（无外部 CDN 依赖，符合 CSP）；首屏加载 10 万构件模型交互 ≥30fps（本地实测）。
- **风险**：web-ifc WASM 体积与加载路径配置；That Open API 版本演进快，锁定 minor 版本并记录。

#### A-07 · ProjectModel 集成 Fragments + 渲染模式切换
- **描述**：在 `ProjectModel/index.tsx` 增加渲染模式选择：`ifc(Fragments)` 优先，无 `frag_key` 时回退现有 `elements`（挤出）/`texture`（贴图）。加一个 UI 切换（沿用 `ModelQualityPanel`/工具栏风格），保证三种模式共存不冲突。
- **交付物**：模式切换 UI + 状态管理；`frag_key` 存在时默认 Fragments。
- **涉及/新增文件**：`apps/web/src/pages/model/ProjectModel/index.tsx`、`ModelViewer.tsx`、`sceneBuilder.ts`（模式分派）。
- **依赖**：A-06。
- **开源件**：同 A-06 + `antd`(MIT，已在)。
- **工作量**：M（2）。
- **验收标准**：同一项目在三种模式间切换无白屏/内存泄漏；无 `frag_key` 项目行为与现网一致；切换保留相机视角。
- **风险**：three.js 挤出场景与 That Open world 的资源释放（`dispose`）需严格管理，防止切换泄漏。

#### A-08 · Fragments 构件拾取与成果标记对齐
- **描述**：把现有「成果标记层（markers）/构件点击属性面板」对接到 Fragments 场景：点击构件读取 IFC 属性（GUID/类型/Pset），markers 按楼层/坐标叠加到 Fragments 世界。复用 `SemanticTreePanel`/`SemanticReviewQueue` 的选中联动。
- **交付物**：Fragments 场景下的拾取高亮 + 属性面板 + markers 叠加。
- **涉及/新增文件**：`apps/web/src/pages/model/ProjectModel/FragmentsScene.tsx`（扩展）、`SemanticTreePanel.tsx`（联动接线）。
- **依赖**：A-07。
- **开源件**：`@thatopen/components`(MIT)。
- **工作量**：M（3）。
- **验收标准**：点击构件正确高亮并显示 IFC 类型/GUID；markers 位置与贴图模式语义一致；语义树选中 ↔ 三维高亮双向联动。
- **风险**：markers 目前是稳定伪随机布点（`_stable_point`），与真实 IFC 构件坐标不完全一致；Phase A 只要求楼层级对齐，构件级精确锚定见 Phase B。

---

### WS3 — VLM 语义微服务（读表 / 判专业 / 跨图提示）

#### A-09 · LLM Provider 视觉消息支持
- **描述**：为 `AnthropicProvider` / `OpenAICompatProvider` / `OllamaProvider` 增加多模态消息支持（`image` content block：base64 或 URL），保持现有文本调用签名兼容。`ModelParams` 无需改，走 messages 内容扩展。为「精确计数/坐标/尺寸禁止交给 VLM」写入 provider 层的用途注释与约束。
- **交付物**：三个 provider 支持 `{type:"image", ...}` 消息；单测覆盖构造正确的多模态 payload。
- **涉及/新增文件**：`apps/api/core/llm/providers/anthropic_provider.py`、`openai_compat.py`、`ollama_provider.py`、`base.py`（消息类型注释）。
- **依赖**：A-01。
- **开源件**：`anthropic` SDK、OpenAI 兼容 SDK（已在）。
- **工作量**：M（2）。
- **验收标准**：mock provider 下多模态消息序列化正确（Anthropic content blocks / OpenAI `image_url`）；文本-only 调用回归无差异；错误的超大图被拒（尺寸/大小校验）。
- **风险**：闭源模型强制降采样（Claude 最长边 1568px、GPT-4o 短边 768px）——A0/A1 大图细节须先切图（A-12），此处只负责传输层。

#### A-10 · 注册 VLM 语义引擎
- **描述**：在 `ENGINE_NAMES` 增加 `drawing_semantic_vlm`（读图名/标题栏/判专业/跨图提示），并提供种子配置（primary=本地 Qwen3-VL 或云端 Qwen2.5-VL；batch/fallback 链）。走现有 `custom_http`/`ollama`/`openai_compat` 任一 provider，本地/云端可热切换。
- **交付物**：`ENGINE_NAMES` 增项；种子 SQL 或管理后台可配；文档登记第 14 个引擎。
- **涉及/新增文件**：`apps/api/routers/admin/engine_configs.py`（`ENGINE_NAMES`）、种子迁移 `migrations/018_vlm_engine_seed.sql`、`CLAUDE.md`/memory 引擎清单更新。
- **依赖**：A-09。
- **开源件**：`Qwen3-VL`(Apache 2.0) / `Qwen2.5-VL`(Apache 2.0)、`PaddleOCR-VL`(Apache 2.0)。
- **工作量**：S（1）。
- **验收标准**：管理后台「引擎配置」出现 `drawing_semantic_vlm`，可切换 provider 并保存；`ModelRouter.route("drawing_semantic_vlm", ...)` 走通（对 mock provider）。
- **风险**：本地 Qwen3-VL 需单卡 16–24GB，部署环境须确认 GPU；无 GPU 时默认云端并在文档标注涉密图纸建议本地。

#### A-12 · 图纸切图预处理器（喂 VLM）
> 注：编号先于 A-11 是因为 A-11 依赖它；执行顺序见 §2。
- **描述**：实现 A0/A1 大图的确定性切图：定位标题栏区域（右下/右侧，按图框规则 + `geometry_extractor` 文本框位置），裁出标题栏 crop 与整图缩略图，控制在模型分辨率上限内（避免细节在降采样中丢失）。纯确定性，不调用 VLM。
- **交付物**：`preprocess_for_vlm(data, ext) -> {title_block_png, overview_png, tiles?}`。
- **涉及/新增文件**：`apps/api/services/vlm_preprocess.py`。
- **依赖**：A-01；复用 `geometry_extractor`（文本/矩形）、`model_builder._render_pdf_sync` 渲染逻辑。
- **开源件**：`PyMuPDF`(AGPL/商用双许可，已在栈内使用)、`ezdxf`(MIT)。
- **工作量**：M（2）。
- **验收标准**：对样例施工图，标题栏 crop 命中率（人工核）≥80%；输出图最长边 ≤ 模型上限；DXF/PDF 两路均产出。
- **风险**：标题栏位置非全国统一，需按常见图框（右下角栏）+ 文本密度启发式，Phase A 达标即可，复杂版式留待迭代。

#### A-11 · VLM 语义服务：读图名/标题栏/判专业/跨图提示
- **描述**：新增 `vlm_semantics.py`：输入切图（A-12），经 `ModelRouter.route("drawing_semantic_vlm")` 抽取图名、标题栏字段（图号/专业/比例/日期）、判专业（结构/建筑/机电/装修），并对跨图关联给出「候选提示 + 置信度」。VLM 只做语义与候选，**不产出任何计数/坐标/尺寸**。结果带置信度，落库供审校。
- **交付物**：`extract_drawing_semantics(drawing) -> DrawingSemanticResult(title, discipline, title_block_fields, cross_hints, confidence)`；离线 mock 供 CI。
- **涉及/新增文件**：`apps/api/services/vlm_semantics.py`、`apps/api/services/drawing_semantics.py`（接入点，现已存在 `extract_semantic_candidates`）。
- **依赖**：A-10、A-12。
- **开源件**：`Qwen3-VL`(Apache 2.0)、`PaddleOCR-VL`(Apache 2.0)。
- **工作量**：L（4）。
- **验收标准**：用 5–10 张真实图纸实测，标题栏字段抽取准确率与专业判别准确率出报表（作为版本定版依据）；无网络/无 GPU 时走 mock，CI 通过；输出 schema 稳定且含 `confidence`。
- **风险**：VLM 幻觉——严格限定用途（禁计数/尺寸）；准确率不达预期时降级为「仅 OCR 标题栏 + 规则判专业」，VLM 作增强而非必需。

#### A-13 · VLM 语义结果接入 pipeline
- **描述**：把 A-11 结果并入 `model_builder`/`drawing_semantics` 现有语义流：VLM 抽取的专业/图名用于校正/补全 `drawing.discipline`、`semantic_tree`、`cross_links` 候选；低置信度进入现有 `SemanticReviewQueue` 审校。与确定性识别冲突时，确定性优先、VLM 作候选标注（保留 `source=vlm` + 置信度）。
- **交付物**：`scene.semantic_tree`/`unassigned_drawings` 融合 VLM 候选；审校队列可见 VLM 建议。
- **涉及/新增文件**：`apps/api/services/model_builder.py`（`_semantic_scene_payload`）、`apps/api/services/model_semantics.py`。
- **依赖**：A-11。
- **开源件**：无新增。
- **工作量**：M（2）。
- **验收标准**：VLM 判专业与人工标注一致率纳入 `quality` 统计；`VLM_SEMANTIC_ENABLED` 关闭时回归无差异；VLM 建议在审校队列可采纳/驳回。
- **风险**：不可让 VLM 覆盖确定性结果——冲突仲裁规则须显式且有测试。

---

### WS4 — 图层规则强化构件识别

#### A-14 · geometry_extractor 采集图层名与块名
- **描述**：扩展几何提取，为每个原语记录来源 CAD 图层名（DXF `entity.dxf.layer`）与块引用名（`INSERT` 的 `entity.dxf.name`，并展开块内实体）。PDF 无图层概念，保留空图层兼容。这是图层约定识别的前提。
- **交付物**：primitives 携带 `layer`/`block` 元信息；DXF `INSERT` 块展开。
- **涉及/新增文件**：`apps/api/core/model3d/geometry_extractor.py`（`_collect_dxf_entities` 增 layer/INSERT 展开）。
- **依赖**：A-01。
- **开源件**：`ezdxf`(MIT)、`PyMuPDF`。
- **工作量**：M（3）。
- **验收标准**：DXF 样例中 `A-WALL`/`S-COLU` 等图层名被正确采集；`INSERT` 块内实体被展开为原语并保留块名；`MAX_PRIMITIVES` 上限与降级路径保持不变。
- **风险**：块嵌套/属性块展开的坐标变换（`INSERT` 的 scale/rotation/insert point）需正确应用，否则构件错位——须加带变换的样例测试。

#### A-15 · types 扩展携带图层信息
- **描述**：`DrawingGeometry` 的 lines/rects/polys 承载可选 `layer`/`block`（用并行结构或轻量包装，避免破坏现有元组解包）。保持向后兼容（旧调用不传即空）。
- **交付物**：`types.py` 结构升级 + 现有解包点适配。
- **涉及/新增文件**：`apps/api/core/model3d/types.py`、所有解包 primitives 的调用点（`element_recognizer.py`）。
- **依赖**：A-14（结构协同设计，建议与 A-14 同一 PR 或紧邻）。
- **开源件**：无。
- **工作量**：S（1）。
- **验收标准**：现有 `element_recognizer` 全部单测不回归；新增 `layer` 字段在无 DXF 图层时安全为空。
- **风险**：元组解包广泛，改动面需一次性覆盖；建议用 `@dataclass` 原语替代裸元组以减少后续脆弱性（KISS 权衡，若改动过大则用并行 `layers` 索引列表）。

#### A-16 · element_recognizer 图层约定识别 + 修 filled 漏检 + 块名识别
- **描述**：引入中国施工图图层约定表（`A-WALL`/`S-COLU`/`S-BEAM`/`S-SLAB`/`M-*`/`E-*` 等）作为构件类型的**强先验**：
  1. **修「柱必须 filled」漏检**——`_find_columns` 第 274–289 行：图层命中 `S-COLU`/含「柱」块名的矩形/多边形即使未填充也识别为柱；
  2. **图层约定优先于纯几何启发式**（图层明确时跳过尺寸阈值猜测，提召回）；
  3. **块名识别**（门/窗/设备常为具名块 `INSERT`），补设备/洞口候选。
  图层缺失时回退现有几何启发式（PDF、无规范图层的 DXF 不受影响）。
- **交付物**：`layer_conventions.yaml`（可配图层→构件映射）+ `element_recognizer` 图层优先识别路径；漏检修复。
- **涉及/新增文件**：`apps/api/core/model3d/element_recognizer.py`、新增 `apps/api/data/layer_conventions.yaml`。
- **依赖**：A-14、A-15。
- **开源件**：`ezdxf`(MIT)、`PyYAML`(MIT)。
- **工作量**：L（4）。
- **验收标准**：对含规范图层的 DXF 样例，柱召回率较基线显著提升（未填充柱不再漏检，用固定样例断言计数）；图层约定命中时构件类型准确率 ≥ 纯几何启发式；无图层样例结果不回归。
- **风险**：图层命名不统一（各设计院差异）——用**可配置 YAML** + 别名，不硬编码；图层与几何冲突时的优先级须有测试（图层强先验 vs 尺寸离群）。

---

### WS5 — 测试、联调与里程碑

#### A-17 · 后端单测（IFC / VLM / 图层识别）
- **描述**：TDD 补齐：A-02/03 的 IFC 映射与降级、A-11 的 VLM mock、A-16 的图层识别与 filled 漏检修复。均提供离线 mock（VLM/网络），符合「AI 服务提供离线 mock 用于 CI」约定。
- **交付物**：`tests/test_ifc_mapping.py`、`test_vlm_semantics.py`、`test_element_recognizer_layers.py`、`test_model_builder_ifc.py`。
- **涉及/新增文件**：`apps/api/tests/`。
- **依赖**：A-03、A-11、A-16。
- **开源件**：`pytest`(MIT)。
- **工作量**：M（3）。
- **验收标准**：Phase A 新增/改动模块覆盖率 ≥80%；降级路径（IFC 失败/VLM 失败/无图层）全部有测试；CI 绿。
- **风险**：IFC 断言依赖 IfcOpenShell 运行环境，CI 镜像须含该依赖。

#### A-18 · 前端组件测试（Fragments 加载/切换）
- **描述**：为 `useFragmentsLoader`/`FragmentsScene`/模式切换写组件测试（含无 `frag_key` 回退、切换不泄漏 `dispose`）。视觉重的部分以 E2E（A-20）补。
- **交付物**：Fragments 加载器/模式切换测试。
- **涉及/新增文件**：`apps/web/src/pages/model/ProjectModel/__tests__/`。
- **依赖**：A-07。
- **开源件**：`vitest`/`jest`（按项目现状）、`@testing-library/react`(MIT)。
- **工作量**：S（1）。
- **验收标准**：加载/回退/切换核心分支有断言；WASM 加载走 mock。
- **风险**：WebGL/WASM 在 jsdom 下需 mock，真实渲染留给 E2E。

#### A-19 · 集成测试：矢量图 → IFC → scene 端到端
- **描述**：以真实/构造矢量图跑通 `build_scene`（开启 `MODEL_IFC_ENABLED`）：产出 `scene.model_ifc.ifc_key`、`.frag`（若 A-04 就绪）、图层强化后的构件计数，验证与 `project_models` 持久化一致。
- **交付物**：`tests/test_model_pipeline_ifc.py`（集成级，可 mark `integration`）。
- **涉及/新增文件**：`apps/api/tests/`。
- **依赖**：A-03、A-04、A-16。
- **开源件**：`pytest`(MIT)。
- **工作量**：M（2）。
- **验收标准**：端到端产出合规 IFC + scene 字段齐全；缺构件/缺依赖时按序降级（贴图）不崩。
- **风险**：MinIO/转换子进程在 CI 的可用性，必要时用本地文件桩替代对象存储。

#### A-20 · E2E：Web Fragments 加载与切换
- **描述**：Playwright 覆盖「进入 ProjectModel → Fragments 模式加载 → 旋转/点击构件出属性 → 切换到贴图/挤出模式」关键流。用固定 `.frag` 样例，确定性等待，不用超时假设。
- **交付物**：`tests/e2e/project-model-fragments.spec.ts`。
- **涉及/新增文件**：`apps/web/tests/e2e/`。
- **依赖**：A-08。
- **开源件**：`@playwright/test`(Apache 2.0)。
- **工作量**：M（2）。
- **验收标准**：三种模式加载均通过；构件点击属性面板出现；无控制台报错；截图归档（1440/768 断点）。
- **风险**：WebGL 在 CI headless 需启用 GPU/软件渲染开关。

#### A-21 · 里程碑 Demo 联调与验收
- **描述**：串起 Phase A 全链演示：**一张矢量图 → 合规 IFC（BlenderBIM 可打开）→ Web 端 Fragments 流畅加载 → VLM 读出图名与专业**，并出一份实测报告（VLM 准确率、Fragments 帧率、图层识别召回提升）。
- **交付物**：Demo 脚本/录屏 + 《Phase A 验收报告》（含实测数字与已知边界）。
- **涉及/新增文件**：`docs/PHASE_A_DEMO.md`。
- **依赖**：A-08、A-13、A-16、A-19、A-20。
- **开源件**：全链集成。
- **工作量**：M（2）。
- **验收标准**：见 §4 Phase A 验收总标准，全部满足。
- **风险**：跨 WS 集成暴露的接口错配需预留缓冲；建议 Demo 前 2 天冻结接口。

---

## 2. 依赖关系与执行顺序

### 2.1 依赖 DAG（文字版）

```
A-01 ──┬─> A-02 ─> A-03 ─┬─> A-04 ─> A-06 ─> A-07 ─> A-08 ─┐
       │                 ├─> A-05                          │
       │                 └───────────────> A-19            ├─> A-21
       ├─> A-09 ─> A-10 ─> A-11 ─> A-13 ────────┐          │
       │            A-12 ─┘(A-11 也依赖 A-12)     └─────────┤
       ├─> A-14 ─> A-15 ─> A-16 ─┬──────────────> A-17     │
       │                         └──────────────> A-19     │
       └──────────────────────────────────────────────────┘
测试：A-17(←A-03/A-11/A-16)  A-18(←A-07)  A-19(←A-03/A-04/A-16)  A-20(←A-08)
```

### 2.2 依赖表

| 任务 | 直接前置 |
|---|---|
| A-01 | — |
| A-02 | A-01, model_ifc_builder(任务1) |
| A-03 | A-02 |
| A-04 | A-03 |
| A-05 | A-03 |
| A-06 | A-01, A-04(样例可解耦) |
| A-07 | A-06 |
| A-08 | A-07 |
| A-09 | A-01 |
| A-10 | A-09 |
| A-11 | A-10, A-12 |
| A-12 | A-01 |
| A-13 | A-11 |
| A-14 | A-01 |
| A-15 | A-14 |
| A-16 | A-14, A-15 |
| A-17 | A-03, A-11, A-16 |
| A-18 | A-07 |
| A-19 | A-03, A-04, A-16 |
| A-20 | A-08 |
| A-21 | A-08, A-13, A-16, A-19, A-20 |

### 2.3 建议执行顺序 / 并行分组

四条工作流在 A-01 后**高度并行**（不同文件、低耦合）：

- **Sprint 1（并行启动）**
  - 轨道 A（IFC）：A-01 → A-02 → A-03 → A-05
  - 轨道 B（图层识别，最独立）：A-14 → A-15 → A-16
  - 轨道 C（VLM）：A-09 → A-10；A-12（并行）
- **Sprint 2**
  - 轨道 A：A-04（→ 供前端 `.frag`）
  - 轨道 B：A-16 收尾 + A-17（识别部分）
  - 轨道 C：A-11 → A-13
  - 前端轨道 D：A-06 → A-07（可用固定 `.frag` 样例提前起步，不等 A-04）
- **Sprint 3（收敛）**
  - A-08 → A-18/A-20（前端测试）
  - A-17/A-19（后端集成测试）
  - **A-21 里程碑联调验收**

> 关键路径：`A-01 → A-02 → A-03 → A-04 → A-06 → A-07 → A-08 → A-21`（IFC+前端链，约 18–19 人天串行）。图层轨道与 VLM 轨道均可在关键路径阴影下并行完成，不额外拉长工期。

---

## 3. 工作量汇总

| WS | 任务 | 人天 |
|---|---|---|
| WS0 准备 | A-01 | 1 |
| WS1 IFC 建模 | A-02, A-03, A-04, A-05 | 3+2+3+1 = 9 |
| WS2 Web Fragments | A-06, A-07, A-08 | 3+2+3 = 8 |
| WS3 VLM 语义 | A-09, A-10, A-11, A-12, A-13 | 2+1+4+2+2 = 11 |
| WS4 图层识别 | A-14, A-15, A-16 | 3+1+4 = 8 |
| WS5 测试/联调 | A-17, A-18, A-19, A-20, A-21 | 3+1+2+2+2 = 10 |
| **合计** | **21 个任务** | **47 人天** |

单人串行约 47 人天；4 轨道并行（2–3 人）现实工期约 **6–8 周**，与 1–2 个月目标一致。

---

## 4. Phase A 验收总标准

1. **合规 IFC 底座**：任一含确定性构件的矢量图，`build_scene` 能产出 `ifcopenshell.open()` 校验通过、层级完整（Project→Site→Building→Storey→构件）的 IFC4，可用 BlenderBIM/That Open 打开。
2. **百万构件级 Web**：IFC→`.frag` 转换成功，前端 Fragments 加载 10 万构件级模型交互 ≥30fps；无 `.frag` 时按序回退 挤出/贴图，无回归。
3. **VLM 语义实测**：用团队 5–10 张真实图纸实测标题栏抽取 + 判专业准确率并出报表；VLM **绝不**输出计数/坐标/尺寸。
4. **图层识别提升**：含规范图层 DXF 的柱召回率较基线明显提升（未填充柱漏检修复），无图层样例不回归。
5. **可回退**：三个特性开关全关时，系统行为与现网**逐字节等价**（回归测试保证）。
6. **质量门**：Phase A 新增/改动模块单测覆盖率 ≥80%；关键路径 E2E 通过；CI 绿。
7. **里程碑 Demo（A-21）**：一张矢量图 → 合规 IFC → Web 流畅加载 → VLM 读出图名与专业，全链贯通并留存录屏与报告。

---

## 5. Scope Boundary — Phase A「不做什么」

明确**不纳入** Phase A，避免蔓延到 Phase B/C：

- ❌ **跨视图 z 向恢复**（剖面/立面抽标高、平面↔剖面↔立面配准）→ **Phase B**。Phase A 楼层标高继续用估算常量并显式标 `is_estimated`。
- ❌ **构件拓扑推理**（门窗从属墙、梁-柱支承、板-梁托承）→ **Phase B**。
- ❌ **IFC-QTO 工程量 / 混凝土量 / 模板量 / 钢筋回填 IFC** → **Phase B**。
- ❌ **符号识别学习模型**（CADTransformer/VecFormer）、**自建数据集** → **Phase C**。Phase A 构件识别仅用确定性图层/几何规则。
- ❌ **扫描件重建**（CubiCasa/HEAT/RoomFormer）——图纸确认为 DWG+矢量 PDF，走矢量主线。
- ❌ **VLM 端到端出模型 / VLM 计数**——硬边界，永不触碰。
- ❌ **人工审校工作台深化**——Phase A 仅复用现有 `SemanticReviewQueue` 雏形接纳 VLM 候选，不做工作台重构（→ Phase C）。
- ⚠️ **markers 构件级精确锚定**——Phase A 仅楼层级对齐，构件级坐标锚定随 Phase B z 恢复一并处理。
```
