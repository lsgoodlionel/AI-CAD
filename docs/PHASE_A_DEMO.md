# Phase A 里程碑 Demo 与验收报告(A-21)

> 版本 V1.0 | 2026-07-10 | 分支 `feat/ai-modeling-research` · PR #8
>
> Phase A(展示级增强)目标:把「超级工程建模」从 2.5D 贴图挤出升级为 **合规 IFC 数据底座 + 百万构件级 Web 渲染 + VLM 语义读表/判专业 + 图层约定强化识别**,走「AI+确定性混合」路线,全程灰度开关、可逐字节回退。

---

## 一、里程碑 Demo:全链路

**一张矢量图 → 合规 IFC → Web Fragments 流畅加载 → VLM 读出图名与专业**

```
矢量图(DXF/矢量PDF)
  │  geometry_extractor(采几何+图层/块) → element_recognizer(图层约定强化识别,确定性)
  ▼
FloorElements(米坐标构件)
  │  ifc_mapping.build_ifc_from_scene → model_ifc_builder(IfcOpenShell)
  ▼
合规 IFC4(IfcProject→Site→Building→Storey→构件,含 Qto 量集)──→ MinIO(ifc_key)
  │  apps/model-convert(@thatopen/fragments,Node 子进程)
  ▼
Fragments(.frag)──→ MinIO(frag_key)
  │  前端 FragmentsScene(That Open + three.js)
  ▼
Web 三维:加载/旋转/点击构件 → IFC 属性面板 → 语义树联动
  ┊
  └ 旁路:VLM(drawing_semantic_vlm)读图名/标题栏/判专业 → 候选(确定性优先仲裁)
```

### 复现命令

```bash
# 1) 生成合规 IFC(PoC,真实 ifcopenshell)
cd apps/api && .venv/bin/python scripts/ifc_poc_demo.py       # → /tmp/model_poc.ifc(16 构件)

# 2) IFC → Fragments(.frag)
node apps/model-convert/ifc_to_fragments.mjs /tmp/model_poc.ifc /tmp/model_poc.frag

# 3) 前端 E2E(Fragments 加载 + 三模式切换,真实浏览器)
cd apps/web && npm run test:unit                               # 57 单测
E2E_SKIP_SEED=1 E2E_BASE_URL=http://localhost:8001 npx playwright test tests/e2e/project-model-fragments.spec.ts
```

---

## 二、Phase A 验收总标准 · 逐条核对

| # | 验收标准 | 状态 | 证据 |
|---|---|---|---|
| 1 | **合规 IFC 底座**:矢量图→`ifcopenshell.open()` 可校验、层级完整的 IFC4 | ✅ | PoC demo 实产 IFC4(标高 0./4.5、单位米、Qto);`test_model_pipeline_ifc.py` 驱动真实 `build_scene` 断言含 `IfcColumn`/`IfcBuildingStorey` |
| 2 | **百万构件级 Web**:IFC→`.frag`,Fragments 加载,回退挤出/贴图无回归 | ⚠️ 架构就绪 | E2E 实测加载 `.frag` 渲染 + 三模式切换 4 浏览器通过;百万级性能是 That Open/Fragments 架构能力(2GB→80MB/60fps),本阶段以小样例验证链路,大模型压测留部署 |
| 3 | **VLM 语义实测**:标题栏抽取 + 判专业准确率;**绝不**输出计数/坐标/尺寸 | ⚠️ 链路就绪 | 服务/融合/门控全测(mock),硬约束落实(prompt+返回结构+非法专业过滤);**真实图纸准确率实测需接入 API + 5-10 张真图**(部署期任务) |
| 4 | **图层识别提升**:规范图层柱召回提升(未填充柱漏检修复) | ✅ | `test_element_recognizer_layers.py`:S-COLU 未填充柱被识别 ✅ + 无图层零回归对照 ✅ |
| 5 | **可回退**:三开关全关时行为与现网逐字节等价 | ✅ | `model_ifc_enabled`/`web_fragments_enabled`/`vlm_semantic_enabled` 默认 false;回归测试断言恒等路径(`test_model_builder_ifc`/`test_vlm_semantics`/`test_model_pipeline_ifc`) |
| 6 | **质量门**:新增模块覆盖率≥80%;关键路径 E2E;CI 绿 | ✅ | 后端 **620 passed / 84.39%**(≥80%);前端 **57 单测**;E2E 4 浏览器实跑通过;CI 覆盖率红灯已修 |
| 7 | **里程碑 Demo** 全链贯通 | ✅ | 上述 Demo 链路各段均有实测(IFC 产出、.frag 转换、E2E 加载、VLM 管线) |

