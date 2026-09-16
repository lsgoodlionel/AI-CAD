# 数理化教材知识化（数学/物理/几何 → 合理性判据的出处）

> 资料：`/Users/lionel/work/02 book/数理化`（用户本地，9 份）
> 清单：`apps/api/core/knowledge/math_sources.py`（**单一来源**）
> 管线：`scripts/knowledge/build_math_markdown.py` → `ingest_math_to_db.py`
> 用途：给 `core/model3d/plausibility/` 的判据补**可回溯的出处**

## 1. 为什么做这件事

合理性引擎会据判据**否定构件**（`impossible` 档的结论可以直接从算量里剔除）。
规范那一侧已经立了规矩 —— `codes.py` 每条限值带条款号与原文摘录，查不到就标
`UNVERIFIED` 不编数。数学这一侧此前是空白：规则的 `basis` 里写着「鞋带公式」
「Jordan 曲线定理」「i=b/√12」，但它们**只是名字**，没有一处可回溯的原文。

一条判据若说不出「凭哪条定理、在哪本书第几页」，它和拍脑袋的区别只是听起来更专业。

## 2. 家底：9 份 → 7 份技术书 → 6 份已入库

| key | 书 | 页/节 | 文字来路 | 用途 |
|---|---|---|---|---|
| `strang-la` | Strang《Introduction to Linear Algebra》5e | 584 | 文本层 | 行列式即面积/体积、最小二乘 |
| `roads-geometry` | Wallace & West《Roads to Geometry》3e | 532 | 文本层 | 公理与解析几何、凸性、Jordan 曲线 |
| `pbm-animation` | House & Keyser《Physically Based Modeling and Animation》 | 36 节 | EPUB | 刚体、碰撞检测与相互穿透 |
| `multiple-view-geometry` | Hartley & Zisserman | 672 | 文本层 | 射影变换、RANSAC、鲁棒估计 |
| `engineering-math` | Siddiqi & Al-Lawati《Modern Engineering Mathematics》 | 851 | 文本层 | 向量分析、微分方程、数值方法 |
| `math-for-ml` | Deisenroth 等《Mathematics for Machine Learning》 | 417 | 文本层 | 概率、最优化、抽样与离群 |
| `knight-physics` | Knight《Physics for Scientists and Engineers》2e | 1450 | **OCR（定向）** | 静力平衡、力矩、惯性矩、弹性 |

**排除 2 份**（记在 `math_sources.EXCLUDED`，不是漏了）：霍金《时间简史》与
Ellenberg《Shape》是科普读物，没有可引用的公式或定理陈述。

### 三件实测出来的事

1. **Knight 是扫描件**。抽样三页文本层全部 0 字符，而其余六本在 500~3000 字符。
   1450 页全量 OCR 代价太高，改为**定向 OCR**：先 OCR 目录页拿到印刷页码
   （§6.1 平衡 152、§12.4 惯性矩 348、§12.8 静力平衡 360、§15.6 弹性 466），
   再用抽样页算出**印刷页 → PDF 页偏移 +32**（144→176、154→186 两处一致），
   只扫这四节约 29 页。
2. **EPUB 不按页引**。fitz 把它重排成 412 页，而 spine 只有 36 节；
   按 412 登记会让统计报出「缺页 376」这种假警报。登记按 **36 节**。
3. **数学排版的文本层抽取不干净**：实测 `det A` 抽成 `<let A`、`A⁻¹` 抽成 `A- 1`。
   所以公式表里 `quote` 保留抽取原样（便于回缓存定位），`expression` 另写
   规范化公式 —— 两者并列，不互相冒充。

## 3. 产物：公式的单一真相源

`core/model3d/plausibility/formulas.py` —— 18 条公式，每条带：

- `expression` 规范化公式、`symbols` 符号与单位；
- **`conditions` 成立条件**（最容易被忽略也最容易出事的一栏：鞋带公式只对
  *简单多边形*成立、Sutherland–Hodgman 裁剪只对*凸裁剪多边形*成立）；
- `source` 出处：`<书 key> p.<PDF 页>` / `DERIVED`（由本表其他条目推出）/
  `UNCITED:<查过什么>`；
- `quote` 原文抽取样。

**页码是 PDF 页**，不是书上印的页码 —— 实测 Strang 第 283 个 PDF 页印的是 273 页，
差 10。引用要能被复现，所以记能在缓存 `book.md` 的 `## p.N` 锚点定位到的那个。

