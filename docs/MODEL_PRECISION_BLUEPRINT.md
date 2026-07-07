# 工程 3D 模型基座 V2 — 构件级高精度重建蓝图（Phase 7）

> 版本：V1.0 | 日期：2026-07-07 | 前置：Phase 6 模型基座（楼层堆叠+贴图+标记）
> 目标：从矢量 PDF/DXF 图纸提取真实几何，重建**总体 → 单体 → 楼层 → 构件（柱/墙/梁/板）
> → 机电（管线/设备）**分级的高保真 3D 模型；识图层接入 YOLOv8（ultralytics 已装）。

## 1. 精度分级承诺（务实边界，前端需按此表达）

| 数据源 | 重建等级 |
|--------|---------|
| 矢量 PDF 结构平面（柱/墙/板） | 构件级：真实轮廓提取 + 挤出（可靠） |
| 矢量 PDF 梁配筋图 | 梁近似：轴线间平行线对 → 梁体（标注截面缺省 300×600） |
| 矢量 PDF 机电平面 | 管线：长折线 → 管/桥架（按专业着色）；设备：闭合块/YOLO 框 → 体块 |
| DXF | 同上且更可靠（实体类型明确） |
| 扫描 PDF / 提不出矢量 | 回退 V1 贴图面板（楼层堆叠） |
| IFC | 真实几何 glb（已有） |

不承诺：效果图级外观、钢筋级细度、全自动无误差 BIM。构件均携带 `src`（来源图纸）可追溯。

## 2. 架构

```
图纸文件 ──► core/model3d/geometry_extractor.py     （PDF get_drawings / ezdxf 实体 → 几何原语）
              │ DrawingGeometry{lines,rects,filled_polys,texts,page_w,page_h}
              ▼
          core/model3d/element_recognizer.py        （轴网+比例尺 → 构件识别，单位：米）
              │ FloorElements{scale,axes,columns,walls,beams,slabs,pipes,equipment}
              ▼
services/model_builder.py（V2 组装）：单体分组（南区/北区…）→ 楼层 → 元素合并 + YOLO 设备补充
              │ scene JSON schema_version=2（V1 keys 全保留）
              ▼
前端 ModelViewer V2：构件挤出渲染（Extrude/Tube）+ 总体/单体切换 + 构件图层开关 + 贴图/构件/混合模式
```

## 3. core/model3d 模块契约（模块 G 所有）

```python
# core/model3d/types.py —— 纯 dataclass，坐标单位：页面点(pt)（extractor）/ 米（recognizer 输出）
@dataclass
class DrawingGeometry:
    page_w: float; page_h: float
    lines:  list[tuple[float, float, float, float]]        # (x0,y0,x1,y1)
    rects:  list[tuple[float, float, float, float, bool]]  # (x,y,w,h,filled)
    polys:  list[list[tuple[float, float]]]                # 闭合多边形（含填充路径）
    texts:  list[tuple[float, float, str]]                 # (x,y,content)

@dataclass
class FloorElements:
    scale: float                      # 米/点 换算系数
    axes: dict                        # {"x":[(label,pos_m)], "y":[(label,pos_m)]}
    columns:   list[dict]             # {outline:[[x,y]..], src:""}
    walls:     list[dict]             # {path:[[x,y],[x,y]], width:m, src:""}
    beams:     list[dict]             # {path:[[x,y],[x,y]], width:m, depth:m, src:""}
    slabs:     list[dict]             # {outline:[[x,y]..], thickness:m, src:""}
    pipes:     list[dict]             # {path:[[x,y]..], dia:m, system:"给排水|电气|暖通|消防|其他", src:""}
    equipment: list[dict]             # {outline:[[x,y]..], height:m, label:"", src:""}

# core/model3d/geometry_extractor.py
def extract_pdf_geometry(data: bytes, page_index: int = 0) -> DrawingGeometry
    # fitz page.get_drawings()（矢量路径）+ page.get_text("words")；异常/空矢量→空 DrawingGeometry
def extract_dxf_geometry(data: bytes) -> DrawingGeometry
    # ezdxf LINE/LWPOLYLINE/POLYLINE/SOLID/HATCH/TEXT/MTEXT；DWG 先 dwg_support.ensure_dxf

# core/model3d/element_recognizer.py
def recognize(geom: DrawingGeometry, discipline: str, drawing_id: str) -> FloorElements
```