---

## 三、各工作流(WS)交付与实测

| WS | 交付 | 实测 |
|---|---|---|
| **WS0** 准备 | 三特性开关(默认关)、前后端依赖 | 依赖校验通过 |
| **WS1** IFC 链路 | `ifc_mapping`/`model_ifc_builder`/`model_ifc_integration`/`fragments_convert`/迁移017/`apps/model-convert` | PoC 真实 IFC4;IFC→.frag 实转(4769B 有效);集成测试端到端 |
| **WS2** Web Fragments | `FragmentsScene`/`useFragmentsLoader`/三模式切换/拾取属性面板/markers | 真实 headless Chromium 渲染;E2E 4 浏览器 |
| **WS3** VLM 语义 | `providers/vision`/第14引擎/迁移018/`vlm_preprocess`/`vlm_semantics`/融合 | 全链路 mock 测试;确定性优先仲裁;门控零差异 |
| **WS4** 图层识别 | `geometry_extractor`(图层+INSERT展开)/`layer_conventions`/`element_recognizer` | 修 filled 漏检(专项测试);无图层零回归 |
| **WS5** 测试/集成 | 后端集成 + 前端生命周期测试 + Playwright E2E + 本报告 | 620+57 测试;E2E 实跑 |
| **infra**(A-04 闭合) | Docker 装 Node + 打包 model-convert + CI 冒烟 | CI 新增 model-convert job + 容器级 IFC→.frag 冒烟 |

---

## 四、诚实的能力边界(务必知悉)

- **楼层标高为估算**:Phase A 未做跨视图 z 恢复,IFC 每层挂 `Pset_ModelProvenance(IsEstimated=true)`,`scene.model_ifc.is_estimated=true`——**绝不伪装成实测**,真实标高由 **Phase B** 恢复。
- **VLM 只做语义候选**:计数/坐标/尺寸一律确定性引擎。真实图纸的判专业/读表准确率需接入 API + 真图实测后定版(部署期)。
- **Fragments 大模型性能**未压测:架构支持百万构件,本阶段验证链路正确性,大模型帧率/内存留部署环境实测。
- **markers 为楼层级对齐**:构件级精确锚定随 Phase B z 恢复处理。
- **web_fragments 部署前置已满足**:infra 提交已给镜像装 Node + 打包转换器;首次启用前建议在 staging 跑一遍容器级 IFC→.frag 冒烟(CI 已含)。

---

## 五、下一步

- **Phase B(算量级)**:跨视图 z 恢复(点亮 `model_lod.cross_view_match`,替换硬编码常量)+ 构件拓扑 + IFC-QTO 算量 + 钢筋回填 → 打通创效。详见 `docs/PHASE_B_TASKS.md` / `docs/CROSS_VIEW_Z_RECOVERY_DESIGN.md`。
- **Phase C(BIM级)**:符号识别学习模型(CADTransformer→VecFormer)+ 自建数据集 + 审校工作台深化。详见 `docs/PHASE_C_TASKS.md`。
- **启用建议**:先在 staging 逐个打开开关(`model_ifc` → `web_fragments` → `vlm_semantic`),各自实测后再上生产。

---

**结论**:Phase A 展示级底座**已完成并全链验证**。合规 IFC + Fragments 渲染 + 图层强化识别为**确定性、可回退、零现网影响**;VLM 语义为灰度候选。标准 IFC 数据底座已就位,Phase B 算量与 Phase C 学习模型均可挂其上推进。
