# D-16 — 离线 OCR 后端评测基座

> 承接 `docs/PHASE_D_LANE5_PLAN.md` D-16 节。评测 CAD 图纸 OCR 的行业惯例
> 是有金标签算 Precision/Recall；但**上海大歌剧院全部现有图纸没有 OCR 人工
> 标注真值**（标注成本高、本轮目标是快速横向比较后端强弱，不是打造标注数据集）。
> 因此本基座提供**两档模式**：有金标签算准确率，无金标签算一致性 + 识别量，
> 两者共用同一套 `run_ocr` 调用 + Markdown 报告框架。

## 代码位置

```
core/model3d/ocr/eval/
├── __init__.py       # 两档模式公共导出
├── metrics.py         # 有金标签度量引擎（容差/精确匹配 + P/R/F1 + 置信标定）
├── harness.py          # 有金标签多后端编排 → OcrComparisonReport
├── unlabeled.py        # 无金标签多后端编排 → UnlabeledComparisonReport（本次新增）
└── report.py            # 两种报告 → Markdown（render_markdown / render_unlabeled_markdown）

core/model3d/ocr/paddleocr_vl_backend.py   # PaddleOCR-VL 适配器 stub（未接线真实推理）
scripts/model3d/ocr_eval.py                 # CLI（--demo / --manifest / --demo-unlabeled / --dir）
tests/test_ocr_eval.py                      # 离线测试（53 例，mock 后端，两档模式全覆盖）
```

## 只评三类 token，理由

评测只覆盖 `elevation`（标高）/ `axis`（轴号）/ `title`+`room_name`（图名·房间名），
因为它们是 `consume.py` 三条下游馈线（`elevation_candidates` / `axis_anchors` /
`space_labels`）真正读取的信号。`dimension` / `note` / `other` 不影响任何下游
决策，评了也不能指导切换后端——评测不做无意义的全量覆盖。

## 两档模式

### 有金标签（`harness.py`）

对有人工标注真值的评测集（未来若做少量精标注样本时用）：

- **标高**：数值容差匹配（默认 ±0.05m，覆盖读数抖动、不掩盖读错整数位）。
- **轴号 / 图名·房间名**：归一化后精确字符串匹配（多重集，允许重复标签各自计数）。
- 贪心按预测置信度降序配对，保证高置信预测优先拿到"最近"的金标签。
- 跨样本聚合：**逐样本分别匹配后按 tp/fp/fn 求和**，不做跨样本拼接匹配——
  拼接会让样本 A 的金标签被样本 B 的预测"误配对"，数值/字符串匹配没有 IoU
  匹配天然的空间局部性兜底，必须按样本隔离。
- **置信标定**（`confidence_calibration`）：识别置信度与"是否命中金标签"的
  点二列相关系数（point-biserial correlation）。越接近 +1 说明"越自信越对"，
  置信度可信、可用来放宽自动化门槛；接近 0 或负值说明置信度不可靠。样本
  不足（<2）或退化（全命中/全未命中/置信度无方差）时返回 `None`——诚实标注
  "不可判定"，不用 0.0 冒充"判定为不相关"。

```python
from core.model3d.ocr.eval import GoldLabels, OcrEvalSample, run_backend_comparison, render_markdown

gold = GoldLabels(elevations=(0.0, 3.6), axes=("1", "A"), titles=("首层平面图",))
sample = OcrEvalSample(file_bytes=pdf_bytes, file_ext="pdf", gold=gold, sample_id="s1")
report = run_backend_comparison([sample], {"rapidocr": RapidOcrBackend()})
print(render_markdown(report))
```

### 无金标签（`unlabeled.py`，本次新增，默认对歌剧院全量图纸用这档）

没有真值时，**不假装有真值**，只产出三类可横向比较后端强弱的信号：

1. **后端间一致性**（`PairwiseAgreement` / `pairwise_agreement`）：同一批图纸
   两个后端各自识别出的三类 token，用与有金标签模式**相同的匹配算法**
   （数值容差 / 归一化字符串）两两比较，输出：
   - `matched`：两后端都识别到、且判定为同一 token 的数量；
   - `only_a` / `only_b`：仅某一后端识别到；
   - `jaccard = matched / (matched + only_a + only_b)`：对称重合率。
     两后端在该类 token 上都是空集时（分母为 0）返回 `None`，不用 1.0 冒充
     "完全一致"。

   **这不是准确率**——两后端一致不代表两者都对（可能一起读错同一处），不
   一致也不代表谁错。用法是横向比较：例如一个新后端与已知较稳定的后端
   一致性高，是"新后端可信"的旁证；一致性低则值得人工抽查裁决谁对。
   一致性计算**只统计双方在该样本均 `available` 的比较**（跳过一方没跑起来
   的样本），避免把"一方压根没跑"误算成"读得不一样"。

