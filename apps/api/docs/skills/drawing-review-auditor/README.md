# drawing-review-auditor 技能资产

本目录为「图纸会审审查」技能的提示词资产，来源于 1909 条真实图纸会审/设计交底记录的认知蒸馏
（原始库：`~/work/031 图纸会审/03 整理数据库/06_认知蒸馏/`）。

## 文件

- `SKILL.md` — 技能定义：执行协议、证据规则、19 专业路由、对象识别 / 场景优先级 / 问题包 / 文书化输出、失败处理。
- `prompt-template.md` — System / User / Output 提示词模板与升级规则。

## V2 升级（对象识别 + 场景 + 问题包 + 文书化输出）

V2 在 V1「专业判断 / 定位 / concern / 归类 / 接口 / 风险 / 标准问题」之上，
新增四段结构化输出，逐一对应一个独立引擎模块：

| V2 输出段 | 含义 | 引擎模块（`core/ai_review/review_audit/`） | 入口 |
|-----------|------|--------------------------------------------|------|
| 对象识别 | 问题指向的构件/部位/系统/节点及级别（部位级/系统级/节点级），区分「显式命名 / 推定 / 证据不足」 | `object_identifier.py` | `identify(discipline_code, concerns, text)` |
| 场景识别 | 四场景路由（正常审图 / 图间冲突 / 施工落地 / 验收风险），优先级 图间冲突 > 施工落地 > 验收风险 > 正常审图，高风险升级 | `scenario_router.py` | `route(text, risk, issue_class)` |
| 问题包 | 三段式（主问题 / 补充问题 / 证据缺口），主问题优先取场景模板、回退问题包模板填位，证据缺口随定位缺失动态生成 | `question_pack_builder.py` | `build(discipline_code, obj, scenario, location, concerns)` |
| 文书输出 | 两类文书口径不混写：会审纪要口径（问题/责任/结论条目）、设计答复口径（设计意图/执行依据/修订说明/闭环条件） | `document_writer.py` | `write(discipline_code, obj, question_pack, interface)` |

端到端入口 `engine.py::audit_text(...)` 串接上述模块，返回 V1 9 key + V2 4 key
（`对象识别 / 场景识别 / 问题包 / 文书输出`）；`标准问题[0]` 即 `问题包.主问题`。

## 与代码的关系

- 结构化知识（19 专业路由、问题/对象模板、concern 词典、定位正则、V2 场景模板 /
  问题包模板 / 文书口径模板）已工程化为 `apps/api/data/review_protocol/*.yaml`，
  由 `core/ai_review/review_audit/` 各模块经 `protocol_loader.py` 加载。
- 当模型路由层启用 `review_question_writer` 引擎做闭环问题润色时，
  `SKILL.md` 的「Required Behavior / Execution Protocol」与 `prompt-template.md` 的 System Prompt
  作为该引擎的系统提示词来源。
- 引擎默认走模板填空（无需 LLM、无需 db），本资产仅在启用 LLM 润色时生效，保持 graceful degradation。
