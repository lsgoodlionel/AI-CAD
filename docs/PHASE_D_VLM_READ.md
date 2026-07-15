# Phase D 泳道5 · item 2 — 远程 VLM 读图适配器（qwen3.5-vision）

> 版本 V1.0 ｜ 2026-07-15 ｜ 承接 `docs/PHASE_D_LANE5_PLAN.md`（泳道5 前沿升级）
>
> 代码：`apps/api/core/model3d/vlm_read/`（`types.py` / `ollama_vlm.py` / `parse.py`）+
> CLI `apps/api/scripts/model3d/vlm_read_drawing.py` + 测试 `apps/api/tests/test_vlm_read.py`（32 例，全离线 mock）。

## 1. 背景（已实证，人工真跑）

远程部署的 Ollama 上跑 `qwen3.5:latest`（具备 vision 能力）。给上海大歌剧院一张
结构剖面图 PNG，模型正确：

- 判专业：结构
- 读出真实标高：-3.200 / -4.700 / +15.00
- 识别构件：梁 / 板 / 柱 / 基础底板

这是 **VLM 推理读图**，不需要 GPU 训练、不需要标注数据集，是 OCR 之外的
**语义读图证据源**。本任务把这条能力封装成可复用适配器。

## 2. 铁律（本项目根本原则，本模块严格遵守）

1. **VLM 只做语义候选 + 置信度，绝不输出计数 / 坐标 / 尺寸 / QTO。**
   `VlmReadResult` 里的 `discipline` / `elevations` / `components` 都是候选
   （`*Candidate` dataclass 带 `confidence` + `evidence`），不是权威真值，
   下游必须经人工或确定性规则复核后才可采信。真正的计数/坐标/尺寸/QTO 由
   `core/model3d/topology_rules.py`、`services/model_qto.py` 等确定性几何管线
   产出，VLM 结果永远不得替代或覆盖它们。
2. **绝不虚高。** 结构化提示词让模型在读不出时显式回答"无"/"unknown"；
   解析器对"无法判断"一律返回 `None`/空元组，不猜测、不编造。全文兜底扫描
   （模型没按格式作答时的退化路径）产出的候选置信度显著低于结构化命中
   （0.55 vs 0.85），供下游按阈值过滤。

## 3. 端点安全 — 如何配置（绝不硬编码）

`ollama_vlm.resolve_base_url()` 按以下优先级解析远程端点，**代码里不出现任何
真实地址**：

1. 数据库 `llm_providers` 表，`name = 'Ollama 远程'` 的 `base_url` 字段
   （复用现有模型路由治理表 `core/llm/router.py` 同款 schema；管理后台可见、
   可随时改，不用碰代码）。
2. 环境变量 `REMOTE_OLLAMA_BASE_URL`。
3. 两者皆缺 → 返回 `None`，`read_drawing_vlm` 优雅降级为
   `backend="none"` + warning，不抛错、不阻断上游。

本地开发地址存放在 gitignored 的 `apps/api/.env.remote-ollama.local`（本模块
代码不读取该文件，也不假设其存在——env var 需要由部署/启动脚本注入）。

```bash
# 本地人工真跑示例（真实地址不进任何被提交文件）
export REMOTE_OLLAMA_BASE_URL="http://<your-remote-host>:11434"
cd apps/api
.venv/bin/python scripts/model3d/vlm_read_drawing.py /path/to/drawing.pdf
```

`llm_providers` 表尚未 seed `'Ollama 远程'` 这一行（本任务未新增迁移文件，
属于文件边界外）；在未 seed 前，DB 路径静默跳过（`fetch_one` 因表内无匹配行
或 DB 未连接而返回 `None`/抛错均被吞掉），直接走 env 回退——不影响可用性。

## 4. 产物结构 — `VlmReadResult`

