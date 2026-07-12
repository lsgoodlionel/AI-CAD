# 工程 3D 模型 · 开发交接记录

> 生成日期 2026-07-12（阶段性收尾更新）｜ 分支 `fix/model-3d-quality`(基于 `main`)｜ PR [#11](https://github.com/lsgoodlionel/AI-CAD/pull/11)
> 目的:在新窗口/新机器继续开发。本文件汇总本阶段已完成、未完成、环境认知、以及各待办的诊断与解决方案。

---

## 0. 一分钟接手

- **当前分支** `fix/model-3d-quality`,**已 push,PR [#11](https://github.com/lsgoodlionel/AI-CAD/pull/11) 开着待评审/合并**。本阶段共 10 个 commit(见 §2)。
- **验证项目**:上海大歌剧院 `project_id=9188e163-c684-415e-a4ec-08f208273eff`(2309 张竣工图,模型 v15+)。
- **环境认知已更正**:此前「compose 坏了」是误判——compose v5.x 正常,`docker compose build` 已完整跑通(见 §5 Task 2)。**日常起停/热重载/打包部署统一按 [../infra/DEV.md](../infra/DEV.md)**。改后端优先用 dev override 的 volume 挂载热重载(而非 docker cp);改前端 `npm run build` + `docker cp dist` 进 nginx(或本机 `npm run dev`)。
- **测试账号**:admin/economist/pm/designer 密码统一 `admin123`。前端 `localhost:3002`,API `localhost:8002`(alt-ports `!override` 直接映,`cad_api_proxy` 代理容器多余可删)。

### 本阶段一句话成果
上海大歌剧院实测驱动:**建模致命修复**(渲染/幻影层/标高/sprawl/贴图/红点/未分层)+ **模型页 UX** + **楼层标高人工录入通道** + **Web 帮助中心** + **内存优化 1.1GB→115MB** + **图纸全文 OCR 基座** + **compose 认知更正**。所有前端 63 单测、OCR 24 单测全绿,真实 `docker compose build` 已跑通。

### 后续升级路线(优先级)
1. **OCR 真实推理落地**(高):放开 `requirements` 的 paddle 依赖正式 build → 真实图验准确率 → 逐步 wiring 到 `section_z`/`grid_anchor`/`semantics`(接入缝 `core/model3d/ocr/consume.py` 已就绪)。见 [MODEL_OCR.md](MODEL_OCR.md)。
2. **PR #11 合并**(高):CI 通过后合入 `main`。
3. **建模精度继续**(中):地上层高剖面稀少,仍依赖人工录入通道;跨视图 z 恢复(section-z)可接 OCR 标高候选自动打底。
4. **内存进阶**(低):当前 115MB 已够用;如需更进一步可对 scene JSON 开 gzip、按楼层懒加载。

---

## 1. 本轮已完成(已提交)

### 建模质量修复(评测上海大歌剧院,commit 7be5bbc / 691ac18)
| 项 | 修复前 | 修复后 |
|---|---|---|
| 三维渲染 | 全空白 | 正常显示 13 层堆叠建筑 |
| 楼层数 | 42(幻影层) | 13(真实) |
| 标高范围 | -411.6~441m | -16.8~31.5m |
| 模型尺寸 | 2583×3414m | 397×209m |
| 贴图加载 | 全挂(ERR_NAME_NOT_RESOLVED) | 0 报错 |
| 红点标记 | 23417 | 1500(按严重度封顶) |
| 基座噪声构件 | 2777(未分层) | 0 |

- `model_story.py`:楼层可信范围约束(弱来源不造幻影层)+ 基础/屋面哨兵标高隔离
- `model_builder.py`:标记按严重度封顶;未分层图不注入楼层构件;**离群构件裁剪(柱分位数包络)消除机电比例错误导致的 sprawl**
- `element_recognizer.py`:板识别柱包络兜底;`floor_parser.py`:桩基→基础
- `storage.py`/`config.py`/`docker-compose.yml`:MinIO 公网端点(预签名 URL 浏览器可达)
- 评测报告:`docs/MODEL_EVAL_SGOH.md`

### 工程模型页 UX(commit ed0d440)
- 右栏「语义审查/待人工识别/楼层标高校正」→ 可折叠面板 `CollapsiblePanel`(展开收起+限高滚动)
- 3D 区蓝色边框 + `ModelViewer` 右下角控制按钮(旋转/平移/缩放/复位),鼠标 OrbitControls 保留

### 楼层标高人工录入/校正(commit b737973)
- migration 025 + `model_story_manual.py` 仓储;`build_scene` 加载人工层高,**按累加层高补全真实底标高**(锚定 ±0.000),使层高真正抬升上层
- API `GET/POST /projects/{id}/model/story-heights`;前端 `StoryHeightPanel`(自动参考 + 人工录入)
- 验证:录 2层=6.0m → 3层标高 9.0→10.5m,4层→15.9m ✓

### 更早(commit a188a4b)
- Phase C 签字门禁:清除弱密码 000000 假签 + 密码强度校验 + CI 告警不阻断
- 工程 3D 模型操作手册(用户版 + 管理员版):`docs/MODEL_MANUAL_USER.md` / `MODEL_MANUAL_ADMIN.md`

---

## 2. Commit 清单(分支 fix/model-3d-quality)

```
b737973 feat(model3d): 楼层标高人工录入/校正通道(自动打底→人工校正)
ed0d440 feat(model3d-ui): 工程模型页 UX 优化(折叠面板 + 3D 视角控制 + 边框)
691ac18 fix(model3d): 离群构件裁剪消除横向 sprawl(模型 2583m→397m)
a188a4b chore(phase-c,docs): 清除签字门禁弱密码 + 3D 模型操作手册
7be5bbc fix(model3d): 修复上海大歌剧院建模致命问题(P0/P1/P2)
```
> 另有未提交:`infra/docker-compose.dev.yml`(热重载,§5 Task2)、`infra/api-proxy.conf`(8002 代理)、本文件。

---

## 3. 待办与诊断(本轮新提 5 项)

### Task 1 — 工程模型页内存过大/重载 ✅诊断完成 ⏳待实现
**诊断结论:内存压力在浏览器(web 客户端),不在服务器。**
- 服务器只构建一次 scene 存 DB;浏览器下载 **7.2MB scene JSON**,解析后在 three.js 建 **~12,500 个构件 mesh + 1500 标记**。这是 WebGL 几何 + JS 堆的主要占用,重载多因浏览器 tab OOM。
- **解决方向(服务器多处理,web 尽量轻):**
  1. **InstancedMesh**(最大收益):重复构件(3415 柱 / 5774 管线)用 three.js `InstancedMesh` 合批,12500 draw → 数个 → 显存/内存骤降。改 `elementsBuilder.ts`。
  2. **按楼层/单体懒加载**:只渲染当前隔离层的几何,切层再建。
  3. **服务端瘦身 payload**:scene 只传轻量几何(简化轮廓/去冗余点),或分块流式;`GET /model` 响应开 gzip(7MB→~1MB)。
  4. **卸载复用**:已有 `disposeObjectTree`,确保切场景彻底释放。
- 优先级:①InstancedMesh ②懒加载 ③gzip。

### Task 2 — compose「build 坏了」✅ 已复核:compose 没坏,是误用
**复核结论(2026-07-12 实测,推翻此前"非常规 fork"判断)**:
- **compose v5.x 是 2026 年的正常版本**:Docker Desktop 4.81.0 自带的官方 compose 就是 v5.2.0(`/Applications/Docker.app/Contents/Resources/cli-plugins/docker-compose`);`~/.docker/modules/cli-plugins/docker-compose` v5.3.0 是 Docker Desktop **官方模块更新机制**,不是 rogue fork。
- **`docker compose build` 正常**:`docker compose --profile app build --dry-run web api` 明确输出「Image cad-web:local Building / cad-api:local Building」。**真实构建已完整跑通**:`docker compose -p cad -f docker-compose.yml -f docker-compose.alt-ports.yml --profile app build web` 成功产出新 `cad-web:local`(镜像时间戳刷新)。此前唯一阻塞是本机拉基础镜像 `node:20-alpine`/`nginx:1.27-alpine` 时网络超时(纯环境问题,`docker pull` 预热后即通),与 compose 无关。
- **`!override` 正常**:`docker compose -f docker-compose.yml -f docker-compose.alt-ports.yml --profile app config` 输出 api→8002、postgres→5434、web→3002,端口正确重映射。
- **此前症状是误用**:①api/web/celery 在 `profiles: [app]` 后面,不加 `--profile app` 就不会被 build/up;②`docker compose up -d`(不带 `--build`)会**复用旧镜像**——这是 compose 标准行为,不是"空操作";要重建须显式 `build` 或 `up --build`。
- **`cad_api_proxy` 代理容器多余**:alt-ports 的 `!override` 直接把 8002 映到 api 容器即可;新环境不需要该代理,可 `docker rm -f cad_api_proxy`。

**权威工作流见 [infra/DEV.md](../infra/DEV.md)**:
- 起基础设施:`docker compose -p cad -f infra/docker-compose.yml -f infra/docker-compose.alt-ports.yml up -d`
- 测试热重载(改代码即生效,不重建镜像):叠加 `-f infra/docker-compose.dev.yml --profile app`
- 正式部署(打包镜像):`docker compose ... --profile app build && ... up -d`

### Task 3 — web 页面缺「操作手册」入口 ⏳待实现
- 现状:手册在 `docs/MODEL_MANUAL_*.md`,但**前端没有查看入口**(此前的 Help 页未落盘/未部署)。
- **方案**:加 `/help` 路由 + 左侧菜单或头部「帮助中心」入口;页面 fetch 手册(把 md 拷进 `public/manual/` + 轻量渲染,或后端出一个只读端点)。按角色区分:普通用户看用户版、管理员多看管理员版。
- 载体二选一:平台内 md 渲染页 / 直接内嵌图文 HTML。参考构建+部署流程见 §4。

### Task 4 — OCR 识别图纸全部文字(核心功能)✅ 基座落地(待真实权重验证)
**已交付(2026-07-12)**:`core/model3d/ocr` 模块 + 24 单测全绿 + CLI + 特性文档 `docs/MODEL_OCR.md`。
- 契约先行、后端可插拔(PaddleOCR 懒加载/降级 + 离线 mock)、渲染→识别→坐标换算→
  分类(标高/轴号/尺寸/楼层/房间/说明)全链路离线可跑;`consume.py` 三个下游接入缝
  (标高候选/轴号锚点/空间图名)就绪,默认不改现有行为。
- **纪律**:置信门槛 + 人工复核双重把关(默认 0.6)。
- **待续**:①放开 requirements 的 paddle 依赖正式 build;②真实图验准确率;③逐步 wiring
  到 section_z/grid_anchor/semantics。详见 `docs/MODEL_OCR.md`。

<details><summary>原始规划(存档)</summary>
- **背景**:CAD 导出 PDF 的正文标注(标高、轴号、构件名、说明)是**矢量绘制的字形,非可提取文本**——`page.get_text` 只拿到标题栏。文字对模型完整性/图纸拼接理解很重要(用户强调)。
- **已探明**:PaddleOCR/tesseract **未安装**;临时装的 tesseract 在真实工程图上**读不出**(细线稀疏,力不从心)。渲染出的标高对人眼**清晰可读**(见 `MODEL_EVAL_SGOH.md`)——说明**OCR 可行,但需强引擎 PaddleOCR**。
- **推进方案(作为核心功能):**
  1. 依赖:`requirements` 加 `paddleocr`+`paddlepaddle`(重,约数百 MB;镜像层需正式 build)。
  2. 管线:图纸 → fitz 渲染高 DPI 位图 → PaddleOCR(中文)→ 结构化文本(内容+坐标+置信度)。
  3. 消费:文本喂入①楼层/标高识别(补 section-z,自动打底人工校正)②轴号→图纸拼接配准③构件名/说明→语义。
  4. 纪律:**置信门槛 + 人工复核**,低置信不采纳(读错比缺失更糟)。先在几张真实图上验准确率再全量。
  5. 注意:多为基坑/围护剖面(地下标高),地上层剖面稀少——地上层高仍主要靠人工录入(Task 3 已建通道)。
</details>

### Task 5 — 本交接记录 ✅(本文件)

---

## 4. 如何运行 / 构建 / 部署(本机现状)

### 起停
- 全栈已在跑:`cad_postgres/redis/minio/chroma`(基础)、`cad_api`(内部 8000)、`cad_celery_worker/beat`、`cad_web`(nginx,3002)、`cad_api_proxy`(8002→api)。
- DB 直连:`docker exec cad_postgres psql -U cad_user -d cad_db`。

### 改后端代码(本机 build 坏,用 cp 注入)
```
for c in cad_api cad_celery_worker; do docker cp apps/api/<改动文件> $c:/app/<路径>; done
docker restart cad_api cad_celery_worker
```
应用迁移:`docker exec -i cad_postgres psql -U cad_user -d cad_db < apps/api/migrations/xxx.sql`

### 改前端代码
```
cd apps/web && npm run build           # node_modules 已在
docker exec cad_web sh -c "rm -rf /usr/share/nginx/html/*"
docker cp dist/. cad_web:/usr/share/nginx/html/
```

### 触发模型重建 + 看进度
```
docker exec cad_api python3 -c "from tasks.model_build import build_project_model; print(build_project_model.delay('<PID>').id)"
docker exec cad_postgres psql -U cad_user -d cad_db -t -A -c "select status,progress->>'stage' from project_models where project_id='<PID>';"
```

### 截图验证(Playwright,node 24)
- 脚本在 `<scratchpad>/walk/`(opera.js 等):登录 admin/admin123 → 打开 `localhost:3002/model/<PID>` → 截图。

---

## 5. 环境坑速查(务必先读)

1. **compose v5.x**:`docker compose build` 空操作、`!override` 失效、`up` 端口串漏。→ 直接 `docker build`;dev 用 `docker-compose.dev.yml` 挂载热重载。
2. **镜像重建会丢 docker cp 的代码**:本机改动是 cp 进容器的,容器 recreate 即回退旧镜像。要么正常 build 打包,要么 recreate 后重新 cp。
3. **MinIO 公网端点**:`MINIO_PUBLIC_ENDPOINT=localhost:9002` 必须设,否则贴图 ERR_NAME_NOT_RESOLVED。
4. **Bash/分类器偶发不可用**:只读操作(读文件/搜代码)不受影响;docker/git 命令偶尔要重试。
5. **OCR 依赖未装**:paddleocr/tesseract 都没有;Task 4 要正式装。
6. **演示层高**:歌剧院 2层=6.0/3层=5.4 是测试录入(非真实),可在「楼层标高校正」清空后重建恢复自动值。

---

## 6. 建议的下一步优先级

1. **Task 2 收尾**:新机器上验证标准 compose,删代理容器,跑通 `docker-compose.dev.yml` 热重载 → 开发体验恢复正常。
2. **Task 1 InstancedMesh** → 解决内存/重载(用户体验硬伤)。
3. **Task 3 帮助入口** → 快速可见价值。
4. **Task 4 OCR** → 立项作为核心功能,按 §3 Task4 分步,先验准确率。
5. push 分支 + 开 PR(全部改动已在 `fix/model-3d-quality`)。
