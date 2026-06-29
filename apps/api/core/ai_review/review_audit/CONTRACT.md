# 会审审查引擎 — 共享契约（所有相关 agent 必须遵守）

本文件是「审图功能升级（接入会审经验蒸馏协议）」并行开发的对齐基准。
知识来源：`~/work/031 图纸会审/03 整理数据库/06_认知蒸馏/`（19 专业、1909 条真实会审记录蒸馏）。

## 1. 知识资产 schema（`apps/api/data/review_protocol/`）

### disciplines.yaml — 19 专业全量
```yaml
disciplines:
  - code: ZH
    name_cn: 综合协调
    coarse: general          # 映射现有5粗专业: structure|architecture|mep|decoration|general
    priority_concerns: [标高, 做法, 尺寸]
    default_interfaces: [结构, 建筑, 给排水]
    risk_triggers:
      - 两个及以上专业图纸互相矛盾
      - 责任边界不清
    objects:
      - {name: 跨专业冲突点, level: 部位级}   # level: 部位级|系统级|节点级
      - {name: 责任边界,     level: 部位级}
```
19 专业代码：ZH JG WH JZ ZJ RF GJG JDQ GPS ZS DQ NT MQ SWT JGUAN JN JK RD XF。
coarse 映射建议：结构类(JG/WH/ZJ/RF/GJG/JK)→structure；建筑类(JZ/JN/MQ/JGUAN/SWT)→architecture；
机电类(JDQ/GPS/DQ/NT/RD/XF)→mep；装饰(ZS)→decoration；综合(ZH)→general。

### question_templates.yaml — 问题模板 + 对象模板
```yaml
templates:
  ZH:
    problem:        # 4 类：一致性/闭合性/接口冲突/施工验收
      - {type: 一致性, text: "[ZH/综合协调] 关于{对象}的标高，现平面图与剖面/节点/系统图表达不一致，按现图无法明确施工依据，请设计明确以哪张图为准，并补充{待明确}。"}
      - {type: 闭合性, text: "[ZH/综合协调] 现图中{对象}的做法表达不完整，尚无法明确其构造/连接/做法及施工控制点，请补充详图并明确{待明确}。"}
    object:         # 对象级模板，key=对象名
      跨专业冲突点: "[ZH/综合协调/部位级] 关于跨专业冲突点的{对象}，现图中其标高/做法/接口条件表达不足或互相矛盾，请设计明确{待明确}并补充对应图纸依据。"
```
占位符统一用 `{对象}` `{待明确}`（Python str.format 风格，缺值填空字符串）。

### concern_keywords.yaml — concern → 触发词
```yaml
concerns:
  标高: [标高, 绝对标高, 相对标高, 完成面, 结构面, 净高]
  做法: [做法, 构造, 大样, 节点做法]
  系统: [系统, 系统图, 回路, 支路]
  预留: [预留, 预埋, 留洞, 套管]
  # ...覆盖蒸馏文件出现的全部 concern
```

### location_patterns.yaml — 定位抽取正则
```yaml
patterns:
  drawings:  ['[A-Za-z]{1,4}-?\d{2,4}', '图号[:：]?\s*\S+']
  levels:    ['[B]?\d+\s*[层F]', '地下\s*\d+\s*层', '屋面']
  axes:      ['[\d①-⑳]+\s*[~～-]\s*[\d①-⑳]+\s*轴', '[A-Z]\s*轴']
  nodes_or_systems: ['节点\s*\d+', '[A-Z]{1,3}\d{1,3}系统']
  spaces:    ['机房', '泵房', '配电间', '卫生间', '楼梯间']
```

## 2. AIIssue 扩展字段（已落在 `core/ai_review/base.py`）

```
discipline_code:str  discipline_name:str  location:dict  concerns:list
issue_class:list  interface_primary:str  interface_related:list
risk_level:str(高|中|低)  object_level:str  standard_question:str  evidence_gap:list
```
review 引擎 engine 名固定为 `"review"`。severity 映射：高→major（命中安全/消防/主系统→critical）、中→minor、低→info。

## 3. 独立模块 API

`POST /api/v1/drawing-review/audit`
请求：`{discipline?:str, title:str, body:str, doc_type?:str, source_db?:str, related_disciplines?:[str]}`
响应统一信封 `{success, data, error}`，`data`：
```json
{
  "专业判断": {"code":"", "name":"", "basis":""},
  "定位信息": {"drawings":[], "levels":[], "axes":[], "nodes_or_systems":[], "spaces":[]},
  "核心concern": [{"label":"", "reason":""}],
  "问题归类": [],
  "接口复核": {"primary":"", "related":[], "reason":""},
  "风险等级": {"level":"高|中|低", "trigger":""},
  "建议动作": [],
  "证据缺口": [],
  "标准问题": []
}
```
批量：`POST /api/v1/drawing-review/audit-batch` 请求 `{items:[<audit请求>...]}` → `data:[<上述结构>...]`。

复用引擎：`from core.ai_review.review_audit.engine import audit_text`（纯文本审查入口，C 调用，不依赖图纸文件/db）。

## 4. 引擎模块入口约定（B 提供，C/E 调用）

```python
# core/ai_review/review_audit/engine.py
def audit_text(title: str, body: str, *, discipline: str | None = None,
               doc_type: str | None = None) -> dict:
    """纯文本会审审查，返回契约3 的 data 结构 dict。无 db / 无 LLM 也可运行。"""

class ReviewAuditEngine(BaseEngine):  # engine_name = "review"
    async def analyze(self, ctx: DrawingContext, db) -> list[AIIssue]: ...
```

