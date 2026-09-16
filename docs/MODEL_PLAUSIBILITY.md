# 现实存在合理性分析

> 模型里这根柱、这块板、这一层、这栋楼，**在现实世界里可能存在吗**。
>
> 代码：`apps/api/core/model3d/plausibility/`（纯函数、无 DB）
> · 编排：`services/model_plausibility.py` · 接口：`GET|POST /projects/{id}/model/plausibility`
> · 审图引擎：`core/ai_review/plausibility_engine.py` · 界面：模型页「审校」模式

## 一、它与金标准的分工

| | 金标准 | 合理性分析 |
|---|---|---|
| 问的问题 | 与图纸比对**得对不对** | 在现实世界里**立不立得住** |
| 手段 | 独立判读 + 事先声明的有效性检查 | 数学 / 物理 / 几何 / 规范下限，**纯计算** |
| 成本 | 要人、要时间、按批次 | 零人工，每次建模后自动跑 |
| 能回答 | 精确率、误检构成 | 哪些构件**一定**错了、错在哪一条 |
| 不能回答 | 实时 | 漏检（它只看得见已经建出来的东西） |

两者互补，谁也替代不了谁。**合理性分析抓不到「该有而没有」**——一根根本没被
识别出来的柱，在这里是隐形的。它的射程是「已经在模型里、但不可能存在」的那些。

## 二、三档严重度：按「凭什么否定它」分

不按「看起来多离谱」分。这一点决定了下游怎么用：`impossible` 可以直接从算量里
剔除，`suspect` 只能提示人看。

| 档 | 含义 | 依据类型 |
|---|---|---|
| `impossible` | 数学或物理上不成立 | 定理、守恒律（轮廓自交、负体积、构件悬浮、层高 ≤ 0）|
| `implausible` | 违反规范强制下限或工程量级 | 国标条款原文、量纲分析 |
| `suspect` | 统计离群，或所依赖的限值尚未取证 | 本模型内部分布 |

## 三、四条纪律（都对应本仓库付过的学费）

1. **每条结论必须带依据与数字**。`Finding.basis` 为空直接抛异常；`evidence`
   里放实测值、阈值、代入公式的中间量 —— 「柱太小」无法复核，
   `{"b_mm": 180, "limit_mm": 300}` 可以。
2. **跑不了 ≠ 通过**。缺数据的规则抛 `RuleNotApplicable`，进报告的 `skipped`，
   界面上单独列在结论之前。规则自己抛异常进 `errored`，与缺数据分开 ——
   混为一谈会把程序 bug 说成「这个模型没数据」，从而永远查不出来。
3. **阈值必须有出处**。`codes.py` 每条限值带规范号 + 条款号 + **原文摘录**；
   查不到就标 `UNVERIFIED` 并写清查过什么，且只能用于 `suspect` 档。
   本仓库有过先例：凭记忆写的平法代号表混进了非平法代号、比例表里有国标
   根本没有的 `1:75`。**据一个记错的数字否定真构件，比不查更坏。**
4. **截断要说出来**。落库每条规则只存前 50 条样例，但**计数完整**，
   被截断的规则列在 `truncated` 里 —— 不能让人以为只有这么多。

## 二·补 · 规则清单（22 条，五族）

| 族 | 规则 | 档 |
|---|---|---|
| 几何存在性 | `geom.self_intersecting_outline` 轮廓自交 | impossible |
| | `geom.zero_area_with_extent` 面积为零却有两向跨度 | impossible |
| | `geom.degenerate_outline` 退化环（相异顶点 <3 / 共线） | impossible |
| | `geom.non_positive_size` 尺寸 ≤ 0 | impossible |
| | `geom.absurd_extent` 单构件尺度超工程上限 | implausible |
| | `geom.duplicate_element` 同层同类近乎完全重合（**命中数 = 冗余构件数**） | implausible |
| | `geom.low_solidity` 实心度过低 | suspect |
| 尺寸下限 | `dim.column_section_below_code` / `beam_width` / `slab_thickness` / `wall_thickness` | implausible |
| | `dim.story_height_out_of_range` 层高越界 · `dim.non_positive_story_height` 层高 ≤ 0 | implausible / impossible |
| | `dim.column_aspect_ratio` 长宽比 > 4 的「柱」其实是墙 | implausible |
| 支承与重力 | `support.floating_column` 悬浮柱（整层过半时降级为「可能是转换层」） | impossible |
| | `support.story_z_overlap` 相邻层 z 区间交叠 | impossible |
| | `support.beam_without_support` 两端皆无支座 · `support.slab_without_edge_support` 板边无支承 | implausible |
| | `support.beam_support_count` **恰好一端有支座**（ΣF=0 且 ΣM=0：简支至少两支座；悬挑与嵌固是例外） | suspect |
| | `support.interpenetration` 异类构件互穿 | implausible |
| | `support.isolated_element` 离群构件 | suspect |
| 静力数量级 | `statics.column_axial_ratio` 轴压比 · `statics.column_slenderness` 长细比 · `statics.beam_span_depth` 高跨比 | implausible |
| 量纲守恒 | `qty.concrete_per_floor_area` · `qty.rebar_per_concrete` · `qty.element_density` · `qty.floor_area_vs_envelope` | implausible / suspect |