2. **识别量与置信分布**（`BackendVolumeMetrics`）：各 kind 的 token 数量 +
   置信度均值/中位数/极值。识别量过低（漏检多）或置信度普遍偏低，都是
   后端偏弱的直接信号，不需要金标签就能判断。

3. **三馈线产出量**（`consume_elevation_count` / `consume_axis_count` /
   `consume_title_count`）：过 `consume.py` 默认置信门槛（0.6）后，真正能
   喂给 section-z / 跨图配准 / 语义树的候选数量——比原始 token 数更贴近
   下游实际价值的"有效识别量"。

```python
from core.model3d.ocr.eval import UnlabeledSample, run_unlabeled_comparison, render_unlabeled_markdown

samples = [UnlabeledSample(file_bytes=pdf_bytes, file_ext="pdf", sample_id="10围护体剖面图")]
backends = {"rapidocr": RapidOcrBackend(), "paddleocr_vl": PaddleOcrVlBackend()}
report = run_unlabeled_comparison(samples, backends)
print(render_unlabeled_markdown(report))
```

聚合口径与有金标签模式一致：每个后端在样本集上只跑一次 `run_ocr`（结果被
该后端参与的所有两两比较复用，避免 N 个后端做 C(N,2) 次两两比较时重复调用）；
逐样本匹配后按 tp/fp/fn 求和再算比率，不做跨样本拼接匹配。

## 金标签格式（有金标签模式，manifest JSON）

```json
{
  "samples": [
    {
      "sample_id": "10围护体剖面图(一)",
      "path": "drawings/10.pdf",
      "gold": {
        "elevations": [-31.9, -24.8, -0.84, 16.2],
        "axes": ["1", "12", "A"],
        "titles": ["10围护体剖面图(一)"]
      }
    }
  ]
}
```

- `path` 相对 manifest 所在目录解析（也接受绝对路径）。
- `elevations`：米制标高值，容许 ±0.05m 容差；标注时按图纸标高字面值（如
  "+16.200"）换算成米填入 `16.2`。
- `axes`：轴号字符串，按图面原样（"1"/"A"/"1/A"），归一化仅去首尾空白。
- `titles`：图名或房间名字符串，二者共用同一评测口径（`title` 类别）。

无金标签模式**不需要** manifest——直接指向图纸目录即可（见下文 CLI 用法）。

## 如何加新后端

1. 在 `core/model3d/ocr/` 下新增文件，实现 `OcrBackend` Protocol
   （`name` 属性 + `is_available()` + `recognize(image_rgb, warnings) -> list[RawBox]`），
   参照 `paddle_backend.py` / `rapid_backend.py` 的懒加载 + 优雅降级范式：
   依赖缺失或推理失败一律 `is_available()` 返回 `False`，`recognize()` 返回
   `[]` + 追加 `warnings`，绝不抛错阻断评测/建模链路。
2. 在 `scripts/model3d/ocr_eval.py` 的 `_BACKEND_REGISTRY` 里注册
   `{"新后端名": NewBackendClass}`。
3. 跑 `--demo` / `--demo-unlabeled` 验证新后端能在报告里正常出现一行
   （即便当前不可用，也应如实显示"不可用"而不是报错退出）。
4. 有金标签评测集就绪后跑 `--manifest`；没有就直接跑 `--dir`（无金标签模式）
   横向比较新后端与现有后端的一致性/识别量/三馈线产出量。

**评测基座本身不修改 `service.py` 的默认回退顺序**（paddle→rapid→mock）——
是否切换默认后端，是看了评测报告后的人工决策，不是自动化的。

## 如何跑歌剧院全量（无金标签模式）

