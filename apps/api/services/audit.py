"""审计日志服务（只追加，不可修改）"""
import json
from typing import Any
from uuid import UUID


async def write_audit(
    db,
    *,
    user_id: UUID | str | None,
    action: str,
    resource: str,
    resource_id: UUID | str | None = None,
    old_state: dict | None = None,
    new_state: dict | None = None,
    ip_address: str | None = None,
) -> None:
    old_state_value = json.dumps(old_state, ensure_ascii=False, default=str) if old_state is not None else None
    new_state_value = json.dumps(new_state, ensure_ascii=False, default=str) if new_state is not None else None

    await db.execute(
        """
        INSERT INTO audit_logs
            (user_id, action, resource, resource_id, old_state, new_state, ip_address)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        user_id,
        action,
        resource,
        resource_id,
        old_state_value,
        new_state_value,
        ip_address,
    )
