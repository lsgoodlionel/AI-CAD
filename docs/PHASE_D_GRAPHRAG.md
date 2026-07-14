# D-18 — 合规审查 GraphRAG 化

> 版本 V1.0 ｜ 2026-07-14 ｜ 承接 `docs/PHASE_D_LANE5_PLAN.md` D-18 节
>
> 状态：🟡 **融合层设计 + harness 骨架已就绪，可离线跑通**；真实精度/召回结论
> 待**离线评测集**（合规判定需专家标注，是当前最大缺口，见 §5）。
> 代码位置：`apps/api/core/ai_review/graphrag/`（`types.py` / `fusion.py`）+
> 单测 `apps/api/tests/test_graphrag_fusion.py`（16 例，离线全绿）。

---

## 0. TL;DR

- 新增一层 **GraphRAG 融合召回编排层**，**不改** `kg_engine.py` / `rag_engine.py` /
  `orchestrator.py` 本体 —— 只读它们的公开接口（`BaseEngine.analyze`）来组合。
- **灰度开关默认关闭**（`FusionConfig.enabled = False`）：关闭时 `run_graphrag_fusion`
  恒等回到「`KGEngine.analyze` + `RAGEngine.analyze` 并行拼接」，与 orchestrator
  当前对这两个引擎的处理**字节级一致**，可安全合入而不改变任何现有行为。
- 灰度开启时：KG 结构召回 + RAG **纯向量召回**（不复用 RAG 引擎内部 LangGraph
  的 LLM 步骤，避免同一查询两次 LLM）→ 合并去重（regulation_ref 精确匹配优先，
  否则文本相似度）→ GraphRAG 自己的 **LLM 多步核查**（走 `ModelRouter`）→
  核查不可用时优雅降级为「合并候选直出」（`mode=fusion_degraded`，issue 显式
  标注「未经核实」，供下游/评测过滤，绝不静默伪装成已核实结论）。
- 本文同时给出**合规审查评价标准判定建议**（用户明确要的交付物，见 §3）与
  **评测 harness 骨架设计**（复用 `core/model3d/eval` 的度量范式，见 §4）。

---

## 1. 现状回顾

| 引擎 | 召回方式 | LLM 调用 | 输出 |
|---|---|---|---|
| KG（`kg_engine.py`） | AGE Cypher 图谱遍历（主路径）/ `regulation_articles` 表查询（降级） | 无 | `list[AIIssue]`，engine="kg" |
| RAG（`rag_engine.py`） | Chroma 向量 Top-K 检索 | 有（LangGraph 三步：identify→lookup→synthesize，经 `ModelRouter`） | `list[AIIssue]`，engine="rag" |
| Orchestrator | KG 与 RAG **各自独立并行**跑（`asyncio.gather`），结果直接拼接写库 | — | 拼接后的 `list[AIIssue]` |

问题：两路召回互不知情，无法利用「图谱结构 + 语义向量」互补，也没有一个统一
的最终核查步骤对两路结论去重/仲裁/授予义务等级。

---

## 2. GraphRAG 融合召回层设计

### 2.1 双路召回

- **KG 路径**：直接调用 `KGEngine().analyze(ctx, db)`（默认注入，可测试时替换）。
  该引擎本身无 LLM 调用，天然是「结构召回」，直接产出 `AIIssue`。
- **RAG 路径**：**不**调用 `RAGEngine.analyze()`（那会带上其内部 LangGraph 的
  LLM 步骤，造成同一查询两次 LLM、成本翻倍且语义上是重复核查）。而是新增一个
  独立的**纯向量召回**函数 `_default_vector_retrieval`，直接查询同一个 Chroma
  collection（`regulation_articles`），返回**结构化候选**
  `RetrievalCandidate(source="rag", regulation_ref, snippet, ...)`，而非
  `rag_engine._query_chroma` 那种拼接成一段文本的形式——后者若拿来逆向切分成
  结构化候选反而更脆弱，索性独立实现（约 20 行，权衡见 §6）。

### 2.2 合并去重（`_merge_and_dedup`，纯函数、确定性）