**纪律**：带 `UNCITED` 的公式**不得用于 `impossible` 档结论**。那一档的含义是
「数学或物理上不可能」，凭一条找不到出处的公式说「不可能」，
是把没有依据说成了最强的依据。由 `tests/test_plausibility_formulas.py` 强制。

## 3·补 · 取证结果：19 条里 10 条有出处

| 出处 | 条数 | 例 |
|---|---|---|
| 文献 | 7 | 行列式即面积 `strang-la p.283` · 格林定理 `engineering-math p.319` · 鞋带（三角形展开）`strang-la p.287` · 凸包定义 `multiple-view-geometry p.531` · **刚体静力平衡 `knight-physics p.392`** · 最小二乘 `strang-la p.232` · RANSAC `multiple-view-geometry p.136` |
| `DERIVED` | 3 | 自交相消 ← 鞋带 + 行列式；形心 ← 格林定理；实心度 ← 凸包定义 |
| `UNCITED` | 9 | Jordan 曲线射线法、Sutherland–Hodgman 凸性要求、最小面积外接矩形；力学六条（惯性矩/回转半径/长细比/欧拉屈曲/简支梁弯矩/从属面积） |

### 四处「书上说的和骨架写的不一致」——这是真发现，不是格式问题

1. **`bh³/12` 与 `ML²/12` 是两个不同的量。** Knight §12.4、pbm-animation、
   engineering-math 里的 `moment of inertia` **全是质量惯性矩**（∫r²dm，kg·m²），
   而结构用的是截面**面积**二次矩（m⁴）。名字像、量纲不同 ——
   这是最容易误引的一处，已写进条目与测试注释，防止后人拿 Knight 把它
   从 `UNCITED` 名单里划掉。
2. **鞋带公式书上只有 n=3 的展开式**，「shoelace / surveyor's formula」这两个名字
   六本里一次都没出现。因此另立 `geometry.greens_theorem`：它把
   **「闭 + 简单（不自交）+ 正向」三个成立条件写成了可引用的原文定义** ——
   正是判据里最需要、骨架里最含糊的那一栏。
3. **单调链算法书上没有**（pbm 明说 "We will not discuss the process here"），
   于是改引凸包的**定义**（最小凸集），那正是下游真正用到的性质（σ ≤ 1）；
   算法本身未取证这一点写在 `conditions` 里。
4. **静力平衡的限定条件书上有明文，而骨架漏了一条**：Knight p.184 明说
   「质点的平衡条件只管不能转动的质点，扩展体需要额外条件」——
   柱和梁都是可转动的扩展体，**只验 ΣF=0 会漏掉倾覆**。已补进 `conditions`。

### 立竿见影的后果

存量模型（歌剧院 v85）上，`impossible` 档从 4607 条降到 **2295 条**：
只有轮廓自交那条依据链完整（格林定理 → 鞋带 → 行列式），得以保留；
「面积为零却有跨度」（1687）与「悬浮柱」（644）因为依赖的
`geometry.min_area_rect` / `polygon_clipping_convex_requirement` 尚无出处，
**自动降为 `implausible`**。补上出处后不用改代码就会升回去。

## 4. 这批书给不了什么（先说清楚）

`codes.py` 里标 `UNVERIFIED` 的四条 —— 柱轴压比限值、长细比容许值、板跨厚比、
混凝土强度设计值 —— 在 GB 50010/50017/50011，**通用数理教材里没有**，
不会因为这批书而被填上。它们是**知识库的缺口**，要补那几本规范。

## 5. 可复现

```bash
cd apps/api
./.venv/bin/python scripts/knowledge/build_math_markdown.py          # 文本层六本
./.venv/bin/python scripts/knowledge/build_math_markdown.py \
    --key knight-physics --ocr --pages 183-190,379-385,391-398,497-502
DATABASE_URL=postgresql://cad_user:cad_pass@localhost:5434/cad_db \
    ./.venv/bin/python scripts/knowledge/ingest_math_to_db.py
```

入库口径：`regulation_books.doc_kind='textbook'`（migration 050）——
**教材不是审图判据**，RAG 检索到「梁的挠曲微分方程」时必须知道它来自教材，
而不是当成规范条文去指控图纸违规。

## 6. 版权

与识图标准那批同一条纪律：原件是用户本地的出版物，派生全文只落本地缓存
（`.gitignore`）与数据库，**不进版本库、不随代码分发**；原件也**不上传 MinIO**
（图集要在工地看原件，数理教材没有这个场景，少一份分发少一分风险）。
仓库里留的是清单、管线代码与**短引用**（公式 + 页码），那才是可复用的部分。
