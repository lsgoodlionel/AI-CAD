---
name: drawing-review-auditor
description: Use when reviewing drawing review records, design disclosure records, design replies, or coordination notes that require generating discipline-level, object-level, scenario-level, and document-ready review questions from drawing-related evidence.
---

# Drawing Review Auditor

## Overview

Use this skill to turn drawing-review text into a structured review package. The goal is not to paraphrase records, but to identify the discipline, locate the drawing evidence, infer the object and scenario, generate high-value review questions, organize them into a question pack, and optionally convert them into meeting-minute or design-reply wording.

## When to Use

Use when the input includes one or more of the following:

- design disclosure records
- drawing review minutes
- design reply sheets
- issue lists tied to drawings, nodes, systems, elevations, dimensions, materials, sleeves, openings, or interfaces
- integrated coordination notes with unresolved drawing/entity conflicts

Do not use when the content is only administrative, attendance, scheduling, or workflow text without a real drawing/entity issue.

## Required Behavior

1. Determine the discipline before giving conclusions.
2. Extract location evidence before analyzing entity conflicts.
3. Treat concerns as audit entry points, not as the final output.
4. Infer the object level:
   - 部位级
   - 系统级
   - 节点级
5. Infer the active scenario:
   - 正常审图
   - 图间冲突
   - 施工落地
   - 验收风险
6. Organize outputs as a question pack, not a loose list of questions.
7. When requested, transform the question pack into meeting-minute wording or design-reply wording.

## Execution Protocol

1. Identify whether the record is single-discipline or integrated coordination.
2. Extract location cues:
   - drawing number
   - level
   - axis
   - node number
   - system number
   - room, equipment, or area name
3. Extract high-priority concerns:
   - elevation
   - dimension
   - node
   - detail/method
   - system
   - loop
   - reserved opening / sleeve / embedment
   - material
   - constructability
4. Infer required interface disciplines.
5. Classify the issue as one or more of:
   - missing expression
   - drawing conflict
   - interface conflict
   - constructability issue
   - acceptance/compliance risk
6. Infer the object:
   - physical part or area
   - system path or equipment/system group
   - detail/node/connection/embedded condition
7. Infer the scenario:
   - routine review
   - inter-drawing conflict
   - construction landing obstacle
   - acceptance or functional risk
8. Build a question pack:
   - 主问题
   - 补充问题
   - 证据缺口
9. If the user needs document-ready output, convert the question pack into:
   - 会审纪要口径
   - 设计答复口径

## Evidence Rules

- Do not give a hard entity conclusion when no location evidence exists.
- If discipline is inferred rather than explicit, state that it is an inference.
- If the text comes from one side only, remind the user that the original drawings still need to be checked.
- If the reply says "please clarify", "to be confirmed", or "coordinate on site", treat that as unresolved unless responsibility and completion condition are explicit.
- If object identity is weak, keep the question at discipline or concern level instead of inventing a fake object.

## Scenario Priority

When one object matches multiple scenarios, use this default priority:

1. 图间冲突
2. 施工落地
3. 验收风险
4. 正常审图

Rules:

- Resolve drawing inconsistency before expanding detailed construction questions.
- If safety, function, or acceptance is affected, move that scenario to the front.
- Do not output many equal-priority questions; choose the highest-value ones first.

## Question Pack Rules

Each standard output should prefer a compact question pack:

1. 主问题: the most blocking or highest-risk issue
2. 补充问题: interface, prerequisite, or secondary clarification
3. 证据缺口: missing drawing/location evidence needed for closure

Do not flood the output with low-density questions. Prefer 1 high-value main question plus 1 support question over a long flat list.

## Output Format

Return results in this structure:

```yaml
专业判断:
  code: ""
  name: ""
  basis: ""
定位信息:
  drawings: []
  levels: []
  axes: []
  nodes_or_systems: []
  spaces: []
核心concern:
  - label: ""
    reason: ""
问题归类: []
对象识别:
  level: 部位级|系统级|节点级
  object: ""
  basis: ""
场景识别:
  name: 正常审图|图间冲突|施工落地|验收风险
  priority_reason: ""
接口复核:
  primary: ""
  related: []
  reason: ""
风险等级:
  level: 高|中|低
  trigger: ""
问题包:
  主问题: ""
  补充问题: ""
  证据缺口: ""
文书输出:
  会审纪要口径: []
  设计答复口径: []
建议动作: []
```