1. `regulation_ref` 精确相等 → 直接判定同一候选，合并 `sources`。
2. 未命中则退到文本相似度：`difflib.SequenceMatcher(snippet_a, snippet_b).ratio()
   >= dedup_similarity_threshold`（默认 0.72，`FusionConfig` 可调）。
3. 先到者（KG 候选先处理）的 `snippet` / `severity_hint` 不被后到者覆盖——只
   追加 `sources`，与 `core/model3d/fusion` 的「规则候选全保留、召回保底」哲学
   一致。
4. 结果按 `max_merged_candidates`（默认 20）截断，防止病态输入喂给 LLM 过长
   prompt。

产出 `FusionCandidate`：`sources` 长度 ≥2（即同时被 kg 与 rag 命中）视为**双路
共识**（`is_consensus`），后续核查/降级路径都会据此提升可信度。

### 2.3 LLM 多步核查（`_default_llm_verify`）

对合并去重后的候选，一次性发给 LLM（走 `ModelRouter.route("graphrag_verifier",
messages)`），要求逐条判定：

- `severity`：critical / major / minor / info
- `obligation_level`：**MUST / SHOULD / MAY / MUST_NOT**（对齐规范知识库设计的
  义务等级），无法判断则填 SHOULD
- 不成立的候选从返回的 `issues` 数组中剔除（LLM 不臆造，未确认的候选**不会**
  被静默转换成"低置信度问题"，而是直接消失——避免噪声）

解析失败（JSON 不合法 / 字段缺失）与路由失败（模型未配置 / 断路器 OPEN / 网络
不可达）统一被 `run_graphrag_fusion` 捕获，触发同一条**优雅降级路径**。

### 2.4 优雅降级（`mode=fusion_degraded`）

LLM 核查不可用时，合并候选**直接转成 AIIssue**，但：

- `description` 显式前缀 `[GraphRAG 降级·LLM 未核实·来源:kg+rag] ...`，任何下游
  消费方（前端 / 评测 harness）都能按此过滤，不会把「未经核实的候选」误当成
  「LLM 已确认的合规结论」。
- 双路共识候选按「共识增强」经验规则升一级 severity（`INFO→MINOR→MAJOR→
  CRITICAL`，`CRITICAL` 封顶），单路候选保持原始 `severity_hint`——这是融合层
  在没有 LLM 时能做的唯一确定性判断，真正的合规结论判定权始终留给 LLM 核查步骤
  或人工审校，融合层本身不臆断合规性。

### 2.5 灰度开关

```python
from core.ai_review.graphrag import run_graphrag_fusion, FusionConfig

# 默认关闭：与现状字节级一致
result = await run_graphrag_fusion(ctx, db, redis)
assert result.mode == "identity"

# 开启：走双路融合
result = await run_graphrag_fusion(ctx, db, redis, config=FusionConfig(enabled=True))
```

**尚未接入 orchestrator.py**（按文件边界要求本轮不改其本体）。接入方式（供下一步
参考，非本轮交付）：在 orchestrator 的并行引擎列表里，用
`run_graphrag_fusion(ctx, db, redis, config=cfg)` 替换掉分别构造
`KGEngine()`/`RAGEngine(db, redis)` 后调用两次 `analyze` 的写法，`cfg.enabled`
可来自引擎业务参数配置（沿用现有「管理后台可调参数」范式），做到运行时热切换、
不需要重新部署。

---

## 3. 合规审查评价标准判定建议（核心交付物）

> 目标：给出一套可复现、可对比「纯 KG / 纯 RAG / GraphRAG 融合」优劣的评价口径。
> 这套口径与本项目 `core/model3d/eval` 的 PQ/精度/召回体系同源（同一套「TP/FP/FN
> → precision/recall/f1」骨架），但**匹配单元从「几何 bbox」换成「合规判定单元」**。

### 3.1 判定单元：什么算「一条可比较的问题」

合规审查的输出不是 bbox，需要先定义可比较的最小单元。建议采用**三元组**：

```
(drawing_id, regulation_ref_normalized, discipline)
```

- `regulation_ref_normalized`：条文引用归一化（去空格、统一「第X条」/「X.X.X」
  格式、剥离版本年份差异如 `GB50010` vs `GB50010-2010` 视配置决定是否等价）。
