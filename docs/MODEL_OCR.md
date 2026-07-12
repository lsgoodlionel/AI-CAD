# 图纸全文 OCR（核心功能）

> 状态：**基座落地 + 离线可测**（`core/model3d/ocr`，24 单测全绿）。真实中文识别待带
> PaddleOCR 权重的部署环境验证；下游楼层/标高·轴号拼接·语义的接入缝已就绪（`consume.py`）。

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
| `paddle_backend.py` | PaddleOCR 中文识别（懒加载 + 优雅降级 + 单例缓存） |
| `mock_backend.py` | 离线确定性桩（预置文本框，让整链路在 CI 无重依赖跑通） |
| `service.py` | 编排：渲染→识别→坐标换算→分类；`run_ocr`(字节) / `ocr_drawing`(file_key) |
| `consume.py` | 下游馈入：`elevation_candidates` / `axis_anchors` / `space_labels` |

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

1. 放开 `requirements.txt` 里 `paddlepaddle` + `paddleocr`（镜像约 +数百 MB，正式 build）。
2. 首次运行下载中文模型权重（需联网或预置权重）。
3. 在几张真实工程图上验准确率（标高/轴号命中率）再全量接入。

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