### 识别规则（确定性几何启发式，全部无 LLM）
- **比例尺**：优先文本命中 `1[:：]\s*(50|100|150|200|500)`；否则由相邻轴线间距中位数≈8.4m 反推；
  再无 → 按 A1 图幅 1:100 缺省。识别失败在 FloorElements.scale 上仍给缺省值并继续。
- **轴网**：长直线（>60% 页宽/高）+ 端部圆圈内文本（①②…/A B C）→ 轴线集合；构件坐标全部
  平移到轴网原点（左下轴交点为 (0,0)），保证同单体各层对齐。
- **柱**：填充矩形/闭合小多边形，边长换算 0.2m~1.5m 之间，长宽比 <4 → column。
- **墙**：间距 0.1m~0.4m 的平行线对，重叠长度 >1m → wall（path=中线，width=间距）。
  discipline=structure 时全识别；architecture 时同规则（隔墙 0.08m~0.3m）。
- **梁**（仅 title/图名含「梁」的图）：柱心连线走廊内的平行线对（间距 0.15m~0.5m）→ beam，
  截面缺省 width=间距、depth=0.6m。
- **板**：最大闭合轮廓（外边界）→ slab（thickness 0.12m）；无闭合轮廓→轴网包络矩形。
- **管线**（discipline=mep）：总长 >3m 的折线 → pipe；system 判定按图名关键词
  （给排水/雨水/消防→给排水|消防，电气/桥架→电气，暖通/风管→暖通），dia 缺省 0.1m
  （桥架 width 0.2m 也用 pipe 表达）。
- **设备**（mep）：0.5m~5m 闭合矩形块 → equipment（height 1.5m，label 取块内文本）。

### 性能约束
- 单页原语上限 20000（超出截断并记 `truncated:true` 于 axes dict）；单图识别超 20s 放弃返回空
  （builder 降级贴图）。识别在线程池执行（复用 model_builder 现有 executor）。

## 4. scene JSON schema_version=2（模块 H 所有；V1 keys 全部原样保留）

```json
{
  "schema_version": 2,
  "buildings": [
    {"key": "south", "label": "南区（大、中歌剧厅）", "origin": [0, 0],
     "floors": [
       {"key": "F1", "label": "1层", "elevation": 1, "order": 1,
        "drawings": [<同 V1 floor.drawings 元素>],
        "elements": {"columns": [], "walls": [], "beams": [], "slabs": [],
                      "pipes": [], "equipment": []},
        "element_stats": {"columns": 0, "walls": 0, "beams": 0, "slabs": 0,
                           "pipes": 0, "equipment": 0}}
     ]}
  ],
  "floors": [<V1 原样（全部单体拍平），旧前端兼容>],
  "markers": [<V1，新增可选 building_key>],
  "cross_links": [...], "ifc_models": [...],
  "stats": {<V1 + "elements_total": {}, "buildings": n, "reconstruction": "elements|texture|mixed">},
  "generated_at": "..."
}
```

- **单体识别**：图名/标题正则 `南区|北区|东区|西区|[A-Z]\d?栋|\d+#楼|单体`；命中分组，
  未命中归 `main`（label=项目名）。buildings 按 key 排序，origin 由前端布局（后端恒 [0,0]）。
- **元素来源选择**：每楼层每类构件只取「最适图纸」——结构平面（图名含 墙柱|结构平面|模板）
  出 columns/walls/slabs；梁图（含 梁）出 beams；机电平面出 pipes/equipment；
  同类多图时全部识别后按 src 合并（不去重，前端按 src 过滤）。