```python
VlmReadResult(
    discipline=DisciplineCandidate(value="结构", confidence=0.85, evidence="专业：结构"),
    elevations=(
        ElevationCandidate(value_m=-4.7, confidence=0.85, evidence="-4.700"),
        ElevationCandidate(value_m=-3.2, confidence=0.85, evidence="-3.200"),
        ElevationCandidate(value_m=15.0, confidence=0.85, evidence="+15.00"),
    ),
    components=(
        ComponentCandidate(label="梁", confidence=0.85, evidence="梁"),
        ComponentCandidate(label="板", confidence=0.85, evidence="板"),
        ComponentCandidate(label="柱", confidence=0.85, evidence="柱"),
        ComponentCandidate(label="基础底板", confidence=0.85, evidence="基础底板"),
    ),
    raw_text="专业：结构\n标高：-3.200、-4.700、+15.00\n构件：梁、板、柱、基础底板",
    backend="qwen3.5-vision",
    model="qwen3.5:latest",
    warnings=(),
)
```

- `.available`：`backend != "none"`，与 `core/model3d/ocr/types.py::OcrResult.available` 同款语义。
- `.filter_confidence(min_conf)`：按置信度过滤三类候选，返回新对象（不可变约定，人工复核纪律同 OCR 模块）。
- `.to_dict()`：JSON 友好序列化，供 CLI/日志/未来 API 端点直接输出。

## 5. 解析逻辑（`parse.py`，纯函数、可离线测）

提示词（`ollama_vlm._PROMPT`）要求模型按固定三行格式作答：

```text
专业：<答案>
标高：<答案>
构件：<答案>
```

解析优先匹配结构化行（`专业：` / `标高：` / `构件：` 正则），命中给
**高置信 0.85**；模型偏离格式时退化为全文关键词/正则兜底扫描，给
**低置信 0.55**。三类候选各自的规则：

- **专业**：从固定词表 `建筑/结构/给排水/暖通/电气/道路/景观` 中匹配；模型显式
  回答"unknown"/"无法判断"时返回 `None`，不猜。
- **标高**：正则 `[±+\-]?\d{1,3}\.\d{2,3}` 提取数值，过滤到合理范围
  `[-30, 300]` 米（防模型幻觉出荒谬数值），去重、按数值升序。
- **构件**：固定词表（梁/板/柱/基础底板/剪力墙/桩……）按**字符区间**去重——
  单字词（如"板"）若命中位置落在已被更长词（如"基础底板"）覆盖的区间内则
  丢弃该次出现，避免"基础底板"重复贡献出裸露的"基础"/"板"候选；但同一单字
  若在文本别处独立出现，仍是有效候选。

## 6. 图像缩放（已实测的硬约束）

远程服务端对图像大小有限制：

| 输入 | 结果 |
|---|---|
| 原图 4718×3338 px / 650KB | HTTP 400（超限） |
| 缩到约 910×644 px / 79KB（缩放矩阵约 0.27） | HTTP 200 成功 |

`ollama_vlm.prepare_image(image_bytes, max_dim_px=1280)` 处理这一步：宽/高
较大者等比缩到 `max_dim_px`（默认 1280），已经小于该尺寸的图不放大。
`read_drawing_vlm` 内部总是先调用它再发请求，调用方不需要自己操心缩放。

CLI (`scripts/model3d/vlm_read_drawing.py`) 用 `fitz.Matrix` 渲染 PDF 首页为
PNG（默认 150dpi，足够看清标注文字），再交给 `read_drawing_vlm` 做二次缩放。

## 7. 降级行为

`read_drawing_vlm` 每一步失败都优雅降级为 `backend="none"` + 具体 warning，
**不抛错、不阻断上游、不编造结果**：

| 失败点 | 降级结果 |
|---|---|
| 端点未配置（DB 与 env 均缺失） | `backend="none"`，warning 提示两处配置来源 |
| 图像字节非法/无法解码 | `backend="none"`，warning 含异常信息 |
| 远程调用失败（网络/超时/HTTP 错误/响应结构异常） | `backend="none"`，warning 含异常信息 |
| VLM 返回空文本 | `backend="none"`（`parse_vlm_text` 内处理） |

单图推理含 thinking 实测约 25 秒，`_DEFAULT_TIMEOUT_SEC=120` 留足余量。

## 8. 接线点 — 作为 section-z 的第二标高源（本任务未实施，仅记录设计）

