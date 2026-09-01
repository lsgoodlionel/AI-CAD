# Phase G 升级蓝图与实施计划 —— 「有信息的模型」双向可追溯

> 版本 V1.0 ｜ 2026-07-17 ｜ 状态:核心已完成(G1/G2/G3)+ 深化项待续
> 依赖:Phase E 档案层(单一真相源)+ Phase F 全量扫描(OCR/VLM/信息)

---

## 0. 目标

让工程模型成为**「有信息的模型」**——不再是无源几何,而是每个构件/每张图都可**双向追溯**:

1. **反向追溯(3D 构件 → 来源)**:点击任一 3D 构件,查看它由**哪张图、什么识别途径、
   什么内容**建立。
2. **正向追溯(图纸 → 用途)**:任一图纸预览旁一键,查看这张图在系统内**识别出了什么信息、
   用来做什么、用在哪里**(生成了哪些模型构件),可正向汇总。

价值:可信、可审计、可复核。审图/算量/创效每一步都能回溯到源图纸的具体内容。

---

## 1. 现状基础(Phase E/F 已就位)

- **构件携带来源**:scene 每个构件带 `src`(来源图纸 id)+ `source`(识别途径 rule/circle)
  + `type_label`(档案 OCR 反哺的类型)。
- **档案单一真相源**:`drawing_extracted_info` 每图每条识别信息(矢量/OCR/VLM),可溯源。
- **前端点选钩子**:ModelViewer 已有 `onSelectElement`。
- **约束(已解除)**:柱/墙/梁/板曾是**合并网格**(一个 mesh = 该层该类全部构件),反向追溯只到
  「该层该类来自哪些图」粒度。现已通过 `itemPicks`(逐构件 faceEnd 区间)让**所有**构件类型
  都能按 raycast `faceIndex` 反查**单个构件**;合批性能不变(仍是一个 mesh)。

---

## 2. 实施计划与进度

### ✅ G1 正向追溯 API(已完成)

`GET /drawings/{id}/trace` — 一张图识别了什么 + 用在哪:
- `info`:识别信息按类别/抽取器汇总(生效值,来自档案)。
- `model_usage`:从最新 scene 统计 `src=该图` 的构件(按楼层/类别)+ 模型版本。
- 纯函数 `model_usage_from_scene` + `build_drawing_trace`,3 单测。
- **实测**:基础底板换撑图 → 识别 470 条 → 生成 525 构件。
- 文件:`services/drawing_trace.py`、`routers/drawings.py`、`tests/test_drawing_trace.py`。

### ✅ G2 前端图纸追溯抽屉(已完成)

`DrawingTraceDrawer`(全站复用):
- ① 识别信息:按类别表 + 抽取器标签(矢量/OCR/VLM 带计数)。
- ② 模型用途:生成构件按楼层/类别 + 模型版本;未生成时提示(说明/详图/未分层)。
- 入口:预览弹窗加「识别信息 / 用途追溯」按钮。
- 文件:`components/DrawingTraceDrawer.tsx`、`components/DrawingPreviewModal.tsx`、
  `services/projectInfo.ts`(getDrawingTrace)。

### ✅ G3 反向追溯:构件 → 来源(已完成)

- `elementsBuilder.collectSourceInfo`:合并网格聚合 distinct 来源图纸/识别途径/类型标签
  入 userData(`sourceDrawings`/`sourcePaths`/`typeLabels`)。
- `ModelWorkspace` 构件点选面板:显示**类型标签**(钢立柱/幕墙/桩)、**识别途径**
  (几何规则/圆检测/柱包络…)、**来源图纸数**;逐张「追溯来源图纸」按钮 → 打开 G2 抽屉,
  反查每张图识别了什么、用在哪。
- 文件:`elementsBuilder.ts`、`ModelWorkspace.tsx`、`services/projectModel.ts`(补 source/shape)。

---

## 3. 深化项(待续,非阻塞)

| 项 | 说明 | 优先级 |
|----|------|--------|
| ~~G-精确~~ ✅ | 柱/墙/梁/板/设备**已全部**改 per-item faceIndex 拾取(`mergedMeshWithPicks`/`itemPicks`/`resolveItemPick`,c6d2202)。**并修掉其中一个正确性缺陷**:墙/梁的 BoxGeometry 是索引几何,faceEnd 却按 position 累加(8 vs 真实 12),边界逐段漂移会反查到**错误的来源图纸**;已统一为 `geometryTriangleCount`(索引按 index、非索引按 position),4 例回归测试锁死 | ✅ 已完成 |
| G-审图追溯 | 审图问题/算量条目也挂来源图纸链,审图/算量结论可回溯源图内容 | 中 |
| G-图纸列表入口 | 图纸列表 / 工程信息页每行加「追溯」入口(现仅预览弹窗) | 低(预览已覆盖主路径) |
| G-VLM 溯源 | 追溯抽屉区分 VLM 候选(专业/构件)与确定性识别,标注置信 | 低 |

---

## 4. 验收

- **反向**:工程模型页点任一构件 → 面板显示来源图纸数/识别途径/类型标签 → 点「追溯来源图纸」
  → 抽屉显示该图识别信息 + 用途。✅
- **正向**:任一图纸预览 → 「识别信息/用途追溯」→ 抽屉显示识别了什么(470 条)+ 生成了什么
  (525 构件)。✅
