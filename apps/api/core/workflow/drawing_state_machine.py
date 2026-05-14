"""
图纸深化状态机（transitions 库）

状态流：
  draft → ai_reviewing → ai_done → technical_review → economic_review
        → settlement_review → published

驳回：任意审批阶段 → draft（携带驳回原因）
"""
from transitions import Machine

# ── 状态定义 ──────────────────────────────────────────────────

DRAWING_STATES = [
    "draft",
    "ai_reviewing",
    "ai_done",
    "technical_review",
    "economic_review",
    "settlement_review",
    "published",
    "rejected",
]

# ── 流转定义 ──────────────────────────────────────────────────
# Guard 函数（conditions）在 API 层实现，状态机仅负责合法性检查。

DRAWING_TRANSITIONS = [
    # 提交 AI 审图
    {"trigger": "submit_for_ai",     "source": "draft",              "dest": "ai_reviewing"},
    # AI 审图完成（Celery 回调）
    {"trigger": "ai_complete",       "source": "ai_reviewing",       "dest": "ai_done"},
    # AI 审图失败，退回草稿
    {"trigger": "ai_fail",           "source": "ai_reviewing",       "dest": "draft"},
    # 一审启动（项目总工接受任务）
    {"trigger": "start_technical",   "source": "ai_done",            "dest": "technical_review"},
    # 一审通过 → 进入二审
    {"trigger": "approve_technical", "source": "technical_review",   "dest": "economic_review"},
    # 一审驳回 → 退回草稿
    {"trigger": "reject_technical",  "source": "technical_review",   "dest": "draft"},
    # 二审通过（经济师已签字）→ 进入三审
    {"trigger": "approve_economic",  "source": "economic_review",    "dest": "settlement_review"},
    # 二审驳回 → 退回草稿
    {"trigger": "reject_economic",   "source": "economic_review",    "dest": "draft"},
    # 三审通过（限额领料单已生成）→ 发布
    {"trigger": "approve_settlement","source": "settlement_review",  "dest": "published"},
    # 三审驳回 → 退回草稿
    {"trigger": "reject_settlement", "source": "settlement_review",  "dest": "draft"},
    # 重大变更升级（经济影响 ≥ 50万）：直接标记，路由到集团审批
    {"trigger": "escalate",          "source": ["technical_review", "economic_review"], "dest": None},
]


class DrawingWorkflow:
    """单张图纸的工作流实例（轻量，不含 DB 操作）"""

    def __init__(self, current_state: str):
        self.state = current_state
        self.machine = Machine(
            model=self,
            states=DRAWING_STATES,
            transitions=DRAWING_TRANSITIONS,
            initial=current_state,
            ignore_invalid_triggers=False,  # 非法触发器抛出 MachineError
        )

    def can_trigger(self, trigger: str) -> bool:
        """检查当前状态是否允许该触发器"""
        return trigger in self.machine.get_triggers(self.state)


def assert_valid_transition(current_state: str, trigger: str) -> None:
    """
    校验状态跳转合法性，非法时抛出 ValueError。
    在 API 层调用，作为第一道防线。
    """
    wf = DrawingWorkflow(current_state)
    if not wf.can_trigger(trigger):
        raise ValueError(
            f"当前状态 [{current_state}] 不允许执行 [{trigger}]，"
            f"合法触发器: {wf.machine.get_triggers(current_state)}"
        )


# 快捷别名：触发后的目标状态
_DEST_MAP: dict[str, str] = {t["trigger"]: t["dest"] for t in DRAWING_TRANSITIONS if t.get("dest")}  # type: ignore


def next_state(trigger: str) -> str:
    """根据触发器名称返回目标状态"""
    return _DEST_MAP[trigger]