- **YOLO 设备补充**（H）：`yolo_detector.detect_drawing_elements` 对 mep 图 PNG 检测，
  框中心映射回米坐标 → equipment（label="YOLO:<cls>"）；ultralytics/权重缺失静默跳过。
  注意：默认 COCO 权重对工程图纸识别能力有限，蓝图层面视为可插拔增强位（自训权重后生效），
  scene.stats 记 `yolo_equipment: n`。

## 5. 前端（模块 I 所有）

- `services/projectModel.ts`：追加 V2 类型（SceneBuilding/FloorElements/ElementColumn…），
  `ModelScene.schema_version?: number`、`buildings?: SceneBuilding[]`。
- `ModelViewer.tsx` + `sceneBuilder.ts` 升级：
  - `schema_version===2 且楼层有 elements` → 构件渲染：柱/墙/梁=ExtrudeGeometry（墙沿 path
    放样 width×层高；梁在层顶下挂 depth），板=Shape 挤出 thickness，管线=TubeGeometry
    （CatmullRomCurve3，按 system 着色：给排水 #1890ff 电气 #fa8c16 暖通 #52c41a 消防 #f5222d），
    设备=挤出体块（点击显示 label）。构件材质按类型配色（柱 #8c8c8c 墙 #bfbfbf 梁 #d9d9d9
    板 #f0f0f0 半透明）。
  - **视图分级**：总体（全部单体网格布局，间距 = 单体包络 + 10m）→ 点击单体聚焦（其余淡出）
    → 楼层隔离（已有）→ 构件点击（Drawer 显示类型/尺寸/来源图纸链接）。
  - **渲染模式切换**：`构件模式 | 贴图模式 | 混合`（混合=构件 + 楼层底面半透明贴图）；
    无 elements 的楼层自动回退贴图面板（V1 行为）。
  - **图层开关**：柱/墙/梁/板/管线(按 system)/设备/标记 逐类显隐。
  - 性能：同类构件用 InstancedMesh（柱）或合并 BufferGeometry（墙/梁），单场景目标 <100k 三角形；
    超限时该层降级贴图并提示。
- 兼容：schema_version 缺失 → 走现有 V1 渲染路径不回归。

## 6. 测试（全量 pytest ≥80% 门槛不许跌破）

| 模块 | 测试 | 要点 |
|------|------|------|
| G | tests/test_geometry_extractor.py | 用 fitz 程序化绘制（rect/line/text）构造 PDF → 提取断言；空 PDF/损坏字节降级空结构 |
| G | tests/test_element_recognizer.py | 合成 DrawingGeometry：柱阵/双线墙/轴网+比例文本/长折线 → 各类构件识别与米换算断言；超限截断 |
| H | tests/test_model_builder_v2.py | FakeDB + monkeypatch recognize → scene v2 契约（buildings/单体分组/elements/element_stats/V1 keys 保留）；识别失败楼层回退贴图 |
| I | tsc 过滤 | 相关文件 0 报错 |

## 7. 并行分工与文件所有权

| Agent | 独占文件 |
|-------|---------|
| G 几何提取与构件识别 | `core/model3d/`（新目录：types.py/geometry_extractor.py/element_recognizer.py/__init__.py）、tests/test_geometry_extractor.py、tests/test_element_recognizer.py |
| H 场景组装 V2 + YOLO 接线 | `services/model_builder.py`、tests/test_model_builder_v2.py（可微调 tests/test_model_builder.py 中仅因 scene 增 key 而断言过严的用例） |
| I 前端构件渲染 | `services/projectModel.ts`、`pages/model/ProjectModel/*` |

H 依赖 G 的接口签名（本蓝图第 3 节即契约，H 用 monkeypatch 先行开发，集成期主会话跑真实链路）。
公共约定同 Phase 5/6（中文注释/类型注解/函数 ≤50 行/文件 ≤800 行/不 git 操作）。
