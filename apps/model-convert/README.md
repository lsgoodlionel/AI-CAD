# model-convert — IFC → Fragments 离线转换器

Phase A 任务 **A-04**。把合规 `.ifc` 转成 That Open **Fragments** 二进制（`.frag`），
供前端 Fragments 加载器高性能渲染（2GB IFC ≈ 80MB frag，百万构件级 60fps）。

这是一个**隔离的 Node 包**，独立于 `apps/web` 前端构建，只在离线转换（Celery 子进程）时使用。

## 用法

```bash
npm install
node ifc_to_fragments.mjs <input.ifc> <output.frag>
```

示例：

```bash
node ifc_to_fragments.mjs /tmp/model_poc.ifc /tmp/model_poc.frag
# -> OK /tmp/model_poc.frag 4769 bytes
```

成功时 stdout 打印 `OK <output> <bytes> bytes` 并以退出码 `0` 结束。

### 退出码

| 码 | 含义 |
|----|------|
| 0 | 成功，`.frag` 已写出 |
| 1 | 参数错误 / 用法错误 |
| 2 | 转换失败（输入不可读、解析错误、空输出等），stderr 输出原因 |

## 实现要点

- 使用 `@thatopen/fragments` 的 `IfcImporter`（内部用 `web-ifc` 解析 IFC）。
- `importer.process({ bytes })` 返回 Fragments 二进制（`Uint8Array`）。
- **web-ifc WASM 路径**：指向本包 `node_modules/web-ifc/`（本地托管，
  `{ absolute: true, path: <abs>/node_modules/web-ifc/ }`），**不依赖外部 CDN**，
  离线可用、符合 CSP。

## 依赖与许可

| 包 | 版本 | 许可证 |
|----|------|--------|
| `@thatopen/fragments` | 3.4.6 | MIT |
| `three`（peer） | 0.185.0 | MIT |
| `web-ifc` | 0.0.77 | **MPL-2.0**（弱 copyleft，按文件级；商用通常可接受，须纳入法务核验清单） |

> ⚠️ **许可提示**：`web-ifc` 为 **MPL-2.0**（非 MIT）。MPL-2.0 是文件级弱 copyleft：
> 仅对被修改的 MPL 源文件有回馈义务，不感染调用方。本包仅**运行时依赖**其
> WASM/JS，未修改其源码，故对本仓库其余代码无许可影响。仍建议登记进法务核验清单。

## 与后端集成

由 `apps/api/services/fragments_convert.py` 以 `subprocess` 方式调用本脚本。
本轮（A-04）仅交付独立转换器 + Python 封装，**未接线进 `tasks/model_build.py`**
（依赖并行的 A-03，留作后续）。
