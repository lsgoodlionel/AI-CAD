# CADTransformer 推理封装（C-08 PoC 基线）

符号 spotting 短期首选后端。用官方 **CADTransformer**（VITA-Group）+ 官方权重，
把 `DXF →（C-02 预处理）SVG/图元 JSON → spotting` 全链路跑通，在 FloorPlanCAD 上
复现论文量级指标（PQ≈68.5），确立**可运行基线**。

> 合规：CADTransformer 许可 **MIT**（可商用，见 `docs/PHASE_C_LICENSE_AUDIT.md §3.1`）。
> 分发时须保留上游版权声明与 LICENSE 副本。**本目录严禁引入 SymPoint**（非商用，CI license 门禁阻断）。

---

## 1. 目录结构与职责

| 文件 | 职责 | 依赖 torch？ |
| --- | --- | --- |
| `adapter.py` | `PrimitiveDoc` ↔ CADTransformer 输入/输出的**纯函数**适配 | ❌ 否，可完整单测 |
| `backend.py` | `CADTransformerBackend`（实现 `SpottingBackend` Protocol），懒加载推理 | ✅ 仅真实推理路径 |
| `Dockerfile.fragment` | GPU 推理镜像片段（torch/dgl 版本锁定），**独立于主 Dockerfile** | — |
| `README.md` | 本文件 | — |

依赖锁定在 `apps/api/requirements-spotting.txt`（**独立 extra，不并入主 `requirements.txt`**）。

---

## 2. 离线降级（本仓库默认状态）

本仓库 CI / 本地环境**无 GPU、无官方权重、未安装 dgl/torch-geometric**，因此：

- `CADTransformerBackend.is_available()` → **False**（依赖或权重缺任一即降级）。
- `CADTransformerBackend.spot(doc)` → 返回空 `SpottingResult(backend="cadtransformer", warnings=("...降级",))`，
  **绝不硬失败、绝不在 CI 崩**。上层 spotting 服务（C-12）据此回落到 mock 后端。
- `adapter.py` 不依赖任何深度学习库，输入适配与输出解析在 CI 下完整可测。

这是**诚实的脚手架**：真实推理接线（模型前向 `backend._infer`）需在具备依赖与权重的
GPU 环境完成，见下文。

---

## 3. 权重获取（MIT，官方）

- 代码与权重：<https://github.com/VITA-Group/CADTransformer>（MIT）。
- 官方权重在 **FloorPlanCAD** 建筑平面图上训练，覆盖门/窗/墙/家具/洁具/楼梯等类目。
- 下载后置于任意路径，通过环境变量注入（**不硬编码、不进 git**）：

```bash
export CADTRANSFORMER_WEIGHTS=/models/cadtransformer/floorplancad.pth
export CADTRANSFORMER_DEVICE=cuda:0   # 缺省 cpu
```

> ⚠️ 官方权重仅覆盖建筑平面图；本平台结构/机电/装修专业域**零样本表现会很差（预期内）**，
> 由 C-09 微调解决。C-08 只验证管线打通。

---

## 4. GPU 运行方式

### 4.1 安装推理依赖（独立 extra）

```bash
cd apps/api
source .venv/bin/activate
# GPU 环境按需安装（版本锁定见 requirements-spotting.txt 与 Dockerfile.fragment）
pip install -r requirements-spotting.txt
```

### 4.2 就绪自检

```python
from core.model3d.spotting.cadtransformer import CADTransformerBackend
b = CADTransformerBackend()          # 从环境变量读取权重路径/设备
print(b.is_available())              # 依赖 + 权重齐全 → True
```

### 4.3 全链路推理（DXF → 候选）

```python
from core.model3d.preprocess import preprocess_drawing
from core.model3d.spotting.cadtransformer import CADTransformerBackend

pre = preprocess_drawing(open("plan.dxf", "rb").read(), "dxf")  # C-02 预处理
backend = CADTransformerBackend()
result = backend.spot(pre.doc)       # → SpottingResult(candidates=..., backend="cadtransformer")
for c in result.candidates:
    print(c.category, c.confidence, c.bbox)
```

`_infer` / `_to_predictions` 是真实前向与输出解析的接线点：图元序列 → dgl 图 →
CADTransformer 前向 → 逐节点 logits + 实例分组头 → `NodePrediction` 列表 →
`adapter.parse_predictions` 聚合为 `SymbolCandidate`。

---

## 5. FloorPlanCAD PQ≈68.5 复现步骤

1. 克隆上游仓库并按其 README 准备 FloorPlanCAD 数据集与官方权重。
2. 在 GPU 机上安装 `requirements-spotting.txt` 锁定的 torch/dgl/torch-geometric。
3. 按上游评测脚本在 FloorPlanCAD 验证子集上跑 panoptic 评测，得到 PQ。
4. **验收标准（C-08）**：复现 PQ 落在论文 **±3** 内（≈65.5~71.5）；再用 C-02 预处理的
   自有图纸跑出符号候选输出（未微调可接受低分）。
5. 记录：数据集版本、权重 checksum、torch/dgl/CUDA 版本、PQ 数值，回填里程碑 M0 报告。

> 本环境无 GPU，无法在此复现 PQ；上表流程为在具备条件的环境执行的操作手册。

---

## 6. 依赖 / 版本锁定说明

- **锁在哪**：`apps/api/requirements-spotting.txt`（独立 extra）+ 本目录 `Dockerfile.fragment`。
- **为何独立**：CADTransformer 依赖较老（特定 torch/dgl 组合），并入主 `requirements.txt`
  会拖累整个 API 服务的依赖解析（**环境地狱风险**）。故隔离为按需安装的推理 extra，
  API 主服务不依赖它即可运行（缺失时优雅降级到 mock）。
- **主镜像不改**：GPU 推理镜像用 `Dockerfile.fragment` 单独构建，不触碰主 `Dockerfile`。
