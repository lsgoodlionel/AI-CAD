# D-20 本地 Mac 训练 CADTransformer — 可行性实测与路线

> 2026-07-15 | 泳道5 · D-20 | 起因：审视「D-20 符号 spotting 微调是否必须 GPU / 能否本地 Mac」

## 结论（实测，修正此前判断）

**本地 Mac（arm64）训练 CADTransformer 技术上可行**——此前「必须 GPU / Mac 训不了」的判断**错误**，真因是**用错 DGL 版本**：

| DGL 版本 | arm64 Mac 结果 | 原因 |
|---|---|---|
| dgl 2.2.x（最新） | ❌ 导入失败 | 缺 C++ `libgraphbolt_pytorch_*.dylib`（Mac wheel 不含预编译库）|
| dgl 2.1.0 | ❌ 导入失败 | graphbolt → `torchdata.datapipes` 不兼容 |
| **dgl 1.1.3（graphbolt 之前）** | ✅ **跑通** | C++ 后端在 arm64 Mac 加载成功 |

**实测证据**（隔离 venv `research/cadtx/.venv`，torch 2.8.0）：
```
dgl 1.1.3 | torch 2.8.0 | mps True
✓ DGL GraphConv + MultiheadAttention + backward on Mac-CPU 全通！
```
即 CADTransformer 的核心训练原语（图卷积 + transformer + 反向传播）**在 Mac CPU 全部可运行**。

## 「MPS 不稳」的真相

- DGL 的**图操作没有 MPS 后端**（只有 CPU/CUDA）→ 图部分在 Mac 跑 **CPU**（非 MPS 崩溃，是根本无 MPS 支持）。
- CNN 骨干（HRNet）、transformer 层是纯 torch，**可上 MPS**。
- 此前 YOLO 的「MPS 崩 torch2.12 TAL 索引」是另一回事（torch 版本+算子问题）；CADTransformer 的约束是 DGL-无-MPS + DGL 2.x-Mac-broken。

## 可行路线

### 隔离环境（关键：绝不污染 app 的 apps/api/.venv）
```bash
python3 -m venv research/cadtx/.venv          # 独立 venv（research/cadtx/ 已 gitignore）
research/cadtx/.venv/bin/pip install torch \
  'dgl==1.1.3' packaging pandas scipy networkx tqdm \
  -i https://pypi.tuna.tsinghua.edu.cn/simple   # dgl 钉死 1.1.x（非 2.x）
```

### 训练分工（Mac）
- **图卷积/图 transformer（DGL）**：CPU（无 MPS，慢）
- **HRNet CNN 骨干、注意力层（torch）**：可 `.to('mps')` 加速
- **数据**：FloorPlanCAD（DXF/SVG）；自建中文域数据（C-05~C-07 已规范）

### 现实速度评估
- **smoke（微型样本 + 几步）**：Mac 秒~分钟级，用于**证明微调环端到端跑通**（本文档目标）。
- **真实全量微调**：图操作困在 CPU → 每 epoch 很慢，FloorPlanCAD 全量在 Mac **以天计**。可行但不划算。
- **建议**：Mac 用于**开发/调试训练脚本 + 小样过拟合验证正确性**；真实全量微调仍上 **Linux CUDA GPU**（DGL CUDA 后端 + GPU 图操作，快数量级）。

## GPU-free 的识别价值替代（已实施）

稠密符号 spotting 的**领域微调**慢（Mac-CPU）；但符号/语义**识别价值**的一大块可用 **远程 VLM 推理**拿到，完全不需训练/GPU：
- `core/model3d/vlm_read/`（泳道5 item2，已提交）：远程 qwen3.5-vision 判专业/读标高/识构件，实测读懂真实结构剖面图。
- 即：**读图/语义 → VLM 推理（无 GPU）；稠密 spotting 精确定位 → CADTransformer（微调需 GPU，推理用预训练权重可 CPU）**。

## 实施进展（实测）

- ✅ clone CADTransformer(MIT) 到 `research/cadtx/CADTransformer`（gitignore）——含 `train_cad_ddp.py`/`models/{model,seg_hrnet,vit}.py`。
- ✅ **依赖链在 Mac 加载**：dgl(1.1.3)/timm/einops/cv2 全 OK；**`seg_hrnet`(HRNet 骨干) 在 Mac 导入 OK**。
- ⚠️ **唯一断点**：`vit.py` 用旧 timm API `_init_vit_weights`（新 timm 已移除）→ **timm 版本漂移**（非 Mac 问题）。修法：`pip install 'timm==0.4.12'`（CADTransformer 同期版）或补丁该 import。
- ⚠️ 训练脚本 `train_cad_ddp.py` 是 **CUDA 硬编码 + DDP**（`torch.cuda.set_device`/`.cuda()`）→ 上 Mac 需把 device 改 `cpu`/`mps`、去 DDP（单机）。

### 最终结论
**本地 Mac 训练 CADTransformer 的所有阻碍都是「版本钉不对」，不是 Mac/MPS/GPU 的根本限制**：
1. `dgl` 必须 **1.1.x**（2.x 的 graphbolt 在 arm64 Mac 破损）
2. `timm` 必须 **CADTransformer 同期版**（如 0.4.12，新版移除了 `_init_vit_weights`）
3. 训练脚本 device 补丁 cuda→cpu/mps、去 DDP

钉对后：代码加载、训练原语（图卷积+transformer+反传）在 Mac CPU 跑通（图操作无 MPS 走 CPU、CNN 可 MPS）。**Mac 适合开发/调试/小样过拟合验证；真实全量微调因图操作困 CPU 而慢，划算做法仍是 Linux CUDA。**