- 未引用具体条文的问题（如语义模糊的 RAG 结论）退化为按
  `(drawing_id, discipline, 文本相似度聚类簇)` 匹配——类比 `core/model3d/eval`
  里「无法用类别精确匹配时退化到位置匹配」的思路（该模块的 `_confusion` 就是
  「不看类别只看位置」的降级匹配，直接可以复用同一种思想）。

### 3.2 核心指标（一级，必须报）

| 指标 | 定义 | 说明 |
|---|---|---|
| **Precision** | TP / (TP + FP) | 系统报出的问题里有多少是真问题（误报率 = 1 − Precision） |
| **Recall** | TP / (TP + FN) | 金标准里的真问题有多少被系统覆盖到（漏报率 = 1 − Recall） |
| **F1** | 2PR / (P+R) | 兼顾误报/漏报的单一数字，用于方法间快速比较 |
| **条文引用命中率** | 命中正确 `regulation_ref` 的 TP 数 / TP 总数 | 区分「问题定位对了但引条文引错了」——这是合规审查特有的、bbox 匹配体系里没有的维度，必须单独报，因为「说对了但引错条文」在工程场景里几乎等同于误导 |

**TP / FP / FN 判定规则**：

- **TP**：系统问题与金标准问题在 §3.1 定义的判定单元上匹配（同图纸+同条文/
  同簇+同专业），且 severity 方向一致（不要求 severity 完全相等，但不能一个说
  critical 一个说 info——阈值可配置，建议差 ≤1 级仍算 TP，差 ≥2 级降级为「弱 TP」
  单独统计，不计入主 Precision/Recall，只在 per_category 明细里体现）。
- **FP**：系统报出但金标准未标注为问题（含「条文引用错误导致判定单元对不上」
  ——即使系统直觉正确但引错条文，也应计入 FP，同时在「条文引用命中率」里体现
  该子问题，避免和「彻底报错」的 FP 混为一谈，建议用 `fp_type: wrong_ref |
  spurious` 二级标签区分）。
- **FN**：金标准里有、系统未报出的问题。

### 3.3 MUST/SHOULD/MAY/MUST_NOT 义务等级混淆矩阵（二级，必须报）

对**判定为 TP 的问题**，额外统计一张 4×4 混淆矩阵（行=金标准义务等级，
列=系统判定义务等级）：

```
              系统: MUST  SHOULD  MAY  MUST_NOT
金标准 MUST       ..     ..    ..     ..
金标准 SHOULD     ..     ..    ..     ..
金标准 MAY        ..     ..    ..     ..
金标准 MUST_NOT   ..     ..    ..     ..
```

- 对角线之外的格子里，**最需要关注的错误方向是「金标准 MUST → 系统判成
  SHOULD/MAY」**（把强制性条款误判为建议性，工程风险最大）；反向（把 MAY 误判
  成 MUST）属于「过度保守」，风险等级低但会拉低用户信任度，两者应分开报告，不
  能只看总体准确率（accuracy）掩盖方向性偏差。
- 建议单独报一个 **「义务降级率」= (MUST→非MUST 的格子之和) / 金标准 MUST 总数**
  作为专项红线指标（对齐项目 KPI 红线一票否决的风格：该指标超过阈值应视为
  GraphRAG 融合不达标，不能上生产灰度）。

### 3.4 误报/漏报口径细化（三级，建议报）

- **误报（FP）细分**：`spurious`（凭空捏造，金标准里该处压根不是问题）vs
  `wrong_ref`（问题方向对但条文引错）vs `wrong_severity`（问题和条文都对但
  severity 差 ≥2 级）。三者对用户体验的伤害程度不同，合并统计会掩盖真实短板。
- **漏报（FN）细分**：`kg_missed_rag_missed`（双路都没召回，融合层无能为力，
  说明底层数据/图谱覆盖不足）vs `retrieved_but_llm_dropped`（双路召回到了，但
  LLM 核查步骤误判为不成立而剔除）。后者是 GraphRAG 融合层**自己引入**的新漏报
  来源（纯 KG/纯 RAG 没有这个环节），必须单独统计，否则无法判断「LLM 核查步骤
  净贡献是正是负」。

### 3.5 三方法对比方法论（纯 KG vs 纯 RAG vs GraphRAG 融合）

