# Phase C 预处理器输出 Schema（C-02）

> 版本 V1.0 | 2026-07-10 | 任务 C-02（泳道 B｜数据）
>
> 模块：`apps/api/core/model3d/preprocess/`　CLI：`apps/api/scripts/model3d/preprocess_drawing.py`
> 上位：`docs/PHASE_C_TASKS.md` C-02；契约源 `apps/api/core/model3d/preprocess/schema.py`

## 1. 目的

模型（CADTransformer / VecFormer）**不吃 DXF/DWG**，只吃 **SVG + 图元序列**。本预处理器
把 DXF/DWG/PDF 统一转为「图元 JSON + SVG」，作为 C-04 自动标注与 C-08/C-09 模型输入的**统一入口**。

复用现有栈：`geometry_extractor`（精确图元 + 图层/块并行列表）、`dwg_support`（DWG→DXF）。

## 2. 图元 JSON 契约（`schema_version: 1`）

```jsonc
{
  "schema_version": 1,
  "units": "pt",                 // 页面点（与 DrawingGeometry 一致）
  "page": { "w": 500.0, "h": 800.0 },
  "counts": { "line": 1, "rect": 1, "polyline": 1, "text": 1 },
  "primitives": [
    {
      "id": 0,                   // 文档内唯一
      "type": "line",            // line | rect | polyline | text
      "layer": "S-BEAM",         // CAD 图层名（弱标签溯源，无则 ""）
      "block": "",               // 所属 INSERT 块名（无则 ""；见 §4 已知限制）
      "points": [[50,100],[400,100]],
      // 可选字段（按 type 出现）：
      "filled": false,           // rect / polyline
      "closed": true,            // polyline（首尾同点）
      "content": "+4.200",       // text
      "color": null,             // 预留（当前恒 null）
      "linetype": null           // 预留（当前恒 null）
    }
  ],
  "warnings": []                 // 降级信息（不支持扩展名/提取失败/空图）
}
```

**点序列约定**：line 两点；rect 四角（顺时针 `(x,y)→(x+w,y)→(x+w,y+h)→(x,y+h)`）；
polyline N 点；text 单点（位置）。

## 3. SVG 契约

- 完整 SVG 文档，`viewBox="0 0 page_w page_h"`，坐标沿用页面点。
- 每图元附 `data-id` / `data-layer` / `data-block`，供标注/训练/审校溯源到原始 CAD 元数据。
- text 内容经 XML 转义；SVG 保证 well-formed（测试以 `minidom.parseString` 断言）。

## 4. 已知限制（诚实标注）

1. **默认预处理路径（`preprocess_drawing`）线段不携带块名**：`DrawingGeometry` 契约中
   `lines` 无 `*_blocks` 并行列，块内 lwpolyline 被分解为线段 → 这些线段 `block` 为 `""`。
   **已由 C-03 `block_expander.expand_blocks()` 解决**：该 ML 数据专用展开器在**每个**展开
   图元（含线段）上保留来源块名 + 图层（见 §6）。需要块级弱标签时走 `expand_blocks`，
   仅需 SVG/展示时走 `preprocess_drawing`。
2. **color / linetype 预留**：`geometry_extractor` 当前不提取颜色/线型，恒为 `null`；
   后续增强填充，不改本契约。
3. **PDF 无图层/块**：PDF 来源图元 `layer`/`block` 恒 `""`（PDF 无此概念）。
4. **大幅面**：A0/A1 切图策略未在 C-02 覆盖（对齐上位方案「大幅面细节进模型前丢失」告警）。

## 5. C-03 块展开器与归一化

- **`expand_blocks(data) -> PrimitiveDoc`**（`preprocess/block_expander.py`）：ezdxf 递归展开
  `INSERT`（嵌套块、缩放/旋转、**MINSERT 阵列经 `multi_insert` 逐格展开**），每个图元携带
  **顶层块名 + 自身图层**作为弱标签溯源（供 C-04）。递归深度上限 `MAX_BLOCK_DEPTH=16`、
  图元上限 `MAX_PRIMITIVES`，异常优雅降级。修复 §4.1 的线段丢块名缺口。
- **`normalize_doc(doc) -> (PrimitiveDoc, NormalizeParams)`**（`preprocess/normalize.py`）：
  **等比缩放**到单位域 [0,1]（保持长宽比，避免各向异性拉伸），返回可逆参数
  `norm = (raw - offset) × scale`，供 NN 输入与 provenance。纯函数、不可变。

## 6. 优雅降级

提取失败 / 不支持扩展名 / 空图**一律返回空文档 + warning，绝不抛异常**
（对齐 `element_recognizer` 风格），保证批处理不因单图中断。
