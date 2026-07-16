# Phase E4-2 VLM 读图评测报告

> 2026-07-16 ｜ 目标:VLM 读图(判专业/读标高/识构件)本地 vs 远程精度对比 + 路由推荐

## 结论(现状)

**VLM 代码路径完整、39 mock 测试全绿;但真实精度测试受环境阻塞——当前无可用视觉模型。**

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
