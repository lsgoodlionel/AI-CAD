"""Phase C 合规人工审核门禁测试（密码通道 + 电子签章预留通道 + OR 语义）。"""
from __future__ import annotations

import json

import pytest

from services import phase_c_signoff as sg

_AT = "2026-07-10T00:00:00+00:00"


@pytest.fixture()
def state() -> sg.SignoffState:
    return sg.default_state()


# ── 初始状态 ─────────────────────────────────────────────────────────────

def test_default_state_has_three_roles_all_unsigned(state):
    assert [r.role for r in state.roles] == list(sg.DEFAULT_REQUIRED_ROLES)
    assert state.is_approved is False
    assert state.pending_roles == sg.DEFAULT_REQUIRED_ROLES


def test_unknown_role_raises(state):
    with pytest.raises(sg.SignoffError):
        state.role("不存在的角色")


# ── 密码通道 ─────────────────────────────────────────────────────────────

def test_set_password_stores_hash_not_plaintext(state):
    updated = sg.set_password(state, "技术负责人", "s3cret-pw")
    entry = updated.role("技术负责人")
    assert entry.has_password
    assert entry.password_hash != "s3cret-pw"
    assert entry.signed is False  # 设密码 != 签核
    # 原状态不可变
    assert state.role("技术负责人").has_password is False


def test_sign_with_correct_password_marks_signed(state):
    state = sg.set_password(state, "技术负责人", "pw-good")
    state = sg.sign_with_password(state, "技术负责人", "pw-good", at=_AT)
    entry = state.role("技术负责人")
    assert entry.signed is True
    assert entry.method == sg.SIGN_METHOD_PASSWORD
    assert entry.signed_at == _AT


def test_sign_with_wrong_password_raises(state):
    state = sg.set_password(state, "技术负责人", "pw-good")
    with pytest.raises(sg.SignoffError, match="密码校验失败"):
        sg.sign_with_password(state, "技术负责人", "pw-bad", at=_AT)


def test_sign_without_password_set_raises(state):
    with pytest.raises(sg.SignoffError, match="尚未设定专用密码"):
        sg.sign_with_password(state, "技术负责人", "whatever", at=_AT)


# ── 电子签章通道（预留）──────────────────────────────────────────────────

def test_null_seal_verifier_never_passes():
    verifier = sg.NullSealVerifier()
    seal = sg.SealConfig(configured=True, fingerprint="fp")
    assert verifier.verify("技术负责人", seal, {"fingerprint": "fp"}) is False


def test_seal_sign_with_matching_fingerprint_marks_signed(state):
    state = sg.set_seal(state, "法务合规", "SEAL-FP-001")
    state = sg.sign_with_seal(
        state, "法务合规", {"fingerprint": "SEAL-FP-001"}, at=_AT
    )
    entry = state.role("法务合规")
    assert entry.signed is True
    assert entry.method == sg.SIGN_METHOD_SEAL


def test_seal_sign_with_wrong_fingerprint_raises(state):
    state = sg.set_seal(state, "法务合规", "SEAL-FP-001")
    with pytest.raises(sg.SignoffError, match="验签失败"):
        sg.sign_with_seal(state, "法务合规", {"fingerprint": "WRONG"}, at=_AT)


def test_seal_sign_without_registration_raises(state):
    with pytest.raises(sg.SignoffError, match="尚未登记电子签章"):
        sg.sign_with_seal(state, "法务合规", {"fingerprint": "x"}, at=_AT)


# ── OR 语义 + 门禁整体 ───────────────────────────────────────────────────

def test_or_semantics_password_or_seal_each_role(state):
    # 技术负责人走密码，法务走签章，负责人走密码 —— 混合通道全部通过
    state = sg.set_password(state, "技术负责人", "pw1")
    state = sg.sign_with_password(state, "技术负责人", "pw1", at=_AT)

    state = sg.set_seal(state, "法务合规", "FP2")
    state = sg.sign_with_seal(state, "法务合规", {"fingerprint": "FP2"}, at=_AT)

    assert state.is_approved is False  # 还差负责人

    state = sg.set_password(state, "Phase C 负责人", "pw3")
    state = sg.sign_with_password(state, "Phase C 负责人", "pw3", at=_AT)

    assert state.is_approved is True
    assert state.pending_roles == ()


def test_revoke_reopens_gate(state):
    for role in sg.DEFAULT_REQUIRED_ROLES:
        state = sg.set_password(state, role, "pw")
        state = sg.sign_with_password(state, role, "pw", at=_AT)
    assert state.is_approved is True

    state = sg.revoke(state, "技术负责人")
    assert state.is_approved is False
    assert state.role("技术负责人").signed is False
    assert state.pending_roles == ("技术负责人",)


# ── 序列化往返 ───────────────────────────────────────────────────────────

def test_round_trip_serialization(state):
    state = sg.set_password(state, "技术负责人", "pw")
    state = sg.sign_with_password(state, "技术负责人", "pw", at=_AT, note="审计已阅")
    state = sg.set_seal(state, "法务合规", "FP")
    restored = sg.from_dict(sg.to_dict(state))
    assert restored == state


def test_load_save_file_round_trip(state, tmp_path):
    state = sg.set_password(state, "技术负责人", "pw")
    path = tmp_path / "signoff.json"
    sg.save(state, path)
    loaded = sg.load(path)
    assert loaded == state
    # 落盘为合法 UTF-8 JSON
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["roles"][0]["role"] == "技术负责人"


def test_load_missing_file_returns_default(tmp_path):
    loaded = sg.load(tmp_path / "nope.json")
    assert loaded == sg.default_state()


def test_seeded_state_file_is_valid():
    """仓库内种子文件可被正确加载；当前为测试预设（默认密码 000000 已签核）。"""
    loaded = sg.load(sg.DEFAULT_STATE_PATH)
    assert [r.role for r in loaded.roles] == list(sg.DEFAULT_REQUIRED_ROLES)
    # 测试预设：三角色均已用默认密码 000000 完成签核，门禁 APPROVED
    assert loaded.is_approved is True
    assert all(r.method == sg.SIGN_METHOD_PASSWORD for r in loaded.roles)


def test_seeded_default_password_000000_verifies():
    """默认预设密码 000000 可通过校验（测试用，正式启用前须重设）。"""
    loaded = sg.load(sg.DEFAULT_STATE_PATH)
    for r in loaded.roles:
        assert sg.verify_password("000000", r.password_hash) is True
