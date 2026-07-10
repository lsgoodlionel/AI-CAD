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

## 冒烟 fixture

`fixtures/sample_building.ifc` 是一份最小合规 IFC4（单面挤出墙体），供 CI 与
容器冒烟验证转换链路：

```bash
node ifc_to_fragments.mjs fixtures/sample_building.ifc /tmp/smoke.frag
# -> OK /tmp/smoke.frag 1175 bytes
```

CI `model-convert` job 每次跑 `npm ci` + `node --check` + 上述冒烟；`docker-images`
job 另在构建好的 `cad-api` 镜像内跑一次同样转换，确保镜像确实带齐 Node 与依赖。

## 容器打包（cad-api 镜像）

本包**随 `cad-api` 镜像一同打包**（Celery worker 与 API 共用该镜像）：

- 构建上下文为 `apps/`（而非 `apps/api`），Dockerfile 把本目录 `COPY` 到
  `/opt/model-convert` 并在其中 `npm ci --omit=dev`；镜像另装 Node 20。
- `apps/api/services/fragments_convert.py` 通过 `MODEL_CONVERT_DIR=/opt/model-convert`
  环境变量（镜像内已 `ENV` 预置）定位本目录与 `node_modules`；本地开发无需设置，
  回退源码树相对路径。

> 未装 Node / 未打包本目录时，`fragments_convert` 会抛 `FragmentsConversionError`，
> 上层 `model_builder` 优雅降级为 `frag_key=null`（回退 glTF/挤出/贴图）——这正是
> 补齐本项 infra 前 Fragments 渲染无法激活的根因。

## 与后端集成

由 `apps/api/services/fragments_convert.py` 以 `subprocess` 方式调用本脚本，
已由 `services/model_ifc_integration.py`（经 `model_builder.build_scene`）接线进
模型构建链路。
