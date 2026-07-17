# 工程 3D 模型 · 操作手册(管理员版)

> 版本 V1.3 ｜ 最后更新 2026-07-16 ｜ 适用对象:系统管理员、运维、后端负责人、技术负责人
>
> 配套文档:一线业务用户请见《[工程 3D 模型 · 操作手册(用户版)](MODEL_MANUAL_USER.md)》。
>
> **手册维护约定:凡涉及构建流程、API、依赖、能力边界、安全项的迭代,开发须同步更新本手册对应章节,并在文末「版本历史」登记一行。**

---

## 目录

1. [系统定位与总体架构](#1-系统定位与总体架构)
2. [阶段能力矩阵与当前状态](#2-阶段能力矩阵与当前状态)
3. [构建流程详解(六阶段)](#3-构建流程详解六阶段)
4. [API 端点完整清单](#4-api-端点完整清单)
5. [权限与数据隔离模型](#5-权限与数据隔离模型)
6. [能力边界:确定性可用 vs 需 GPU/权重/数据](#6-能力边界确定性可用-vs-需-gpu权重数据)
7. [算量(QTO)口径](#7-算量qto口径)
8. [符号识别 / 融合引擎 / 评测](#8-符号识别--融合引擎--评测)
9. [数据集与标注质量门槛](#9-数据集与标注质量门槛)
10. [许可合规(强制门禁)](#10-许可合规强制门禁)
11. [优雅降级矩阵](#11-优雅降级矩阵)
12. [数据库表速查](#12-数据库表速查)
13. [部署、开关与依赖](#13-部署开关与依赖)
14. [运维与故障排查](#14-运维与故障排查)
15. [安全遗留项(上线前必处理)](#15-安全遗留项上线前必处理)
16. [事件编排层与管线建议(Phase D)](#16-事件编排层与管线建议phase-d)
17. [路由迁移与重定向(Phase D)](#17-路由迁移与重定向phase-d)
18. [手册维护约定](#18-手册维护约定)

---

## 1. 系统定位与总体架构

工程模型子系统把项目图纸自动升级为可在 Web 三维查看、可算量、可审校的工程模型。技术路线的唯一可靠形态是 **「AI + 确定性混合」**,而非 VLM 端到端。

**四层分工架构**(权威总纲见 `docs/AI_READING_TO_3D_MODEL.md`):

| 层 | 职责 | 技术 |
|---|---|---|
| **L1 几何真值层** | 精确坐标/尺寸/计数 | ezdxf / 矢量 PDF 确定性提取 |
| **L2 符号语义层** | 符号识别 + 读表判专业 | CADTransformer→VecFormer + VLM(仅语义候选) |
| **L3 结构装配层** | 构件→拓扑→IFC→跨视图 z 恢复 | 规则 + IfcOpenShell + 自研 z 恢复 |
| **L4 成果呈现层** | Web 三维 + 算量 + 审校 | That Open/Fragments + QTO + 审校工作台 |

**贯穿全栈的铁律**:凡「数出来 / 量出来 / 对齐坐标」一律交确定性引擎;**VLM 只做语义候选 + 置信度,绝不输出计数/坐标/尺寸/QTO**。

核心代码位置:
- 主编排:`apps/api/services/model_builder.py`(`build_scene()`)
- 确定性引擎:`apps/api/core/model3d/`(geometry_extractor / element_recognizer / section_level_extractor / elevation_opening_extractor / grid_anchor_extractor / topology_rules / provenance / layer_conventions)
- 子模块:`preprocess/`、`spotting/`、`fusion/`、`eval/`、`dataset/`
- 服务:`apps/api/services/`(model_qto / model_qto_summary / model_topology / section_z_recovery / model_z_levels / model_component_sections / model_lod / model_ifc_*)
- 异步任务:`apps/api/tasks/model_build.py`
- 路由:`apps/api/routers/{project_models,model_spotting,model_annotations,model_review}.py`

---

## 2. 阶段能力矩阵与当前状态

| 阶段 | 交付能力 | 状态 |
|---|---|---|
| **Phase 6** | 模型基座(楼层堆叠 + 图纸贴图 + IFC glTF + 成果标记),展示级 2.5D | ✅ 已建成 |
| **Phase 7** | 构件级重建 V2(柱/墙/梁/板 + 机电管线/设备),scene schema v2。**此阶段 z 全硬编码**(梁 0.6 / 板 0.12 / 管 0.1 / 层高 4.5m) | ✅ 已建成 |
| **Phase A** | 合规 IFC4 底座 + 百万构件级 Web 渲染(Fragments)+ VLM 语义 + 图层识别强化 | ✅ 完成并全链验证(百万构件压测、VLM 真实准确率为部署期任务) |
| **Phase B** | 算量级:跨视图 z 恢复(替换硬编码常量,点亮 LOD300 gate)+ 构件拓扑 + IFC-QTO 算量 + 创效打通(B-01~B-24) | ✅ 完成(以确定性合成几何验证;真实脱敏整套图到位后复跑,口径不变) |
| **Phase C** | BIM 级:合规门禁 + 自建数据集 + 符号识别学习模型服务化 + 融合引擎 + 审校闭环 + 统一评测(C-01~C-18) | ✅ 离线可交付部分全完成;**M2(审校收敛)达成;M1(符号识别超纯规则)基座就绪,终评数字待 C-09 真实微调(GPU+数据+权重)** |

各阶段验收报告:`docs/PHASE_A_DEMO.md`、`docs/PHASE_B_DEMO.md`、`docs/PHASE_C_ACCEPTANCE.md`、`docs/PHASE_C_EVAL_REPORT.md`。

---

## 3. 构建流程详解(六阶段)

主编排 `services/model_builder.py:build_scene()`,由 Celery 任务 `tasks/model_build.py:build_project_model` 异步驱动。每阶段通过 `progress_cb` 回写 `project_models.progress`(阶段码 `fetch|render|recognize|assemble`)供前端进度条实时展示。

| 阶段 | 内容 | 关键模块 |
|---|---|---|
| **0 输入聚合(fetch)** | 查项目、图纸、每图最新 AI 审图问题、最近批次跨图发现;A-13 VLM 语义融合(灰度开关 `vlm_semantic_enabled`,关闭时恒等无副作用) | `_fetch_inputs` |
| **1 楼层解析/归一化** | 从图名/图号/审图文本提取楼层三元组;单体识别 + 楼层归一化 + 层高推断 | `floor_parser.parse_floor`、`model_story.normalize_story_table` |
| **1.5 跨视图 Z 恢复(B-05)** | 找剖面图→抽标高序→对齐平面楼层序→`z_overrides`,有覆盖则用实测层高重新归一化并点亮 LOD gate;无剖面 no-op;单图 20s 超时 | `section_z_recovery`、`cross_view_registration.register_views` |
| **2 资产渲染(render)** | 逐图下载→渲染→上传 MinIO;PDF 走 fitz,DXF 走 ezdxf,DWG 先 `dwg_support.ensure_dxf`,IFC→glb | `_build_assets` |
| **3 楼层堆叠 + 构件重建(recognize)** | 图纸堆入楼层;逐层逐类选最适图纸→几何提取→识别柱/墙/梁/板/管线/设备→跨图轴号配准→YOLO 设备补充;推导真实米标高 | `model_elements.build_floor_elements` |
| **3.5 截面回填(B-07)** | 从剖面/详图文本抽宽×高/板厚/墙厚/柱截面/管径,覆盖硬编码默认,打 `z_source=measured`;无标注全默认(`estimated=True`) | `model_component_sections` |
| **4 组装 + 拓扑 + LOD(assemble)** | 单体分组 + 拓扑图 + LOD 评估(100/200/300)+ 组装 scene v2 + 可选程序化 IFC | `model_topology`、`model_lod.evaluate_lod_capability` |
| **5 落库** | 成功:`status=ready`,version+1,写 scene/assets/build_mode/built_at;失败:`status=failed`,error 截断 500 字符,最多重试 2 次 | `tasks/model_build.py` |

> **重要**:主构建链只落 `project_models.scene/assets`。QTO 与拓扑在 GET 端点**即时从 scene 重算**(非依赖落库表);`model_quantities`/`model_topology_relations` 等表用于快照/看板。

> **图层先验强化(阶段 3 recognize)**:`element_recognizer` 的**楼板与墙识别现已接入 `classify_by_layer` 图层先验**(此前仅柱/设备接入,墙/板纯几何):
> - **楼板**:图层判定为 slab 的多边形**逐块收集**(修复「每图仅取面积最大的一块板」),支持多分区筏板/多板块;命中筏板/底板/承台(`_RAFT_RE`)者标 `kind=raft` 并给更厚默认(`_RAFT_THICKNESS_M=0.5m` vs 楼板 `0.12m`)。无图层命中时回退原「最大多边形 / 轴网包络 / 柱包络」兜底,零回归。
> - **墙**:成对两线均落在墙图层时,间距上限由 `_WALL_GAP`(0.4m)放宽到 `_WIDE_WALL_GAP_MAX`(1.0m),召回此前被结构性丢弃的**地下室外墙/挡土墙/人防墙**;非墙图层仍按 0.4m,不产生假墙。
> - `data/layer_conventions.yaml` 的 slab/wall 已补 承台/筏板/底板/地下室外墙/挡土墙/人防墙 及 DWQ/RFQ/FB/DB/CT 等拼音·英文代号;`model_elements._STRUCTURE_TITLE_RE` 与 `drawing_filename_parser` 补「基础/筏板/底板/承台/地下室」,避免基础平面图被漏筛出结构桶。
> - 收益取决于图层命名规范度;图层缺失/非常规命名时仍走几何兜底,精度不劣于旧版。

---

## 4. API 端点完整清单

所有端点前缀 `/api/v1/projects`,均依赖 `get_current_user` 认证;本模块**无角色级授权**(见 [§5](#5-权限与数据隔离模型))。

### 4.1 模型构建与场景 — `routers/project_models.py`

| 方法 | 路径 | 说明 | 失败码 |
|---|---|---|---|
| POST | `/{id}/model/rebuild` | UPSERT + 置 building + 触发 Celery + 审计 | 404 `PROJECT_NOT_FOUND` |
| GET | `/{id}/model` | 模型状态与 scene | 404 `MODEL_NOT_BUILT` |
| GET | `/{id}/model/quantities` | QTO 工程量汇总(实时算,不落库) | — |
| POST | `/{id}/model/quantities/to-proposal` | QTO 差值→创效提案草稿(201) | 409(scene 空)/ 400 `NO_POSITIVE_SAVING` |
| GET | `/{id}/model/annotation-queue` | 待标注楼层队列 | — |
| POST | `/{id}/model/annotations` | 保存楼层人工标注 | 400(缺 drawing_id) |
| GET | `/{id}/model/semantics` | 语义图谱 | — |
| POST | `/{id}/model/semantic-operations` | 语义操作(乐观锁 `expected_version`) | 409 `SEMANTIC_VERSION_CONFLICT` / 422 |
| GET | `/{id}/model/rebuild-impact` | 语义操作重建影响预估 | — |
| GET | `/{id}/model/asset-url` | 资产签名 URL(300s) | 403 `ASSET_FORBIDDEN`(key 前缀越权) |

`to-proposal` 请求体 `QtoToProposalBody`:`rebar_inputs`、`rebar_params`、`extra_saving_yuan`、`title`。写 `incentive_proposals`(type=`B`,仅 draft;下游 calculate/签字硬约束不被绕过)。

> ⚠️ **前端缺口(如实记录)**:`POST /{id}/model/quantities/to-proposal` 端点本身可用,但截至 2026-07-14,`apps/web/src` 中**没有任何页面调用它**(模型页「算量模式」`QuantityModePanels.tsx` 只展示汇总 + 跳转算量中心,不含转提案按钮;算量中心 `pages/quantities/` 同样没有)。即该接口目前只能靠直接调用 API 使用,不在任何前端用户旅程里。与之对照,同为 Phase D 新增的 `POST /{id}/findings/{source}/{key}/to-proposal`(见 §4.6 Finding 统一聚合)**已经**接了前端按钮(审查中心「转创效提案」)。补齐 QTO 转提案的前端入口是已知待办,不在本次 D-22 文档同步范围内。

### 4.2 符号识别 — `routers/model_spotting.py`

| 方法 | 路径 | 说明 | 失败码 |
|---|---|---|---|
| POST | `/{id}/drawings/{did}/spot` | 单图符号 spotting | 409(无 file_key)/ 502(下载失败) |
| GET | `/{id}/drawings/{did}/spot/backends` | 观测后端选路/可用性 | — |

### 4.3 符号标注人审 — `routers/model_annotations.py`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/{id}/drawings/{did}/symbol-annotations` | 列标注(置信升序,低置信先审) |
| POST | `/{id}/drawings/{did}/symbol-annotations` | 保存(confirm/reject/reclass/addbox/edit)+ 写埋点 |
| GET | `/{id}/symbol-annotations/export` | 导出 confirmed 金标签(COCO,喂 C-09) |

动作→状态:addbox/edit/confirm→confirmed,reject→rejected,reclass→reclassed。addbox 需 category+bbox;confirm 等需 id。双写 `model_symbol_annotations` + append-only `model_review_actions`。

### 4.4 语义审校 — `routers/model_review.py`

| 方法 | 路径 | 说明 | 失败码 |
|---|---|---|---|
| GET | `/{id}/model/review-queue` | 审校队列(拓扑/命名/规范,低置信+冲突优先) | 400(非法 target_kind) |
| POST | `/{id}/model/review-actions` | 提交审校动作 + 写审计(201) | 400(reclass 缺 new_category) |

Query:`target_kind`(topology/naming/compliance/element/symbol)、`discipline`、`only_conflicts`、`limit`(0-1000)。优先分 = 冲突权重 1000 + 100×(1-conf)。

### 4.5 返工度量 — `routers/dashboard.py`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/dashboard/model-review-metrics?project_id=&discipline=` | 确认/改类/否定/补框率 by 专业 by 类别 + 按天收敛趋势。返工率 = reclass+reject+addbox |

### 4.6 Finding 统一聚合 — `routers/findings.py`(Phase D · D-05/D-06/D-07,migration 026)

前缀同为 `/api/v1/projects`。把五类割裂的问题/发现(单图 AI 审图 `ai_review_issues` / 会审 `review_audit_findings` / 跨图 `review_batches.cross_findings` / 语义审校 `project_models.scene` 派生 / 符号待审 `model_symbol_annotations`)统一读取为一个 **Finding** 抽象,供前端「审查中心」(`pages/review/Center/`)消费。**不修改**上述任一来源表的结构或写入路径——聚合只发生在 `services/finding_service.py` 应用层;新增的唯一持久化表是状态覆盖表 `finding_status`(见 [§12](#12-数据库表速查))。

| 方法 | 路径 | 说明 | 失败码 |
|---|---|---|---|
| GET | `/{id}/findings` | 列表:`source`(engine/review/cross/semantic/symbol)/`severity`/`status`/`drawing_id` 筛选 + 分页 + 汇总(含 `saving_potential_count`) | — |
| GET | `/{id}/findings/{source}/{source_key}` | 单条详情(含 `has_saving_potential` 规则判别标) | — |
| POST | `/{id}/findings/{source}/{source_key}/status` | 状态流转:`pending→acknowledged→remediated→closed`,**单向不可回退** | 409(非法/回退流转) |
| POST | `/{id}/findings/{source}/{source_key}/to-proposal` | 一键转创效提案**草稿**;规则优先判别创效潜力,`use_llm=True` 时可选走 `ModelRouter` 增强召回;仅造 draft,三审签字硬约束不被绕过 | 409 `NO_SAVING_POTENTIAL` |

`source_key` 按来源语义不同:engine/review 用来源表 UUID;symbol 用 `model_symbol_annotations.id`(bigint 字符串化);semantic 用 `build_review_queue` 派生稳定 id(如 `host:o1`);cross 用 `{batch_id}:{category}:{key}` 组合 key。

### 4.7 管线建议待办 — `routers/pipeline.py`(Phase D · D-08,migration 027)

前缀同为 `/api/v1/projects`。事件编排层只生成「建议/待办」并落库,本路由仅提供查询与人工采纳/忽略的出口——**采纳建议本身不触发任何重建/创建提案等硬动作**,前端/调用方仍需自行调用 `POST /{id}/model/rebuild`、`POST /{id}/model/quantities/to-proposal` 等既有端点完成实际操作。详见 [§16](#16-事件编排层与管线建议phase-d)。

| 方法 | 路径 | 说明 | 失败码 |
|---|---|---|---|
| GET | `/{id}/pipeline/suggestions?status=` | 列出建议待办,缺省只看 `open` | 422 `INVALID_STATUS` |
| POST | `/{id}/pipeline/suggestions/{sid}/accept` | 标记已采纳(不代为执行) | 404 `SUGGESTION_NOT_FOUND` / 409 `SUGGESTION_ALREADY_RESOLVED` |
| POST | `/{id}/pipeline/suggestions/{sid}/dismiss` | 标记已忽略 | 同上 |

---

## 5. 权限与数据隔离模型

**⚠️ 关键点,管理员必须知晓:工程模型子系统整体无角色门禁。**

- 前端路由 `/model` **无 `access` 字段**(对比 `/admin` 需 `isAdmin`),模型页组件内**无 `useAccess`/角色判断**。
- 后端所有端点仅要求 `get_current_user`(已登录活跃用户),**未做 `require_admin` 或经济师白名单**。
- 即:**任何已登录用户都可查看模型、触发重建、做标注与审校**。
- **数据隔离**靠 `project_id` + 资产 `key` 前缀校验实现(`asset-url` 越权返回 403 `ASSET_FORBIDDEN`,前缀须为 `projects/{id}/model_assets/`)。

真正的角色门禁在别处(三审签字 `isEconomist`/`isPM`、`/admin` 的 `isAdmin`),**不适用于工程模型**。若业务上需要对「重建/审校」做角色收敛,是一个明确的待增强项——当前不存在。

---

## 6. 能力边界:确定性可用 vs 需 GPU/权重/数据

### 6.1 确定性可用(今天即生效,可回退,零现网影响)

几何提取、图层约定构件识别(含楼板多板块/基础底板·筏板·承台/地下室外墙的图层先验识别,见 §3 阶段 3 说明)、合规 IFC4 生成、IFC→Fragments、Web 三维渲染、跨视图 z 恢复、构件拓扑、IFC-QTO 算量、钢筋回填、创效草稿、融合引擎的**规则通道**、统一评测**基座**、审校工作台闭环、数据切分/合规门禁。

### 6.2 需 GPU / 权重 / 真实数据才生效(当前降级)

| 能力 | 阻塞依赖 | 当前降级形态 |
|---|---|---|
| 符号识别学习模型真实精度 | GPU + 脱敏数据 + 权重微调(C-09) | spotting **mock 占位**(规则派生),`is_available()=False` |
| VecFormer 后端(PQ 91.1) | 官方**权重未释放** + 许可复核 | 占位 stub,恒 `False`,降级 mock/CADTransformer |
| VLM 读表/判专业真实准确率 | 接入 VLM API + 5-10 张真图 | 全链路 mock 通过,准确率待部署期实测 |
| 百万构件级 Web 帧率/内存 | 大模型 + 部署环境压测 | 小样例验证链路,压测留部署期 |
| M1 终评(学习模型超纯规则数字) | C-09 真实微调权重 + 冻结 test 集 | 评测表诚实留白 |

> **对外话术锚点**:全流程效率提升现实值 **25%–30%**,定位「AI 出初模 + 平台内人工审改」,**非「一键出 BIM」**。学术峰值实例精度不等于可交付。

### 6.3 Phase E 新增能力与边界(图纸信息档案层 + PDF 几何识别)

**新增确定性能力**:
- **图纸信息档案层**:导入即抽取(OCR/矢量文字/文件名),每图一份档案入库,
  工程信息页可查/人审修正(verified 覆盖 auto 且跨重抽不复活);建模标高/轴号
  从档案读(单一真相源,不再重复 OCR)。
- **围护桩/圆柱圆检测**:栅格 HoughCircles 补柱,双闸(仅平面图+仅结构/通用)
  防误检——歌剧院整机 columns 3089→5794(+2705 桩)。
- **构件类型标签**:档案 OCR 短标签(钢立柱/幕墙/围护桩)就近关联几何构件,
  附 type_label(不新增顶层类别)。

**诚实边界(纯 PDF 项目,如歌剧院)**:
| 现象 | 原因 | 说明 |
|---|---|---|
| 无图层构件识别 | PDF 无 CAD 图层 | 图层词表仅对 DXF 项目有效;PDF 靠几何+OCR |
| 矢量文字取不到 | CAD PDF 正文为矢量字形 | 类型/标注信号只能来自档案 OCR |
| 钢柱/桩靠圆检测 | 桩以圆/线段簇表达,非近方多段线 | 平面图有效,剖面不跑(防钢筋圆误检) |
| 轴号/类型标签整机显效滞后 | 依赖 OCR 回填覆盖(289s/图)+ 变换质量(轴线多无标签) | 代码就绪,随回填铺开逐步显现;见 `docs/PHASE_E_E3_AUDIT.md` |
| 板为数十块量级 | 楼层堆叠模型每层 1 板 + 桩包络补板 | 非缺陷;深度板恢复(线段闭合外轮廓)未做 |
| 外立面/幕墙曲面/自由曲面外壳 | 方法论边界 | 不支持,禁止宣称「还原效果图」 |

---

## 7. 算量(QTO)口径

`services/model_qto.py` + `model_qto_summary.py`。纯几何、可离线手算校验。

- **混凝土**:毛体积 + 拓扑扣减净体积。柱=area×层高;墙=length×width×height(默认 width 0.2);梁=毛−Σ支承端(默认 width 0.3 / depth 0.6);板=毛−Σ支承梁(Liang-Barsky 线段裁剪)。
- **模板**:接触模板面(侧+底)与自由面(顶/端)分列,板底扣梁顶。
- **钢筋**:**只读复用** `core.economic.rebar_calculator.optimize_cutting`(GB50010,不改算法);无配筋输入则 `rebar_missing=True` 不臆造。默认标准长度 9000/10000/12000,钢价 4000/t,目标损耗率 1.5%。
- **汇总**:按类型分桶 + measured/estimated/uncovered 计数(缺失构件不静默漏量);分楼层算量(层高优先取 story_tables 实测,缺省 4.5m)→ 项目/楼层/单体三级。
- **触发**:`GET /model/quantities` 从 scene **实时算**(不落库)。快照走 `model_quantities` 表(migration 022)。
- **IFC 量集**:`write_concrete/formwork/rebar_quantities` 挂到 `Qto_*BaseQuantities`,写入失败不阻断算量。

> 边界:QTO 扣减为 MVP 通用口径(按类型代表值);变截面/地区细则/符号级配筋自动识别 Phase B 不做。

---

## 8. 符号识别 / 融合引擎 / 评测

### 8.1 Spotting 服务(`core/model3d/spotting/`)

引擎名 `symbol_spotting`,纳入 ModelRouter 引擎治理(migration 023 种子 primary=CADTransformer / fallback=mock)。后端优先级链:CADTransformer(懒加载,import/实例化失败即回退)→ MockSpottingBackend(离线兜底,基于 auto_label 图层弱标签)。调用日志异步写**专用表** `symbol_spotting_logs`(不污染 LLM token 列)。

### 8.2 融合引擎(`core/model3d/fusion/`)

核心方法论:**学习模型补规则的模糊边界,不替代规则。**

1. bbox IoU 贪心配对;
2. 每对交 `arbitrate` 裁决;
3. **未配对规则原样保留**(召回保底);
4. 未配对模型置信 ≥ 门槛(0.45)则补召回 `source=model`,否则 `model_rejected`。

仲裁:同类→共识增强 `source=fused`;异类 + 规则强命中(≥0.85)→**规则不被覆盖**(rule_protected);异类 + 规则弱→按「置信 + 规则优先级」仲裁(可 model_override 或 rule_wins)。规则优先级:column>beam>slab/wall>door/window>pipe/equipment>axis。

**结构性保证**:融合召回 ≥ 纯规则、精度 ≥ 纯规则。配置从 `data/model3d/fusion_policy.yaml` 加载,缺失/解析失败降级 DEFAULT_POLICY 绝不抛。

### 8.3 评测(`core/model3d/eval/`)

度量定义**锁定实现**(规避与论文不可比),IoU 阈值默认 0.5:

| 指标 | 定义 |
|---|---|
| 匹配(TP) | 同类别且 IoU>0.5 |
| P / R / F1 | TP/(TP+FP) / TP/(TP+FN) / 2PR/(P+R) |
| SQ | TP 平均 IoU |
| RQ | TP/(TP+0.5FP+0.5FN) |
| **PQ** | SQ×RQ(FloorPlanCAD 口径) |

CLI:`scripts/model3d/eval_harness.py --demo`(合成)或 `--manifest`(冻结 test 集)。C-09 真实权重就绪前 model 端由 mock 代入,报告显式标注该状态。

---

## 9. 数据集与标注质量门槛

规范见 `docs/PHASE_C_DATASET_SPEC.md`、`docs/PHASE_C_ANNOTATION_GUIDE.md`。

- **类别体系(冻结)**:9 顶层类 `column/beam/slab/wall/door/window/pipe/equipment/axis` + 机电 4 系统(消防/给排水/电气/暖通)。**一旦返工已标数据全废**,须专业工程师联合评审冻结。
- **样本量级**:结构 ≥3-5 万实例 / ≥150 张 / ≥8 项目,滚动分批(结构→机电→装修)。
- **切分**:**按项目切分(非按图)防泄漏**,固定种子可复现,**test 集冻结,仅终评解冻一次**(`scripts/model3d/dataset_split.py`)。
- **标注质检门槛**:Cohen's Kappa **κ≥0.8**(硬门槛,不达标整批驳回重标);框匹配率 ≥0.85(观察线);弱标签一致率目标 ≥70%。
- **数据飞轮**:人审动作写 `model_review_actions` + `audit_logs`,金标签(confirm/reclass/addbox)写 `model_symbol_annotations` 回流 C-09。COCO 导出接口:`GET /symbol-annotations/export`。
- **大文件**:数据集走 MinIO/DVC,**不进 git**。

---

## 10. 许可合规(强制门禁)

⚠️ 见 `docs/PHASE_C_LICENSE_AUDIT.md`。

- **产品代码只允许**:CADTransformer(**MIT**)、VecFormer(**Apache 2.0**)。
- **SymPoint 绝不进产品**:V1 非商用、V2 未声明许可。物理隔离于 `research/sympoint-eval/`(已入 `.gitignore`/`.dockerignore`),CI `license-compliance` 为**阻断型门禁**,强制 grep 恒空。SymPoint 仅作天花板对标,只回流数字,代码/权重永不进产品或镜像。
- 变更任何 spotting 后端依赖前,先过 license 审计;VecFormer 权重释放后须复核许可再启用(`docs/PHASE_C_VECFORMER_WATCH.md`)。

---

## 11. 优雅降级矩阵

设计铁律:**任何单点失败降级,绝不中断整体构建。** 代码库统一用 `# noqa: BLE001` 标注跨边界宽异常捕获→降级点。

| 环节 | 依赖 | 降级 |
|---|---|---|
| 输入聚合 | projects/drawings | 项目不存在→任务失败重试;无图纸→空 scene |
| VLM 语义 | `vlm_semantic_enabled` | 关闭时三步恒等无副作用 |
| 楼层解析 | 图名/图号文本 | 匹配不到→UNZONED;标注表未部署→回退自动识别 |
| Z 恢复 | 剖面 + 几何 | 无剖面→no-op;单图失败/20s 超时→跳过;配准失败→降级剖面单证据 |
| 资产渲染 | MinIO + fitz/ezdxf/ifcopenshell | 单图失败/90s 超时→`image_key=""` 线框;>25MB 跳贴图;>400 张线框占位;ifcopenshell 缺失→ifc_skipped |
| 构件识别 | `core.model3d` + 几何 | ImportError/单图 20s 超时→回退贴图空 elements |
| 截面回填 | 剖面/详图文本 | 无标注→全默认 estimated,不伪装实测 |
| 拓扑/QTO | elements | 无支承→isolated,net=毛不扣;无配筋→rebar_missing |
| 符号 spotting | CADTransformer 权重/GPU | 无 GPU/权重→回退 mock,永不硬失败;预处理失败→空文档+warning |
| 融合 | fusion_policy.yaml + pyyaml | 缺失/解析失败→DEFAULT_POLICY |
| 标注/审校 | migration 024 两表 | 读取失败→回退自动识别不阻断构建 |
| 进度回写 | migration 014 progress 列 | 写失败仅 debug 告警,不影响构建 |

**LOD 诚实原则**:缺剖面时 `estimated=true` 且 LOD 停在 <300 不虚高;含剖面→标高实测、`estimated=False`、`matched=True`、LOD 300。

---

## 12. 数据库表速查

| 迁移 | 表/列 | 用途 |
|---|---|---|
| 013 | `project_models` | 项目级唯一,status(pending/building/ready/failed),version,scene/assets JSONB |
| 014 | `project_models.progress` 列 | 实时进度 |
| 015/016/017 | story_levels / semantic_graph / IFC(build_mode 列) | 相邻能力 |
| 019 | `model_z_recovery_levels` | 跨视图标高,source(section/elevation/estimated),evidence_ref |
| 020 | `model_component_sections` | 构件截面(beam/column/slab/wall/pipe),estimated 标志 |
| 021 | `model_topology_relations` | host/beam_support/slab_support,覆盖式落库 |
| 022 | `model_quantities` | 混凝土净/毛、模板、钢筋、estimated_ratio、payload 下钻 |
| 023 | `symbol_spotting_logs` + 引擎种子 | spotting 调用日志 + primary/fallback 配置 |
| 024 | `model_review_actions`(append-only)+ `model_symbol_annotations` | 人审埋点(C-17 度量)+ 符号框金标签 |
| 025 | `story_levels` 人工标高列 | 楼层标高人工录入/校正通道 |
| 026 | `finding_status` | Finding 统一状态覆盖表(pending/acknowledged/remediated/closed),不回写来源表 |
| 027 | `pipeline_events` + `pipeline_suggestions` | 管线事件流水 + 建议待办(rebuild_model/create_proposal),同类型同项目仅保留一条 open |

---

## 13. 部署、开关与依赖

### 灰度开关

- **`vlm_semantic_enabled`**:A-13 VLM 语义融合。默认关闭,关闭时恒等无副作用。启用需接入 VLM API(本地 Ollama / 云端 DashScope,引擎 `drawing_semantic_vlm`,种子见 `migrations/018`)。

### 关键依赖(可选、懒加载、缺失即降级)

- **ifcopenshell**:IFC 生成/校验/glb。缺失→ifc_skipped。
- **CADTransformer(torch/dgl)**:符号识别学习模型。`requirements-spotting.txt` 锁定,懒加载,无 GPU/权重→回退 mock。
- **ultralytics(YOLOv8)**:设备补充检测,默认 COCO 权重对工程图能力有限,为可插拔增强位。
- **ODA File Converter**:DWG→DXF(`dwg_support`)。
- **That Open/Fragments(前端)**:`@thatopen/fragments`,IFC→`.frag` 转换与高性能渲染。

### 构建产物存储

- 图纸渲染贴图、IFC/glb/frag 资产存 MinIO,前缀 `projects/{id}/model_assets/`,访问走 300s presigned URL。

### 触发构建

- API:`POST /projects/{id}/model/rebuild`(异步 Celery)。
- 直接:`tasks/model_build.build_project_model`(最多重试 2 次)。

---

## 14. 运维与故障排查

| 症状 | 排查 |
|---|---|
| 模型长期 `building` 不动 | 查 Celery worker 是否存活、`model_build` 任务日志;`project_models.progress` 停在哪个阶段码 |
| `status=failed` | 读 `project_models.error`(截断 500 字符);常见:ODA/ifcopenshell 缺失、图纸文件损坏、MinIO 不可达 |
| 三维大量白框 | 图纸非矢量/过大(>25MB 跳贴图,>400 张线框占位)或渲染超时(90s);属正常降级 |
| 某层空构件 | 图纸未归属楼层(查 `unclassified_drawings`)或非矢量;引导用户走「楼层归属」标注后重建 |
| spotting 全 mock | 预期行为(C-09 前)。`GET /spot/backends` 看 active 后端;CADTransformer 需 GPU+权重 |
| 算量缺钢筋 | `rebar_missing=True`,需配筋输入;非缺陷 |
| 语义操作 409 | 乐观锁冲突,指引前端刷新到最新 version 重做 |
| 资产 URL 403 | key 前缀越权,检查是否跨项目取资产 |

日志与审计:所有状态变更写 `audit_logs`(append-only);spotting 走 `symbol_spotting_logs`;人审走 `model_review_actions`。

---

## 15. 安全遗留项(上线前必处理)

> ⚠️ 以下为已知遗留,部署到生产前必须处理:

1. **人工审核签字栏默认密码**:Phase C 人工审核双通道门禁(密码 + 电子签章预留)当前测试预设**默认密码 `000000`**(`data/model3d/phase_c_signoff.json`)。**上线前必须重设**,并妥善配置电子签章通道。
2. **模型子系统无角色门禁**:见 [§5](#5-权限与数据隔离模型)。若业务要求限制「重建/审校」到特定角色,需自行增加授权;当前任何登录用户均可操作。
3. **VLM 灰度**:`vlm_semantic_enabled` 生产启用前确认 VLM API 凭据从环境变量/密钥管理器读取,不硬编码。
4. **数据脱敏**:训练数据(C-06/C-07)须按 `PHASE_C_DATASET_SPEC.md` 脱敏后方可入库,test 集冻结仅终评解冻一次。

---

## 16. 事件编排层与管线建议(Phase D)

Phase D · D-08 新增一个轻量「事件编排层」(`apps/api/core/pipeline/`,路由 `routers/pipeline.py`,迁移 `migrations/027_pipeline_events.sql`),目标是把「审图完成」「模型建成」这类事件,转成人工可采纳的**建议待办**,而不是自动触发有副作用的动作。**设计铁律:自动打底、人工确认**——沿用楼层标高人工录入通道的成功模式。

### 16.1 两个建议类型

| 事件 | 建议 | 触发条件(默认阈值) |
|---|---|---|
| `ai_review.completed` | `rebuild_model`(建议重建模型) | 自上次建模以来变更的图纸数 ≥ `rebuild_impact_min_drawings`(默认 **1**,常量 `DEFAULT_REBUILD_IMPACT_MIN_DRAWINGS`) |
| `model.built` | `create_proposal`(建议创建创效提案) | 模型重建后钢筋等节约额 ≥ `auto_proposal_min_saving`(复用经济测算引擎既有参数,默认 **5000 元**) |

同一项目同一 `suggestion_type` 同时只保留一条 `open` 状态记录(`pipeline_suggestions` 唯一索引保证幂等,重复触发只刷新已有一条,不会无限堆积)。

### 16.2 开关:两个当前只能靠直接写库/裸 API 配置的键

开关**复用既有 `engine_params` 表**,`scope='pipeline'`:

| `param_key` | 说明 | 默认(无记录时) |
|---|---|---|
| `ai_review_to_rebuild_suggestion_enabled` | 全局开关 | `True`(缺省开) |
| `{project_id}:ai_review_to_rebuild_suggestion_enabled` | 项目级覆盖,优先于全局键 | 覆盖上一行 |
| `model_built_to_proposal_suggestion_enabled` | 全局开关 | `True`(缺省开) |
| `{project_id}:model_built_to_proposal_suggestion_enabled` | 项目级覆盖 | 覆盖上一行 |

> ⚠️ **已知产品缺口(如实记录)**:这两个开关**目前没有任何管理界面可配置**——`routers/admin/engine_params.py` 的 `EngineScope = Literal["kg", "economic", "ai_review", "rebar"]` **不包含 `"pipeline"`**,现有 `/admin/engine-params/{scope}` 系列端点会直接拒绝 `scope=pipeline` 的请求(路径参数类型校验失败),管理后台「引擎参数配置」页也没有对应 Tab。`core/pipeline/config.py` 的读取逻辑本身走的是裸 SQL(`SELECT param_value FROM engine_params WHERE scope=:scope AND param_key=:key`),不经过这个 admin 路由,所以**唯一可行的配置方式是直接对 `engine_params` 表执行 SQL**,例如:
>
> ```sql
> INSERT INTO engine_params (scope, param_key, param_value, updated_at)
> VALUES ('pipeline', 'ai_review_to_rebuild_suggestion_enabled', 'false', now())
> ON CONFLICT (scope, param_key) DO UPDATE SET param_value = 'false', updated_at = now();
> ```
>
> 要接入管理后台 UI,需要按 `core/pipeline/config.py` 顶部注释指引,在 `EngineScope` 中加入 `"pipeline"` 并在 `routers/admin/engine_params.py` 补充一份 schema(参照 `ECONOMIC_PARAM_SCHEMA` 写法)。这不在 D-22(文档同步)范围内,留作后续工作块。

### 16.3 前端消费现状

截至 2026-07-14,**数据看板的项目视图**(`pages/dashboard/PipelineStatusPanel`,D-15)已接入 `GET /{id}/pipeline/suggestions`,展示当前项目的未处理建议待办(「建议重建模型」「建议创建创效提案」),并提供「去处理」(accept + 跳转到对应模块)与「忽略」(dismiss)操作;无未处理建议时不渲染,保持安静。项目工作台、审查中心、模型页暂不展示这些建议。如需在界面外查询,可直接调用 API:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "$API_BASE/api/v1/projects/{project_id}/pipeline/suggestions?status=open"
```

数据看板已提供入口;在项目工作台/审查中心/模型页内联展示建议是后续增强(不在本次 D-22 范围)。

---

## 17. 路由迁移与重定向(Phase D)

Phase D 合并了多处同类入口(见 `docs/PHASE_D_BLUEPRINT.md` §0.3),前端路由层通过 `redirect` 做兼容,**旧路由不会 404,也没有删除对应页面源文件**(仅路由不再可达):

| 旧路径 | 新路径 | 说明 |
|---|---|---|
| `/` | `/hub` | 根路径重定向到项目工作台(原先无根路径重定向或指向图纸列表) |
| `/drawings/review-batches` | `/review` | 套图审查列表页并入审查中心 |
| `/drawings/review-batches/:id` | `/review` | 套图审查详情页并入审查中心;**注意:重定向到审查中心首页,不带 `:id` 参数**,不会自动定位到原批次,需在审查中心手动按项目/Tab 筛选 |

`apps/web/src/pages/drawings/ReviewBatch/` 目录下的旧页面源码**予以保留未删除**,仅路由层不再指向它,便于后续如需回滚或复用局部逻辑。

> 未发现 `docs/PHASE_D_BLUEPRINT.md` 曾设想的 `economic-calc → 算量中心` 重定向——因为钢筋翻样面板此前本就没有独立顶级路由(只嵌在图纸详情页内),不存在需要重定向的旧地址;算量中心(`/quantities`)是纯新增入口,不是替换。

---

## 18. 手册维护约定

**「边开发边更新」执行规则:**

1. 任何涉及以下内容的提交,须同步更新本手册与用户版对应章节:
   - 构建流程阶段变化(§3)
   - 新增/变更 API 端点(§4)
   - 权限模型调整(§5)
   - 能力边界/降级行为变化(§6、§11)
   - 算量口径、评测口径变化(§7、§8)
   - 数据库迁移涉及模型表(§12)
   - 依赖/开关变化(§13)
   - 安全项处理进展(§15)
   - 管线建议开关/阈值变化(§16)
   - 路由合并/重定向变化(§17)
2. 每次更新在两份手册文末「版本历史」各登记一行(版本号 + 日期 + 变更摘要)。
3. C-09 真实微调完成后,须更新 §2 的 M1 状态、§6 能力边界表、§8 评测结论。
4. 建议将本手册纳入 PR 检查清单:「是否需要更新 MODEL_MANUAL_*?」

---

## 版本历史

| 版本 | 日期 | 变更 |
|---|---|---|
| V1.0 | 2026-07-11 | 首版:覆盖 Phase 6/7 + 超级工程建模 Phase A/B/C 的架构、构建流程、API、能力边界、算量、融合评测、合规、降级、运维与安全遗留项 |
| V1.1 | 2026-07-14 | 阶段 3 楼板/墙识别接入 `classify_by_layer` 图层先验:楼板多板块收集 + 基础底板/筏板/承台(kind=raft, 0.5m)+ 地下室外墙宽缝召回(`_WIDE_WALL_GAP_MAX`);扩充 layer_conventions.yaml 与专业路由关键词。前端新增「楼层板片显隐」切换(用户手册 §7.6) |
| V1.2 | 2026-07-14 | Phase D(D-22 手册同步):新增第 16 章《事件编排层与管线建议》(D-08 两个建议类型/阈值、`engine_params scope=pipeline` 开关及其「无管理界面,只能直接写库」的已知缺口、前端消费现状——数据看板项目视图已接入)、第 17 章《路由迁移与重定向》(`/`→`/hub`、套图审查旧路由→`/review`,旧页面源码保留未删);§4 新增 4.6 Finding 统一聚合 API(`routers/findings.py`,migration 026)与 4.7 管线建议 API(`routers/pipeline.py`,migration 027),并在 4.1 补充 QTO 转创效提案端点当前无前端入口的说明;§12 补登 migration 025–027;§18(原§16)维护约定增补两条触发项 |
| V1.3 | 2026-07-16 | Phase E:§6.3 新增「图纸信息档案层 + PDF 几何识别」能力与诚实边界——档案层(导入即抽取/人审 verified/单一真相源,migration 029-031)、围护桩圆检测(整机 columns 3089→5794)、构件类型标签(档案 OCR 反哺);纯 PDF 项目边界(无图层/矢量文字取不到/圆检测/OCR回填滞后/板数十块量级)。详见 `docs/PHASE_E_BLUEPRINT.md`、`docs/PHASE_E_E3_AUDIT.md` |