## 5. 模型路由引擎名

新增 `review_question_writer`（闭环问题润色，LLM 可选，失败回退模板原句）。

---

# 契约 V2（对象识别 + 场景 + 问题包 + 文书化输出）

来源升级：`06_认知蒸馏/drawing-review-auditor/SKILL.md`（Output Format / Scenario Priority / Question Pack / Document）、
`06_Agent技能规范.md` 4C/4D/4E/4F + 第7节逐专业「场景模板/问题包模板/纪要口径模板/答复口径模板」、`07_技能抽样验收报告.md`。

## V2-1. 新增数据资产（`apps/api/data/review_protocol/`）

### scenario_templates.yaml — 场景模板（每专业 × 主对象 × 场景）
```yaml
scenarios:
  JG:
    - object: 梁、柱、板、墙、核心筒
      level: 部位级
      正常审图: "[JG/部位级/正常审图] 请核对梁、柱、板、墙、核心筒的图纸表达是否完整，现需确认其定位、做法、接口条件及施工依据是否已明确。"
      图间冲突: "[JG/部位级/图间冲突] 关于梁、柱、板、墙、核心筒，现平面/剖面/节点/系统图之间表达不一致，按现图无法统一施工依据，请明确以哪张图为准并补充修订依据。"
```
场景四值：正常审图 | 图间冲突 | 施工落地 | 验收风险。模板至少覆盖 正常审图 + 图间冲突；
施工落地/验收风险无专属模板时复用图间冲突模板并替换场景标签。

### question_pack_templates.yaml — 问题包模板
```yaml
packs:
  JG:
    主问题: "[JG/结构/{级别}] 关于{对象}的{concern}，现图中其标高/做法/接口条件表达不足或互相矛盾，请设计明确{待明确}并补充对应图纸依据。"
    补充问题: "[JG/结构/{级别}] 请同步核对{对象}涉及的接口专业图纸、节点或系统表达，明确责任边界、前置提资条件及最终闭环方式。"
    证据缺口: "请补充对应图号、层位、轴线、节点号/系统号及关联专业图纸依据后再闭环。"
```

### document_templates.yaml — 文书口径模板
```yaml
documents:
  JG:
    纪要口径:
      - {type: 问题条目, text: "关于{对象}，经审查发现当前图纸在{concern}方面存在{场景}问题，按现图无法明确最终执行依据；请设计单位明确修订图纸、责任专业及闭环时间。"}
      - {type: 责任条目, text: "建议由{主责专业}牵头，{接口专业}复核，形成书面修订依据后关闭问题。"}
      - {type: 结论条目, text: "当前按{场景}优先级处理，如未补充图纸依据则问题不得关闭。"}
    答复口径:
      - {type: 设计意图, text: "关于{对象}问题，设计意图为解决其{concern}控制要求。"}
      - {type: 执行依据, text: "图纸执行依据以最新修订图及对应节点详图为准。"}
      - {type: 修订说明, text: "应在对应平面、节点、系统图中补充或统一表达，保证图间口径一致后执行。"}
      - {type: 闭环条件, text: "仍存在接口冲突时，应补充跨专业复核结果及最终版本图纸后再关闭。"}
```

### disciplines.yaml 扩展
每个 object 补 `level` 与（可选）`concerns`；补全各专业对象（如 MQ 新增 `收边收口`(节点级)）。

## V2-2. AIIssue 扩展字段（已落 `core/ai_review/base.py`）
```
object_name:str  object_basis:str  scenario:str  scenario_reason:str
question_pack:dict({主问题,补充问题,证据缺口})  doc_minutes:list  doc_reply:list
```

## V2-3. audit_text 输出 schema V2（在 V1 9 key 上新增）
```json
{
  "对象识别": {"level":"部位级|系统级|节点级", "object":"", "basis":"显式命名|推定（依据…）"},
  "场景识别": {"name":"正常审图|图间冲突|施工落地|验收风险", "priority_reason":""},
  "问题包":   {"主问题":"", "补充问题":"", "证据缺口":""},
  "文书输出": {"会审纪要口径":[{"type":"","text":""}], "设计答复口径":[{"type":"","text":""}]}
}
```
`标准问题` 保留（= 问题包.主问题 +（如有）补充问题）。

## V2-4. 引擎模块入口（B 提供，C/E 调用）
```python
# object_identifier.py
def identify(discipline_code: str, concerns: list[dict], text: str) -> dict   # {level,object,basis}
# scenario_router.py  —— 优先级 图间冲突 > 施工落地 > 验收风险 > 正常审图
def route(text: str, risk: dict, issue_class: list) -> dict                    # {name,priority_reason}
# question_pack_builder.py
def build(discipline_code, obj: dict, scenario: dict, location: dict, concerns: list) -> dict
# document_writer.py
def write(discipline_code, obj, question_pack, interface) -> dict             # {会审纪要口径:[],设计答复口径:[]}
```

## V2-5. 独立 API 扩展
- `/audit` data 直接含 V2 section。
- 新增 `POST /api/v1/drawing-review/document`：`{title, body, discipline?, doc_kind:"minutes"|"reply"}` → 返回对应文书口径。

## V2-6. 数据库 migration 004
`ai_review_issues` 与 `review_audit_findings` 各 `ADD COLUMN IF NOT EXISTS`：
object_name varchar(64), object_basis varchar(32), scenario varchar(16), scenario_reason text,
question_pack jsonb, doc_minutes jsonb, doc_reply jsonb。