对比方式参照 `core/model3d/eval/harness.py::run_comparison` 的三方法对比骨架
（rule vs model vs fusion），移植为：

```
methods = {
    "kg_only":   仅用 KGEngine.analyze 的结果做评测,
    "rag_only":  仅用 RAGEngine.analyze 的结果做评测,
    "graphrag":  run_graphrag_fusion(config=enabled) 的结果做评测,
}
```

在**同一批评测样本**（图纸 + 金标准问题）上分别跑三种方法，产出上述 §3.2/§3.3
指标的三份报告，横向对比。**结构性预期**（融合层设计目标，需评测验证而非假设）：

- `graphrag.recall >= max(kg_only.recall, rag_only.recall)`
  ——双路召回原则上应扩大覆盖面（合并去重不丢候选，LLM 核查只做筛选不做新增召回）。
- `graphrag.precision` 相对 `rag_only.precision` 应有提升（LLM 核查步骤剔除了
  单纯向量检索的模糊噪声候选），但相对 `kg_only.precision`（结构化、天然高精度）
  可能持平或略降——因为召回面扩大了，这是预期的 precision/recall 权衡，不代表
  融合层"更差"，报告须把这一权衡讲清楚，不能只看单一 F1 数字下结论。
- 若评测显示 `graphrag.recall < kg_only.recall`（即融合层比单纯 KG 召回更少），
  说明 LLM 核查步骤过度剔除，是需要修正 prompt/阈值的明确信号（对齐 §3.4 的
  `retrieved_but_llm_dropped` 指标）。

### 3.6 与人工审校的关系（复用现有埋点体系）

Phase C 已建立「返工点埋点」体系（`migrations/024_review_actions.sql`，确认/
改类/否定/补框率）。D-18 评测集冷启动阶段，可直接复用同一套人工审校动作作为
金标准来源的一部分：审校人员对 GraphRAG 灰度输出做「确认/否定/改判 severity/
改判义务等级」，这些动作本身就是评测所需的金标签,与 C-16/C-17 的「数据飞轮」
思路一致——不必等到专门的标注项目，先用灰度上线 + 人审埋点滚雪球攒出评测集。

---

## 4. 评测 harness 骨架设计（复用 `core/model3d/eval` 度量范式）

> 本轮**未新建** `core/ai_review/eval/` 目录（超出严格文件边界），此处给出可
> 直接落地的骨架设计，供下一轮实现时对照。度量核心逻辑（TP/FP/FN 匹配、
> precision/recall/f1/混淆矩阵）与 `core/model3d/eval/metrics.py` 同构，只需把
> 「bbox IoU 匹配」换成 §3.1 的「判定单元匹配」。

```python
# 设想中的 core/ai_review/eval/metrics.py（骨架，未创建）
@dataclass(frozen=True)
class ComplianceGt:
    """金标准问题（人工标注）。"""
    drawing_id: str
    regulation_ref: str
    discipline: str
    obligation_level: str  # MUST/SHOULD/MAY/MUST_NOT
    severity: str

def _match_unit(a, b) -> bool:
    """判定单元匹配（对齐 §3.1）：同图纸 + 条文归一化相等（或文本相似度退化）。"""
    ...

def evaluate_compliance(gt: list[ComplianceGt], pred: list[AIIssue]) -> ComplianceMetrics:
    """产出 §3.2 核心指标 + §3.3 义务等级混淆矩阵 + §3.4 FP/FN 细分。
    实现思路与 core/model3d/eval/metrics.py::evaluate 同构：
      1. 贪心匹配 gt/pred（本例按判定单元而非 IoU）
      2. TP/FP/FN 计数 → precision/recall/f1
      3. 额外产出 4x4 obligation_level 混淆矩阵（仅在 TP 子集上统计）
      4. FP/FN 二级细分标签（spurious/wrong_ref/wrong_severity，
         kg_missed_rag_missed/retrieved_but_llm_dropped）
    """
```

