# Phase E4-2 VLM 读图评测报告

> 2026-07-16 ｜ 目标:VLM 读图(判专业)真实精度 + 路由推荐
> 模型:远程 qwen3.5:latest(`https://openclaw.lsgood.cn`,Ollama 网关经 Cloudflare)

## 结论(已实测)

**远程 qwen3.5 VLM 真实可用且能看图判专业:清晰三专业(建筑/结构/机电)准确率约
80%,含模糊兜底类 general 时整体 63%。VLM 适合作专业「提示/兜底」,非权威判定。**

### 判专业实测(20 图抽样,金标 = `drawings.discipline`)

| 专业 | 命中/样本 | 说明 |
|------|----------|------|
| 建筑 architecture | **5/5** | 全对(观众厅/楼梯详图/设计说明) |
| 机电 mep | 4/5 | 1 错(夹层电气监控总图→判建筑,平面像建筑) |
| 结构 structure | 3/5 | 2 错(钻孔灌注桩说明/降压井平面→判机电,均边界模糊,降压井实涉水) |
| 其他 general | **0/4** | 总说明/围护桩/工况/总平图——模糊兜底类,VLM 难映射 |
| **合计** | **12/19 = 63%** | 排除 general 兜底类:**12/15 = 80%** |

**关键洞察**:
- qwen3.5 确能读图(thinking 里正确识别「桩基平面布置图」等),**建筑/结构/机电清晰专业 ~80%**。
- `general/其他` 是催化剂式误差源:它是无清晰视觉专业信号的兜底类(说明书/总平/工况),
  人工也难判——不宜用它苛求 VLM。
- 结构的错例(灌注桩/降压井)是真实边界,非模型瞎判。
- 1 例远程瞬断(Remote end closed),网关偶发。

### 工程接线修复(生产可用)

- `core/llm/providers/ollama_provider.py`:httpx 默认无浏览器 UA → 被 Cloudflare 前置
  的远程网关 403。**加常规 User-Agent header**(合法 API 客户端标识,本地直连无副作用),
  app 的 ModelRouter 现可正常调远程 qwen3.5。

### 路由推荐

- **判专业**:文件名/目录派生的 discipline(现有确定性)更可靠;VLM 仅作**缺文件名信息
  时的兜底提示**,或与确定性结果**冲突时提示人审**,不覆盖确定性判定。
- **读标高**:VLM 仅结构专业 + 主源(矢量/OCR)标高不足才调(`_needs_vlm_elevation`),
  维持「绝不虚高」;OCR 标高已 97% 高置信(E4-1),VLM 是补充非主力。
- primary/fallback 配置可经 `engine_model_configs` 热切换(不改码)。

## 原(阻塞)记录 — 已解决

早期健康检查显示无可用视觉模型(DashScope 无 key / qwen2.5vl 未 pull / gemma4 非视觉),
后经远程 qwen3.5 + UA 修复解锁真实测试。VLM 代码路径 39 mock 测试全绿。

## 连通性矩阵(健康看板实测)

| 提供商 | 视觉模型 | 连通 | 可用于真实测试 |
|--------|---------|------|--------------|
| 阿里云 DashScope | qwen-vl-max(primary)| ❌ | 无 API key |
| Ollama 本地 | qwen2.5vl:7b(fallback,配置)| base_url 可达 | ❌ **模型未 pull** |
| Ollama 本地 | gemma4:latest(实际已装)| ✅ | ❌ **非视觉**(images 参数 400) |
| Ollama 远程 | qwen3.5 | ✅ | 待确认视觉能力 |
| Claude / OpenAI / DeepSeek | — | ❌ | 无 key |

**净结论**:配置的两个 VLM(qwen-vl-max / qwen2.5vl:7b)一个缺 key、一个没 pull;
本地实装的 gemma4 非视觉。**跑不了真实读图精度。**

## 已验证(代码/逻辑层)

- `core/model3d/vlm_read/*`(ollama_vlm / parse / types)+ `read_drawing_vlm`:
  判专业/读标高/识构件的解析、置信、`绝不出计数/坐标/尺寸` 铁律——**39 单测全绿**
  (`tests/test_vlm_read.py`)。
- 建模 section-z 的 VLM 标高兜底(仅结构专业 + 主源标高不足才调,`_needs_vlm_elevation`)
  逻辑测试通过(`tests/test_model_builder_vlm_section_z.py`)。
- 即:**VLM 一旦有可用模型,链路即可跑;缺的是模型,不是代码。**

## 跑真实测试的前置(二选一)

1. **pull 本地模型**:`ollama pull qwen2.5vl:7b`(约 6GB),然后按下方评测方案跑。
2. **配 DashScope key**:设 `DASHSCOPE_API_KEY` 环境变量(qwen-vl-max 云端)。

## 评测方案(模型就绪后执行)

- **判专业**:抽样歌剧院 100 图跑 VLM 判专业,**金标 = `drawings.discipline`**(已知),
  算准确率(目标 ≥95%);本地 qwen2.5vl vs 远程/云端对比。
- **读标高**:与 E4-1 OCR 标高金标交叉,算 VLM 标高命中率(维持「绝不虚高」)。
- **成本/延迟**:经 ModelRouter 日志(健康看板 E0-1 修复后可视化 daily 成本/断路器)。
- **路由推荐**:据准确率/延迟/成本定 primary/fallback,写回 `engine_model_configs`(热切换,不改码)。

## 边界

- 本报告如实记录「无可用视觉模型」的环境现状;VLM 真实准确率一贯是**部署期任务**
  (CLAUDE.md / MODEL_MANUAL 已声明),非本地可完成。代码就绪,待模型/密钥到位即跑。