现有确定性标高管线：`core/model3d/section_level_extractor.py` →
`services/section_z_recovery.py` / `services/model_z_levels.py`，第一标高源是
矢量字形（`page.get_text`）+ OCR 兜底（`core/model3d/ocr/consume.py::as_geometry_texts`，
门槛 `_GEOMETRY_MIN_CONF=0.8`）。

`vlm_read.elevations` 候选（`VlmReadResult.elevations`）结构上与
`ocr.consume.elevation_candidates()` 的返回形态相近（`value_m` + `confidence`），
**可以**作为第三/第二兜底标高源接入，设计要点（未来落地时遵循）：

1. **只在几何源缺失时兜底**，绝不覆盖矢量字形/OCR 已给出的标高——与现有
   `_section_levels_ocr_fallback` 同款"几何优先、语义补漏"哲学。
2. VLM 候选**没有位置坐标**（铁律：不产出坐标），无法像 OCR token 一样绑定到
   具体标高线（`extract_section_levels` 靠 ±10pt 邻近绑线）；因此 VLM 候选
   只适合用作"这张图大概有哪些标高值"的**存在性校验/交叉验证**（例如：矢量
   识别出 3 个标高，VLM 也读出同一组数值 → 提升人工复核时的置信展示；
   若矢量识别出的标高集合与 VLM 读出的差异很大 → 标记该图为"低置信/需人工
   复核"），而不是直接生成新的标高线记录。
3. 引擎名建议：如果未来经 `ModelRouter` 统一调用（而非本模块的直连 Ollama
   调用），需在 `core/llm/router.py` 的 14 个预定义引擎名之外新增
   `drawing_vlm_read_semantic`（区别于既有 `drawing_semantic_vlm`，后者读图
   名/标题栏/判专业跨图提示，前者聚焦标高/构件候选）——本任务未新增该引擎
   配置，仅记录命名建议供未来落地时参考。
4. 语义树接线：`components` 候选可比照 `ocr.consume.space_labels()` →
   `services/model_semantics.py::merge_into_semantics_input` 的模式，作为
   `drawing["vlm_component_hints"]` 挂载到语义构建输入，同样遵循"未挂该键的
   调用方行为不变"的向后兼容约定。

## 9. 能力边界

- 不做符号 spotting（计数/坐标）——那是 `core/model3d/spotting/`（CADTransformer/融合引擎）的职责。
- 不做 QTO/算量——那是 `services/model_qto.py` 的职责。
- 不保证覆盖所有专业/构件词表之外的术语（MVP 词表，见 `parse.py::_COMPONENT_VOCAB`，按需扩展）。
- 标高合理范围硬编码为 [-30, 300] 米，超高层/深基坑极端项目如超出此范围会被过滤——按需调整 `parse.py::_ELEVATION_MIN_M/_ELEVATION_MAX_M`。
- 未接入 `ModelRouter`（断路器/调用日志/多提供商回退），是直连单一远程 Ollama 的轻量适配器；若要纳入统一治理，需按上节接线点新增引擎配置（不在本任务范围）。

## 10. 测试

`apps/api/tests/test_vlm_read.py`，32 例，全离线（mock httpx.AsyncClient / mock
DB 解析函数 / 合成 PNG），不发起任何真实网络请求：

```bash
cd apps/api
.venv/bin/python -m pytest tests/test_vlm_read.py -q --no-cov
.venv/bin/python -c "import core.model3d.vlm_read.ollama_vlm"
```

覆盖：解析（专业/标高/构件的结构化命中 + 全文兜底 + 去重/去噪）、
`VlmReadResult.filter_confidence`、图像缩放（不放大小图）、`call_vlm_chat`
HTTP 调用与响应结构校验、端点解析优先级（DB > env > None）、整链路四种降级
路径、成功路径端到端（含"发给远程的图已被缩放"的断言）。

人工真跑（不在自动化测试范围，需网络 + 真实端点）：

```bash
export REMOTE_OLLAMA_BASE_URL="http://<remote-host>:11434"
cd apps/api
.venv/bin/python scripts/model3d/vlm_read_drawing.py /path/to/real_drawing.pdf
```