```python
# 设想中的 core/ai_review/eval/harness.py（骨架，未创建）
# 对齐 core/model3d/eval/harness.py::run_comparison 的三方法对比模式
async def run_compliance_comparison(samples: list[EvalSample]) -> ComparisonReport:
    methods = {}
    for method_name, runner in [
        ("kg_only",  lambda ctx: kg_engine.analyze(ctx, db)),
        ("rag_only", lambda ctx: rag_engine.analyze(ctx, db)),
        ("graphrag", lambda ctx: run_graphrag_fusion(ctx, db, redis, config=FusionConfig(enabled=True))),
    ]:
        preds = [await runner(s.ctx) for s in samples]
        methods[method_name] = evaluate_compliance(flatten(s.gt for s in samples), flatten(preds))
    return ComparisonReport(methods=methods, sample_count=len(samples), notes=(
        "graphrag 端在无真实评测集/无 ollama 权重前用 mock llm_verify 代入，"
        "结论待评测集与真实 LLM 核查同时就绪后复评（同 Phase C M1 的诚实边界表述）。",
    ))
```

产出报告建议复用 `core/model3d/eval/report.py` 的 Markdown 渲染范式（表格化三方法
对比 + 混淆矩阵渲染），保持全项目评测报告风格一致。

---

## 5. 缺口：真实评测集

这是 D-18 当前**唯一的硬阻塞**（与 D-16/D-17 类似，工具链已就绪，缺样本）：

- 需要一批**图纸 + 人工合规判定结论**（`drawing_id, regulation_ref,
  discipline, obligation_level, severity` 五元组金标签）。
- 冷启动建议（同 §3.6）：先在**灰度关闭**状态下常规运行 KG/RAG 拿到现状问题，
  人工审校确认/否定/改判，把审校结果当金标准种子；灰度打开后同一套审校流程
  继续滚雪球——不需要专门标注项目单独立项，复用 C-16/C-17 的审校飞轮机制。
- 在评测集就绪前，本文档 §3 的判定标准与 §4 的 harness 骨架已可直接落地，只是
  `ComparisonReport` 里 `graphrag` 一栏的**结论数字**待评测集就绪后才有意义
  （与 Phase C M1 终评「基座就绪、数字待权重」的诚实边界表述一致）。

---

## 6. Ollama 临时测试（本地 gemma4:latest）

D-18 的 `llm_verify` 默认实现（`_default_llm_verify`）走
`ModelRouter.route("graphrag_verifier", messages)`。若数据库里没有该
`engine_name` 的 `engine_model_configs` 种子，`ModelRouter._get_config` 返回
`None` → `route()` 抛 `RuntimeError` → `run_graphrag_fusion` 捕获并优雅降级为
`mode=fusion_degraded`（见 §2.4）——**这是设计内的安全默认行为，不会报错崩溃**，
只是拿不到 LLM 核查的增益。

### 6.1 若要用本地 ollama gemma4:latest 临时测试真实 LLM 核查步骤

沿用 `migrations/018_vlm_engine_seed.sql` 同款「provider → model → engine 配置」
三段式种子写法（本文档只给出 SQL 片段供本地临时执行，**未新建 migration 文件**
——按本轮严格文件边界，正式落库需另起一个 `migrations/0NN_graphrag_engine_seed.sql`，
不在本轮交付范围内）：

```sql
-- §1 复用 002 已内置的 'Ollama 本地' provider（base_url 默认
--    http://host.docker.internal:11434；本地裸跑 Python 若不在容器里，
--    改成 http://localhost:11434，与本次任务描述的临时测试环境一致）
UPDATE llm_providers SET base_url = 'http://localhost:11434'
WHERE name = 'Ollama 本地';

-- §2 注册 gemma4:latest 模型（本地跑，价格填 0）
INSERT INTO llm_models
    (provider_id, model_id, display_name, context_window, supports_vision,
     input_price_per_1m, output_price_per_1m)
SELECT id, 'gemma4:latest', 'Gemma 4 (本地 Ollama)', 8192, false, 0, 0
FROM llm_providers WHERE name = 'Ollama 本地'
ON CONFLICT (provider_id, model_id) DO NOTHING;

-- §3 为 graphrag_verifier 引擎注册 primary 配置
INSERT INTO engine_model_configs
    (engine_name, task_type, model_id, temperature, max_tokens, top_p)
SELECT 'graphrag_verifier', 'primary', lm.id, 0.10, 2048, 0.90
FROM llm_models lm
JOIN llm_providers lp ON lp.id = lm.provider_id
WHERE lp.name = 'Ollama 本地' AND lm.model_id = 'gemma4:latest'
ON CONFLICT (engine_name, task_type) DO NOTHING;
```