### 待做（专项 smoke）
1. pin timm==0.4.12 修 vit.py；device 补丁 cuda→cpu/mps
2. 下 HRNet/ViT 预训练权重（网络慢，需镜像/耐心）
3. 合成/微型数据 → 跑前向+反向+一步 optimizer.step，证明真实 CADTransformer 微调环在 Mac 端到端收敛
4. 评估 Mac 小规模微调 vs Linux GPU 全量的速度差

---

## ✅ 训练环 smoke 实测跑通（2026-07-15，Apple M2 / 8 核）

**结论：真实 CADTransformer（91.4M 参数，HRNet-W48 骨干 + 图 transformer 注意力）在本地 Mac 前向+反向+optimizer.step 端到端跑通，loss 单调下降、全程有限、不崩。** 证明本地 Mac 微调训练可行落地（非仅理论）。

### 实测证据（合成微型样本过拟合，`research/cadtx/smoke_train.py`）

| 设备 | 点数 | loss 序列（8 步） | 稳态每步 | 判定 |
|---|---|---|---|---|
| **CPU** | 128 | 4.258 → 3.116 → 2.258 → 1.583 → 1.043 → 0.665 → 0.420 → **0.269** | **682 ms** | ✅ 有限·单调降 |
| **MPS**（+fallback） | 128 | 4.258 → 3.110 → … → **0.278** | **383 ms** | ✅ 有限·单调降 |
| CPU | 1000（真实图规模） | 4.228 → 3.613 → 3.253 → 2.965 | 1131 ms | ✅ 降 |
| MPS（+fallback） | 1000 | 4.228 → 3.610 → 3.251 → 2.981 | 842 ms | ✅ 降 |

- **MPS 比 CPU 快 ~1.8x**（128 点 383 vs 682 ms；1000 点 842 vs 1131 ms），即便有一个算子被迫回退 CPU（见下）。
- 首步含编译预热（MPS ~2.5–4s），稳态后稳定。

### 修好的坑（全是版本漂移 + 少量 device 补丁，均在 gitignored `research/cadtx/` 副本内）

1. **timm**：`pip install 'timm==0.4.12'`（新版移除了 `_init_vit_weights`/`checkpoint_filter_fn`/`default_cfgs`/`build_model_with_cfg` 旧 API）→ **修好 vit.py 唯一断点**。
2. **numpy 2.0 别名**：`seg_hrnet.py:310` 用了已移除的 `np.int` → 改 `int`。
3. **device 补丁**：`vit.py` 去掉硬编码 `ViT.cuda()`；`model.py` 的 `get_vit(pretrained=True)` 改为读 `cfg.vit_pretrained`（默认 False，smoke 不联网下权重）；device 由外层 `model.to(device)` 统一管。
4. **无预训练权重降级**：`seg_hrnet.init_weights` 文件缺失原本 `raise`，改为随机初始化 + warn（smoke 非精度目标）。HRNet/ViT 预训练权重**未下载**——smoke 全随机初始化即证明训练环跑通。
5. **MPS 算子缺口**：`aten::grid_sampler_2d_backward`（vert-align 的 `grid_sample` 反向）**MPS 未实现** → `PYTORCH_ENABLE_MPS_FALLBACK=1` 让该单算子回退 CPU（其余仍在 MPS）。这是 torch MPS 覆盖缺口，非根本阻碍。

### 意外发现：此 repo 的模型前向**根本不用 DGL**

全仓库 `grep dgl` 为空——CADTransformer 的图注意力是**纯 torch**（`index_points`/`gather`/`softmax` 实现 KNN 稀疏注意力），DGL 只在其数据预处理链。故 smoke 训练**无需 DGL**，整模型可整体上 CPU 或 MPS。此前「DGL 无 MPS 卡训练」是针对通用图训练路线，对本 repo 的模型前向不成立。

### 环境锁定（`research/cadtx/.venv`，Python 3.9.6）

```
torch==2.8.0  torchvision==0.23.0  timm==0.4.12  numpy==2.0.2
yacs==0.1.8   einops==0.8.2  scikit-learn==1.6.1  matplotlib==3.9.4  opencv-python-headless
# dgl==1.1.3 仍在环境里但本 smoke 未用到
```

### Mac vs GPU 全量微调速度差评估

- **smoke（本目标）**：Mac 秒级跑通，完全够用于**开发/调试训练脚本 + 小样过拟合验证正确性**。
- **单步实测**：1000 图元/图（≈ 真实图纸），M2 上约 **0.84s（MPS）/ 1.13s（CPU）每步**（batch=1，随机权重、合成数据；真实数据 dataloader/增广会再加时间）。
- **全量微调外推**：FloorPlanCAD 训练集 ~ 数千张图 × 每图多步 × 数百 epoch。以每步 ~1s、单卡 batch=1 估：**单 epoch 数千秒（近小时级），全程以天计**。
  - GPU（如 A100/3090，DGL/torch CUDA + 更大 batch + 无 grid_sample 回退）通常**快一个数量级以上**（每步几十 ms，且可 batch 并行）。粗估 **Mac 全量微调比单卡 GPU 慢 ~10–30x**（受制于：无大 batch 并行、grid_sample 反向回退 CPU、M2 算力 vs 独显）。
- **建议不变**：**Mac 做训练脚本开发 + 小样过拟合正确性验证（本 smoke 已证）；真实全量微调（C-09）仍上 Linux CUDA GPU** 才划算。

### 复现命令

```bash
research/cadtx/.venv/bin/python research/cadtx/smoke_train.py cpu                 # CPU
PYTORCH_ENABLE_MPS_FALLBACK=1 research/cadtx/.venv/bin/python research/cadtx/smoke_train.py mps   # MPS
# 可选 --points 1000 --steps 4 调规模
```

