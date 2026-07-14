"""事件编排层（Phase D · 泳道3 · D-08）。

轻量事件驱动编排，用 Postgres 事件表 + Celery 异步消费串联散落的模块，
不引入新中间件。原则：自动打底、人工确认——本层只落「建议/待办」，
绝不自动执行三审/签字/重建等有副作用的硬动作。

对外主要入口：
- ``events.emit_event()``    记录事件 + 派发 Celery 异步消费
- ``handlers.dispatch()``    Celery 任务侧按事件类型路由到具体处理器
- ``config``                 项目级/全局开关与阈值读取（缺省开）
"""
from core.pipeline import config, events, handlers

__all__ = ["config", "events", "handlers"]