之后 `run_graphrag_fusion(ctx, db, redis, config=FusionConfig(enabled=True))`
（不传 `llm_verify` 覆盖）会自然走到本地 ollama。**提醒**：`gemma4` 是通用对话
模型，未针对 JSON 结构化输出微调，`_parse_verify_response` 对非法 JSON 会抛
异常（预期行为，见 §2.4 自动降级）；本地临时验证时建议先用
`ollama run gemma4:latest` 手工核对模型是否稳定输出纯 JSON，不稳定属于预期
（该模型非本项目最终选型，仅供临时链路验证），生产灰度前应换回项目已验证的
`claude-sonnet-4-6` / `deepseek` 等结构化输出更稳定的模型。

### 6.2 不改数据库的更轻量验证方式

单测已经覆盖了 `_default_llm_verify` 之外的全部逻辑（合并去重/降级/灰度开关），
若只想验证"本地 ollama 网络能通、gemma4 能返回内容"，可绕开 `ModelRouter`
直接跑：

```python
import asyncio, httpx

async def quick_check():
    r = await httpx.AsyncClient().post(
        "http://localhost:11434/api/chat",
        json={"model": "gemma4:latest", "messages": [{"role": "user", "content": "你好"}], "stream": False},
        timeout=30,
    )
    print(r.json())

asyncio.run(quick_check())
```

确认本地 ollama 服务与模型本身可用后，再走 §6.1 的 DB 种子把它接入
`ModelRouter`，两步分开排障更清楚。

---

## 7. 关键取舍

| 取舍 | 决定 | 理由 |
|---|---|---|
| RAG 路径是否复用 `RAGEngine.analyze()` | **不复用**，新写独立向量召回 | 避免同一查询两次 LLM（RAG 内部 LangGraph + GraphRAG 自己的核查），成本/延迟翻倍且语义重复 |
| 是否复用 `rag_engine._query_chroma` | **不复用**，独立实现约 20 行 | 该函数返回拼接文本，逆向切分成结构化候选比独立实现更脆弱；代价是与 `rag_engine.py` 存在约 20 行相似逻辑（可接受的重复，换取解耦和可测试性） |
| 灰度开关默认值 | **默认 False（关闭）** | 保证本次改动是纯新增、零风险合入；上线灰度节奏由后续接入 orchestrator 时决定 |
| LLM 核查失败时的行为 | **降级直出 + 显式标注**，而非静默吞掉或报错崩溃 | 与全项目「优雅降级」惯例一致；显式标注防止把「未核实候选」误当「已核实结论」用在生产判定上 |
| 双路共识候选是否升级 severity | **仅在降级路径**做经验性升级；LLM 核查路径完全交给 LLM 判定 | 融合层本身不应臆断合规结论，经验规则只是「LLM 不可用时的兜底」，不能喧宾夺主 |
| harness 是否本轮落地为代码 | **仅设计骨架写入本文档**，未新建 `core/ai_review/eval/` | 超出本轮严格文件边界；真实结论仍待评测集，先落地骨架设计不影响后续实现 |

---

## 8. 完成情况自查（对照任务目标）

1. ✅ 设计 + 脚手架 GraphRAG 融合召回层：`core/ai_review/graphrag/{types,fusion}.py`，
   不改 KG/RAG/orchestrator 本体，灰度开关默认关闭=恒等回到现并行。
2. ✅ 合规审查评价标准判定建议：见 §3（precision/recall/F1、义务等级混淆矩阵、
   条文引用命中率、误报/漏报细化口径、三方法对比方法论）。
3. ✅ harness 骨架：见 §4（复用 `core/model3d/eval` 度量范式的设计，本轮未新建
   代码文件，超出严格文件边界）；ollama gemma4 临时测试接法见 §6（含缺种子时的
   优雅降级说明）。
4. ⏳ 真实评测集（精度/召回结论）：见 §5，本轮最大缺口，与 D-18 原计划「缺什么」
   一致，需要后续专家标注或人审飞轮滚雪球积累。