- **数据一致**:追溯的识别信息 = 工程信息页所见(同一档案单一真相源)。✅

**测试**:G1 后端 3 例;前端构建通过;真实端点实测(470→525)。

---

## 5. G4 识别信息明细(设计完成,待实现)

> 状态:方案已定,代码**未持久化**。目的:让正反向追溯都能看到「具体什么内容」,而不止分类计数。

**缺口**:`build_drawing_trace` 只做 `by_category`/`by_extractor` 计数,丢弃了档案每条的
`content`(图上原始文字)/`value_json`(结构化值,如标高 -5.900)/`source_kind`(是否人审核实)。

**方案**:
- 后端 `services/drawing_trace.py` 新增纯函数 `info_items_digest(items)`:每条产出
  `{category, display(具体内容), extractor, confidence, verified}`;按类别分组、组内 verified 优先;
  超 800 条截断。`build_drawing_trace` 的 `info` 增加 `items` + `items_truncated`。
- 前端 `DrawingTraceDrawer` ①区块下增「识别信息明细」按类别折叠列表(具体内容 + 途径标签 +
  已核实徽标);类型 `TraceInfoItem`。反向侧构件面板「追溯来源图纸」复用同一抽屉。

**任务**:`G4-1` 后端 digest + 单测;`G4-2` 前端明细折叠。**优先级:高**。

---

## 6. 待解决问题与方案(G5–G9,实战暴露)

> G6/G9 的**根因**指向装配范式,已被 `PHASE_H_BLUEPRINT.md` 吸收根治;此处保留问题定位与
> Phase G 层面的止血动作。G7/G8 独立可交付、不依赖 H。

### G5 建模流程 vs 人类阅图顺序对齐(问题①)

**人类阅整套工程图的标准顺序**(建模编排原则):① 专业:建筑→结构→机电→幕墙→装修;
② 图种:目录→总说明→总图→平面图→剖面图→节点详图;③ 竖向:桩基→围护→地下→地上(自下而上)。
**当前差距**:各图平等跑引擎,无「先读总说明/构件表拿全局约束」;竖向无基底锚定。
**方案**:建模编排按「专业→图种→自下而上」排序(详见 `PHASE_H_BLUEPRINT.md` §5)。**优先级:中(与 H 合流)**。

### G6 OCR/VLM/矢量信息 ↔ 模型「未关联标注」(问题②)

**诊断**:scene 构件仅带单值 `src`,**不反指档案条目 id**,「构件↔具体内容」链断裂;大量无图层 PDF 抽不出几何→无模型。
**方案**:构件携带 `archive_refs` = Phase H `component_observations.archive_ref`;「无模型图」标注原因。**优先级:中(H2 前置)**。

### G7 大歌剧院扫描进度卡 75%(问题③)

**诊断(代码事实,`routers/project_info.py`)**:`percent = round(100*ready/total)`,`ready` = `item_count>0`
→ **卡 75% = 25% 图 `item_count=0`**;API **无法区分「任务没跑」与「跑了产出空」**。
**方案**:进度接**任务级状态**(queued/running/done/failed);空图记**原因码**(no_text/ocr_failed/no_transform/non_model_view),可筛选可重试。**优先级:高(可观测性)**。

### G8 工程信息「只有分类数量,没有具体内容」(问题④)

**诊断(代码事实)**:`_summarize_items` 只返计数 + 8 条 `samples`(前 40 字符);`get_summary` 是计数 + coverage。
**方案**:工程信息页每类别**可展开看具体条目**(值+途径+是否核实),复用已有 `listInfoItems`;与 G4 共用 `TraceInfoItem`。**优先级:高(后端已就绪)**。

### G9 多图拼接坐标/标高错误(问题⑤)

**诊断(代码事实,`elementsBuilder.ts`)**:XY 靠 `realTransform(center)` 全楼统一平移,但 `drawing_transform`
仅覆盖 705/2309 图→无变换图错位;Z 靠 `storyHeight` 堆叠非真实标高;无轴网/标高语义配准;归一化兜底破坏比例。
**方案**:治标——无 transform/无轴网格图**从总装隔离标「未配准」**,禁归一化进多图总装;治本——轴网锚点跨图配准(`PHASE_H_BLUEPRINT.md` §3)。**优先级:高(止血)**。

---

## 7. 补充问题优先级与收敛路径

| 编号 | 问题 | 根因层 | 首选动作 | 优先级 |
|------|------|--------|---------|--------|
| G4 | 追溯只有计数无明细 | 展示层 | digest 明细(后端+前端) | 🔴 高 |
| G7 | 扫描卡 75% | 可观测性 | 任务级状态 + 空图归因 | 🔴 高 |
| G8 | 工程信息无具体内容 | 展示层 | 类别展开明细(接 listInfoItems) | 🔴 高 |
| G9 | 拼接位置错误 | 装配范式(H) | 无配准图隔离止血 | 🔴 高 |
| G6 | 信息↔模型未关联 | 装配范式(H) | 构件带 archive_refs | 🟡 中 |
| G5 | 建模顺序≠阅图顺序 | 编排 | 专业+图种顺序化编排 | 🟡 中 |

> 收敛路径:**G4/G7/G8 + G9 止血**(独立可交付)→ 进 `PHASE_H_BLUEPRINT.md` 实体装配垂直切片
> (柱/桩+单体)→ 达标推广。G5/G6 与 Phase H 合流根治。