## Object-Level Guidance

Use the inferred object to choose question style:

- `部位级`: ask about location, elevation, dimension, functional boundary, opening, finish, or access
- `系统级`: ask about continuity, path, capacity, coordination, sequence, and acceptance closure
- `节点级`: ask about detail completeness, connection, material, embedment, sealing, anchorage, or closure

If an object is explicit, keep the object name in the question. Do not flatten it into an abstract discipline statement.

## Document Output Guidance

### Meeting-Minute Wording

Use when the user wants review questions or coordination minutes.

Preferred structure:

- 问题条目: what is inconsistent or unclear
- 责任条目: who leads and who supports
- 结论条目: deadline, closure condition, or pending evidence

### Design-Reply Wording

Use when the user wants the output as a design response or revision explanation.

Preferred structure:

- 设计意图: what the design means
- 执行依据: which drawing or revision governs
- 修订说明: what changed
- 闭环条件: what else is needed before the issue is closed

Do not mix meeting-minute tone with design-reply tone.

## Discipline Routing

- `ZH / 综合协调`: prioritize cross-discipline conflict points, responsibility boundaries, integrated elevations, and sequence.
- `JG / 结构`: prioritize structural members, anchorage, openings, elevations, and detail completeness.
- `WH / 围护`: prioritize support system, water-stop logic, node closure, staged construction, and basement safety.
- `JZ / 建筑`: prioritize openings, stairs, roof/wet-area methods, fire/egress, and usable boundary conditions.
- `ZJ / 桩基`: prioritize pile position, pile top elevation, pile type, cap relationship, and sequencing.
- `RF / 人防`: prioritize protective units, openings, wall penetrations, dense details, and code/acceptance conditions.
- `GJG / 钢结构`: prioritize steel members, plates/bolts, weld/fireproof logic, and erection feasibility.
- `JDQ / 机电综合`: prioritize integrated elevations, trunk routing, reserved conditions, plant rooms, and installation sequence.
- `GPS / 给排水`: prioritize piping/valves, sleeves/openings, pump room interfaces, slopes, and fire-water consistency.
- `ZS / 装饰装修`: prioritize finish closure, ceiling endpoints, material logic, wet-area closure, and final appearance feasibility.
- `DQ / 电气`: prioritize loop/power path, cabinets, cable trays, grounding, and vertical shaft conditions.
- `NT / 暖通`: prioritize ducts/pipes, outlets/louvers, machine rooms, smoke control, and maintenance space.
- `MQ / 幕墙`: prioritize grids/openings, embeds/connections, edge closure, waterproof/drainage, and access/maintenance.
- `SWT / 室外总体`: prioritize site elevation, paving/roads, drainage, entrances, and municipal interfaces.
- `JGUAN / 景观`: prioritize landscape elevations, paving/features, greenery, drainage, and architecture/municipal interfaces.
- `JN / 节能`: prioritize insulation assemblies, parameter consistency, thermal-bridge logic, and compliance closure.
- `JK / 基坑`: prioritize excavation layers, dewatering, monitoring, load control, and staging logic.
- `RD / 弱电`: prioritize tray routing, cabinets/termination, point completeness, shafts, and coordination with electrical/fire.
- `XF / 消防`: prioritize system completeness, linkage, power, machine-room routing, and acceptance closure.

## Failure Handling

- Missing discipline: infer from title, subject, and terminology, and mark as inferred.
- Missing location: output `证据不足` and ask for drawing number, level, axis, node, or system identifiers.
- Weak object identity: keep output at discipline/concern level; do not fabricate a precise object.
- Multi-discipline mixed conflict: switch to integrated coordination mode instead of forcing a single-discipline answer.
- Unclosed response: ask for responsible party, drawing basis, and completion condition.

## Final Check

Before finishing, verify:

1. The issue is tied to a real drawing/entity location.
2. The conflict type is explicit.
3. The object level is explicit or explicitly marked uncertain.
4. The scenario is explicit and prioritized.
5. The question pack is compact and ordered.
6. The document wording, if used, matches the requested document type.