**两道降级闸，同一条纪律的前后两段**：限值未取证（`codes`）→ 结论降到 `suspect`；
公式未取证（`formulas`）→ `impossible` 降到 `implausible`。规则级严重度是**意图**
（import 期固定），真正落到结论上的是运行时那一道 —— 出处补上之后自动升回去，
不用改代码。

## 三·补 · 判据的出处：两张表，各管一半

| | 管什么 | 在哪 | 查不到时 |
|---|---|---|---|
| `codes.py` | **规范限值**（柱最小截面、活载标准值…） | GB 55xxx 通用规范原文 | 标 `UNVERIFIED` 保留占位，只能出 `suspect` |
| `formulas.py` | **数学/物理公式**（鞋带公式、静力平衡、惯性矩…） | 数理化教材（`docs/KNOWLEDGE_BASE_MATH_PHYSICS.md`） | 标 `UNCITED`，**不得用于 `impossible` 档** |

两条纪律同源：一条判据若说不出「凭哪条条款/哪本书第几页」，它和拍脑袋的
区别只是听起来更专业。而这一层的结论会被拿去**从算量里剔除构件** ——
据一个记错的数字或一条没出处的公式去否定真构件，比不查更坏。

`Formula` 里最容易被忽略、也最容易出事的一栏是 **`conditions`（成立条件）**：
鞋带公式只对**简单多边形**成立（所以自交环算出的面积不可信，这正是
`geom.self_intersecting_outline` 的立论基础）；Sutherland–Hodgman 裁剪只对
**凸裁剪多边形**成立（所以重叠面积按凸包算会偏大，阈值必须定在明显量级）。

## 四、已知能被它抓住的真实缺陷（都是实测发生过的）

| 现象 | 实测规模 | 对应规则族 |
|---|---|---|
| 轮廓自交（「蝴蝶结」）致鞋带面积正负相消、混凝土量算成零 | 存量 3166/10170 根柱（31.1%）、69/514 块板 | 几何存在性 |
| 兜底板把**图框**当成楼板，折算方量 519,684 m³ | 一次重建 | 量级守恒 |
| 总图与分图画同一个区域，柱被算几遍 | 一层多张图 | 几何存在性（重复）|
| 2 张离群图把场景包络撑到 4.8 公里，真实内容 760 米 | 第二工程 | 支承/离群 |
| 比例错导致构件整体缩小 1.5 倍 | .03C/.05C 落到缺省 1:100 | 尺寸下限 |

## 五、怎么跑

- **自动**：每次建模完成后由 `tasks/model_build.py` 跑一次并落库
  （失败只告警，不影响建模主流程）。
- **手动**：`POST /projects/{id}/model/plausibility/run`，或模型页「审校」模式
  →「现实合理性」面板 →「重新分析」。
- **读**：`GET /projects/{id}/model/plausibility` 返回**最近一次**结果，
  不即时重算 —— 免得一个只读接口悄悄做重活，也免得两次打开页面看到两份不同的数。
- **审图路径**：作为第 6 引擎并入 AI 审图（`plausibility`），构件由
  `core/ai_review/drawing_elements.py` 现场识别提供（仅 PDF、单图硬超时 60 秒、
  失败降级为「没有构件」并在引擎侧报降级）。单张图没有楼层关系，
  凡是要标高/层高/上下层的规则会如实 `skipped`。

## 六、数据与表

`model_plausibility_reports`（migration 052）：一行一次分析，
`report` 是按规则聚合并截断样例后的结果，`counts` 单独拎出来供排序。

## 七、边界（先说清楚它做不到什么）

- **抓不到漏检**：没建出来的构件在这里是隐形的。
- **重叠面积按凸包裁剪会高估**：判据是「重叠大到不可能」，高估让结论偏保守
  （更容易报），但不能拿它当精确体积。
- **静力校核只是数量级校核**：从属面积按「楼层面积 ÷ 柱数」粗估，不是真设计计算；
  代入量全部写进 `evidence`，供人复核。
- **标高是估的时候不做上下层判断**：拿估出来的标高去否定构件，等于用自己编的数做判据。
