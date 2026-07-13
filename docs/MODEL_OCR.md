# 图纸全文 OCR（核心功能）

> 状态：**真实推理已落地并验证**（2026-07-13，上海大歌剧院真实竣工图实测）。
> aarch64 容器经 RapidOCR(onnxruntime) 后端 + 大图分块识别跑通：
> 「10围护体剖面图(一)」提取 **261 个 token，13 个标高候选全部置信 0.96~1.00**
>（-31.900 ~ +16.200，与基坑围护剖面性质吻合），31 个尺寸、5 个楼层名；耗时 22.5s/图。
> 模块 `core/model3d/ocr`，34 单测全绿；下游接入缝就绪（`consume.py`），wiring 到
> section-z/拼接/语义为下一步。

## 为什么需要它

CAD 导出 PDF 的正文标注（标高 ±0.000、轴号、构件名、尺寸、说明）绝大多数是
**矢量绘制的字形，不是可提取文本**——`page.get_text` 只能拿到标题栏。而这些文字对
模型完整性与图纸拼接理解至关重要：

- **标高** → 楼层真实层高（补 section-z 自动打底，人工再校正）
- **轴号** → 跨图拼接配准的锚点
- **构件名/房间名/图名** → 语义树

所以要把图纸**高 DPI 栅格化后跑 OCR**，产出带坐标 + 置信度的结构化文本。

## 架构（对齐 `spotting/` 的可插拔契约）

```
run_ocr(bytes) ──► _render_first_page (fitz 高DPI栅格化)
                     │
                     ▼
                 OcrBackend.recognize  ← PaddleOcrBackend(懒加载,降级) / MockOcrBackend(离线)
                     │  (text, bbox_px, conf)
                     ▼
                 像素→页面点换算 (× 72/dpi)
                     │
                     ▼
                 classify_text  ──►  TextToken{text,bbox,confidence,kind,value}
                                        kind ∈ elevation/axis/dimension/level_name/room_name/note/title/other
```

| 文件 | 职责 |
|------|------|
| `types.py` | `TextToken` / `OcrResult` / `OcrBackend` 协议 |
| `classify.py` | 确定性文本分类 + 标高/尺寸数值解析（纯函数） |
| `paddle_backend.py` | PaddleOCR 中文识别（懒加载 + 优雅降级 + 单例缓存，兼容 2.x/3.x 双 API） |
| `rapid_backend.py` | RapidOCR（PP-OCR 模型的 onnxruntime 移植）——paddle 在 aarch64 崩溃时的稳定回退 |
| `mock_backend.py` | 离线确定性桩（预置文本框，让整链路在 CI 无重依赖跑通） |
| `service.py` | 编排：渲染→识别→坐标换算→分类；后端有序回退 paddle→rapid→none |
| `consume.py` | 下游馈入：`elevation_candidates` / `axis_anchors` / `space_labels` |

### aarch64 已知问题（2026-07 实测）

paddlepaddle **3.0/3.1/3.2 原生推理引擎在 linux/aarch64 容器（Apple Silicon Docker）构造
predictor 时 SIGSEGV**（崩于 C++ `PreparePirProgram/SaveOrLoadPirParameters`，
`FLAGS_enable_pir_api=0`/`enable_mkldnn=False` 均无效）。这是**进程级崩溃，try/except
拦不住**，因此无法自动探测——aarch64 部署须设 `CAD_OCR_DISABLE_PADDLE=1` 显式禁用
paddle，service 自动回退 RapidOCR（同源 PP-OCR 模型精度，onnxruntime 推理稳定）。

## 大图分块识别（正文小字的关键）

工程图多为 A0/A1，200dpi 渲染近万像素。OCR 检测器默认把长边缩到 ~1k px，
**正文小字（标高/轴号/尺寸）缩到 1-2px 全部丢失，只有标题栏大字幸存**
（整图版实测仅 26 token、0 标高）。service 对超过 2000px 的图自动切
1600px 重叠块（overlap 200px）逐块识别，坐标平移回全图后按 IoU≥0.5 去重
（保留高置信者）。分块版实测 261 token、13 标高（提升 10 倍）。

## 真实图实测（上海大歌剧院，RapidOCR + 分块，2026-07-13）

| 图纸 | token | 标高候选 | 尺寸 | 楼层名 | 耗时 |
|------|-------|---------|------|--------|------|
| 10围护体剖面图(一) | 261 | **13 个（置信 0.96~1.00）** | 31 | 5（含「首层」）| 22.5s |
| 05 第二阶段工况平面图 | 142 | 0（平面图本无标高，符合预期）| 0 | — | 21.1s |

剖面图标高实录：`-31.900 / -24.800 / -23.200 / -9.300 / -5.500 / -2.350 / -2.050 /
-1.050 / -1.000 / -0.840 / +5.500 / +9.300 / +16.200` —— 基坑围护剖面地下标高为主，
与图纸性质完全吻合。标题栏文本（设计院名称/证书编号/工程编号）置信 0.9~1.0 全对。
已知弱项：轴号（圆圈内单字符）召回低，后续可对轴网区域定向增强；个别手写体
中文有错字（置信门槛可挡住大部分）。

## 关键纪律：置信门槛 + 人工复核

**读错比缺失更糟。** `run_ocr(min_confidence=…)` 与 `consume.*(min_confidence=0.6)` 双重把关，
低置信 token 不进自动几何管线，仅作人工复核候选。默认门槛 0.6，标高/轴号影响配准，
可按专业调高。

## 用法

```bash
# CLI（离线可跑，无 paddle 则 backend=none 优雅降级）
python scripts/model3d/ocr_drawing.py 图纸.pdf --dpi 200 --min-conf 0.6 --json

# 代码
from core.model3d.ocr import run_ocr, elevation_candidates
result = run_ocr(pdf_bytes, "pdf", dpi=200)
elevs = elevation_candidates(result)   # [{value_m, center, bbox, confidence, text}, ...]
```

## 启用真实识别（部署）

1. `pip install -r requirements-ocr.txt`（独立 extra，勿并入主 requirements）。
2. **aarch64 环境**（Apple Silicon Docker）：设 `CAD_OCR_DISABLE_PADDLE=1`
   （paddle 原生引擎该平台 SIGSEGV，见上文），只装 `rapidocr-onnxruntime` 一行即可
   （模型内置于 wheel，无需联网下权重）。x86_64 环境两者都装则优先 paddle。
3. 网络不稳时容器内安装用清华源 + `--resume-retries 8` 断点续传。
4. 本地 dev 容器装好后 `docker commit cad_api cad-api:local` 烘焙持久化（防 recreate 丢包）。

## 下游接入缝（待逐步 wiring）

`consume.py` 三个函数即为接入点，默认不改变现有行为：

- `elevation_candidates` → `services/section_z_recovery` / `model_story`（自动打底标高，
  与已有人工层高录入通道 migration 025 互补：OCR 打底 → 人工校正）
- `axis_anchors` → `core/model3d/grid_anchor_extractor` / `cross_view_registration`
- `space_labels` → `services/model_semantics`

## 已知边界

- 多数真实图为基坑/围护剖面（地下标高），地上层剖面稀少——地上层高仍主要靠
  人工录入通道兜底。
- OCR 对细线稀疏的工程图有漏检；tesseract 实测不胜任，必须 PaddleOCR。
- 当前仅识别首页；多页图纸逐页 OCR 为后续增量。
