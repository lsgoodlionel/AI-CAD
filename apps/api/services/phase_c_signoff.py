"""Phase C 合规人工审核门禁（C-01 签字栏的可执行升级）。

把 ``docs/PHASE_C_LICENSE_AUDIT.md`` §7 的纸面签字栏升级为**双通道**审核：

1. **专用密码通道**：相关人员各自设定专用密码（bcrypt 存哈希，明文不落盘）；
   审核时输入密码校验通过即完成本角色签核。
2. **电子签章通道（预留）**：``SealVerifier`` 抽象接口 + 默认占位实现，
   为后续接入真实 CA / 电子签章服务预留；配置指纹后可用参考实现验签。

**OR 语义**：同一角色「密码」或「电子签章」任一通过即视为该角色人工审核完成
（对应需求「密码签章两者一个通过则表示人工审核完毕」）。门禁整体通过 = 所有
必需角色均已签核。

设计约束：
- 纯逻辑 + 不可变数据结构（``@dataclass(frozen=True)``），所有变更返回新对象。
- ``load`` / ``from_dict`` / ``is_approved`` 等只读路径**不依赖 passlib**（懒加载），
  使 CI 的 ``check`` 无需安装依赖即可运行（对齐「离线可跑」约定）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol

# 状态文件默认位置（apps/api/data/model3d/phase_c_signoff.json）
DEFAULT_STATE_PATH = Path(__file__).resolve().parents[1] / "data" / "model3d" / "phase_c_signoff.json"

# 审计门禁默认必需角色（与审计文档 §7 签字栏一致）
DEFAULT_REQUIRED_ROLES = ("技术负责人", "法务合规", "Phase C 负责人")

SIGN_METHOD_PASSWORD = "password"
SIGN_METHOD_SEAL = "seal"


class SignoffError(Exception):
    """审核门禁操作异常（未知角色 / 密码错误 / 未设凭据 / 签章验签失败）。"""


# ── 数据结构（不可变契约）───────────────────────────────────────────────

@dataclass(frozen=True)
class SealConfig:
    """电子签章配置（预留）。

    ``fingerprint``：可信签章指纹 / 证书标识；``configured`` 为 True 表示该角色
    已登记电子签章，允许走签章通道。MVP 用指纹比对参考实现，后续可替换为
    CA 证书链 / PDF 数字签章校验，不改本契约。
    """
    configured: bool = False
    fingerprint: str | None = None


@dataclass(frozen=True)
class RoleSignoff:
    """单个必需角色的凭据与签核状态。"""
    role: str
    password_hash: str | None = None
    seal: SealConfig = field(default_factory=SealConfig)
    signed: bool = False
    method: str | None = None           # password | seal | None
    signed_at: str | None = None        # ISO 时间（由调用方注入，保证可复现/可测）
    note: str | None = None

    @property
    def has_password(self) -> bool:
        return bool(self.password_hash)

    @property
    def has_seal(self) -> bool:
        return self.seal.configured and bool(self.seal.fingerprint)


@dataclass(frozen=True)
class SignoffState:
    """Phase C 审核门禁总状态。"""
    audit_doc: str = "docs/PHASE_C_LICENSE_AUDIT.md"
    gate: str = "phase_c_model_code"
    roles: tuple[RoleSignoff, ...] = ()

    def role(self, name: str) -> RoleSignoff:
        for entry in self.roles:
            if entry.role == name:
                return entry
        raise SignoffError(f"未知角色：{name}（有效角色：{[r.role for r in self.roles]}）")

    @property
    def is_approved(self) -> bool:
        """门禁是否通过：所有必需角色均已签核（每角色密码或签章任一即可）。"""
        return bool(self.roles) and all(r.signed for r in self.roles)

    @property
    def pending_roles(self) -> tuple[str, ...]:
        return tuple(r.role for r in self.roles if not r.signed)


# ── 电子签章验证接口（预留，可插拔）─────────────────────────────────────

class SealVerifier(Protocol):
    """电子签章验签抽象。真实实现（CA / 数字签章）后续替换，接口不变。"""

    def verify(self, role: str, seal: SealConfig, payload: dict) -> bool: ...


class NullSealVerifier:
    """占位实现：电子签章模块尚未接入，一律不通过（预留态）。"""

    def verify(self, role: str, seal: SealConfig, payload: dict) -> bool:  # noqa: D401
        return False


class FingerprintSealVerifier:
    """参考实现：以登记指纹与呈递指纹逐字比对完成验签。

    用于在真实 CA 接入前提供一个**可用且可测**的签章通道，
    使「密码 OR 签章」的 OR 语义即刻成立。
    """

    def verify(self, role: str, seal: SealConfig, payload: dict) -> bool:
        if not (seal.configured and seal.fingerprint):
            return False
        presented = payload.get("fingerprint")
        return bool(presented) and presented == seal.fingerprint


# ── 密码哈希（懒加载 passlib，避免只读路径引入依赖）─────────────────────

def _pwd_context():
    from passlib.context import CryptContext  # 懒加载：check/status 无需 passlib

    return CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    if not plain:
        raise SignoffError("密码不能为空")
    return _pwd_context().hash(plain)


def verify_password(plain: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return _pwd_context().verify(plain, hashed)
    except (ValueError, TypeError):
        return False


# ── 纯变更操作（返回新状态）─────────────────────────────────────────────

def _replace_role(state: SignoffState, name: str, **changes) -> SignoffState:
    updated = tuple(
        replace(r, **changes) if r.role == name else r
        for r in state.roles
    )
    if not any(r.role == name for r in state.roles):
        raise SignoffError(f"未知角色：{name}")
    return replace(state, roles=updated)


def set_password(state: SignoffState, role: str, password: str) -> SignoffState:
    """相关人员自设专用密码（存哈希）。设定密码不等于签核。"""
    state.role(role)  # 校验角色存在
    return _replace_role(state, role, password_hash=hash_password(password))


def set_seal(state: SignoffState, role: str, fingerprint: str) -> SignoffState:
    """登记电子签章指纹（预留通道启用）。"""
    if not fingerprint:
        raise SignoffError("签章指纹不能为空")
    state.role(role)
    return _replace_role(state, role, seal=SealConfig(configured=True, fingerprint=fingerprint))


def sign_with_password(
    state: SignoffState, role: str, password: str, *, at: str, note: str | None = None
) -> SignoffState:
    """密码通道签核：校验专用密码通过 → 标记该角色人工审核完成。"""
    entry = state.role(role)
    if not entry.has_password:
        raise SignoffError(f"角色「{role}」尚未设定专用密码，无法用密码签核")
    if not verify_password(password, entry.password_hash):
        raise SignoffError(f"角色「{role}」密码校验失败")
    return _replace_role(
        state, role, signed=True, method=SIGN_METHOD_PASSWORD, signed_at=at, note=note
    )


def sign_with_seal(
    state: SignoffState,
    role: str,
    payload: dict,
    *,
    at: str,
    verifier: SealVerifier | None = None,
    note: str | None = None,
) -> SignoffState:
    """电子签章通道签核：验签通过 → 标记该角色人工审核完成。"""
    entry = state.role(role)
    if not entry.has_seal:
        raise SignoffError(f"角色「{role}」尚未登记电子签章（预留通道未启用）")
    verifier = verifier or FingerprintSealVerifier()
    if not verifier.verify(role, entry.seal, payload):
        raise SignoffError(f"角色「{role}」电子签章验签失败")
    return _replace_role(
        state, role, signed=True, method=SIGN_METHOD_SEAL, signed_at=at, note=note
    )


def revoke(state: SignoffState, role: str) -> SignoffState:
    """撤销某角色签核（如审计内容变更需重签）。"""
    state.role(role)
    return _replace_role(state, role, signed=False, method=None, signed_at=None)


# ── 序列化 ───────────────────────────────────────────────────────────────

def _role_to_dict(r: RoleSignoff) -> dict:
    return {
        "role": r.role,
        "password_hash": r.password_hash,
        "seal": {"configured": r.seal.configured, "fingerprint": r.seal.fingerprint},
        "signed": r.signed,
        "method": r.method,
        "signed_at": r.signed_at,
        "note": r.note,
    }


def _role_from_dict(d: dict) -> RoleSignoff:
    seal_raw = d.get("seal") or {}
    return RoleSignoff(
        role=d["role"],
        password_hash=d.get("password_hash"),
        seal=SealConfig(
            configured=bool(seal_raw.get("configured")),
            fingerprint=seal_raw.get("fingerprint"),
        ),
        signed=bool(d.get("signed")),
        method=d.get("method"),
        signed_at=d.get("signed_at"),
        note=d.get("note"),
    )


def to_dict(state: SignoffState) -> dict:
    return {
        "audit_doc": state.audit_doc,
        "gate": state.gate,
        "roles": [_role_to_dict(r) for r in state.roles],
    }


def from_dict(data: dict) -> SignoffState:
    return SignoffState(
        audit_doc=data.get("audit_doc", "docs/PHASE_C_LICENSE_AUDIT.md"),
        gate=data.get("gate", "phase_c_model_code"),
        roles=tuple(_role_from_dict(r) for r in data.get("roles", [])),
    )


def default_state() -> SignoffState:
    """初始状态：三个必需角色，均未设凭据、未签核。"""
    return SignoffState(
        roles=tuple(RoleSignoff(role=name) for name in DEFAULT_REQUIRED_ROLES)
    )


def load(path: Path | str = DEFAULT_STATE_PATH) -> SignoffState:
    p = Path(path)
    if not p.exists():
        return default_state()
    with p.open(encoding="utf-8") as fp:
        return from_dict(json.load(fp))


def save(state: SignoffState, path: Path | str = DEFAULT_STATE_PATH) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fp:
        json.dump(to_dict(state), fp, ensure_ascii=False, indent=2)
        fp.write("\n")