```bash
cd apps/api

# 全部图纸（目录内所有 *.pdf，大小写不敏感，按文件名排序保证可复现）
.venv/bin/python scripts/model3d/ocr_eval.py \
  --dir /path/to/上海大歌剧院/图纸目录 \
  --backends rapidocr,paddleocr_vl \
  --out docs/ocr_eval_sgoh_report.md \
  --json docs/ocr_eval_sgoh_report.json

# 先抽样调试（--limit 避免大批量图纸跑一次要很久；--recursive 递归子目录）
.venv/bin/python scripts/model3d/ocr_eval.py \
  --dir /path/to/上海大歌剧院/图纸目录 --recursive --limit 20 \
  --backends rapidocr
```

- 部署环境按 `docs/MODEL_OCR.md` 装好 `rapidocr-onnxruntime`（aarch64 容器推荐）
  或 `paddleocr`/`paddlepaddle`（x86_64）。
- `paddleocr_vl` 目前恒为"不可用"（真实推理未接线，见下节），会在报告里如实
  标注为不可用，不参与打分。
- 报告分三张表：识别量与置信分布、三馈线产出量、后端间一致性——据此判断
  "哪个后端识别得更多/更自信/与其它后端更一致"，不产出"谁更准"的结论。

## PaddleOCR-VL 真实推理接线 TODO

文件：`core/model3d/ocr/paddleocr_vl_backend.py`，函数 `_load_engine()` 内标注
的 TODO 处（约第 39~63 行）。当前状态：

- `is_available()` 会探测 `import paddleocr` 是否成功；**即使依赖已安装**，
  也显式保持返回 `False`（`_load_failed = True`）——因为 PaddleOCR-VL /
  PP-StructureV3 的真实构造方式（类名、构造参数、输出结构）**本次改动未
  核对**，不臆造签名假装跑通。
- `recognize()` 在不可用时返回 `[]` + 告警，不抛错，与 `paddle_backend.py`
  / `rapid_backend.py` 同一降级范式。

**装好 `paddleocr>=3.x` 后的接线步骤**：

1. 按当时发布的 PaddleOCR-VL / PP-StructureV3 API 文档核实构造方式（预计是
   类似 `PPStructureV3(...)` 或 `PaddleOCRVL(...)` 的 pipeline 类，需要现查
   当时版本的真实签名，不可凭旧版本臆测）。
2. 在 `_load_engine()` 里把探测成功后的两行占位逻辑（`_load_failed = True;
   return None`）替换为：构造引擎实例 → 缓存到 `_engine_singleton` → 返回
   该实例。
3. 在 `PaddleOcrVlBackend.recognize()` 里把 `return []` 替换为：调用引擎推理
   → 按 PP-StructureV3 输出结构解析出 `(text, bbox_pixels, confidence)` 三元组
   列表——参照 `paddle_backend.py` 里 `_construct_paddleocr` /
   `parse_paddle_output` 的"构造 → 推理 → 解析为 RawBox 列表"范式。
4. 接线完成后，先跑 `--demo-unlabeled`（离线，不需要真实依赖）确认没有破坏
   报告格式，再跑 `--dir` 对一小批真实图纸做无金标签横向对比，确认识别量/
   置信分布合理后，再考虑是否需要少量精标注做有金标签评测。
5. **不要**在这一步顺带修改 `service.py` 的默认回退顺序——那是看了评测报告
   后的独立决策（见 `docs/PHASE_D_LANE5_PLAN.md` D-16 节"需确认"②）。

## 关键取舍

- **诚实边界优先于虚假的完整度量**：不可用后端如实标注"不可用"，不用 0 分
  冒充"测过但很差"；置信标定退化时返回 `None`，不用 0.0 冒充"判定为不相关"；
  无金标签模式绝不产出 Precision/Recall/F1（那需要真值才有意义），只产出
  一致性/识别量这类不需要真值的横向比较信号。
- **两档模式复用同一套匹配算法**（`match_elevation_values` / `match_label_set`），
  无金标签模式把"另一个后端的输出"当作有金标签模式里"金标签"的位置——省了
  一套平行的匹配逻辑，也保证两档口径互相可对照。
- **纯 Python，无 numpy/scipy 依赖**（含置信度统计用标准库 `statistics`），
  与 `core/model3d/eval/metrics.py` 的既有原则一致，保证离线 CI 零额外依赖。
- **无金标签模式的一致性≠质量**：报告和本文档反复强调这一点，是刻意的——
  历史上"一致性代替准确率"是评测方法论的常见误用陷阱，必须在数据结构
  （`AgreementMetrics` 字段命名避开 precision/recall）和文档两处都堵住这个误解。
