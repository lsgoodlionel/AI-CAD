#!/usr/bin/env python3
"""Phase C 合规人工审核门禁 CLI（C-01 签字栏可执行版）。

子命令：
  set-password --role R      相关人员自设专用密码（交互式 getpass，不回显）
  set-seal     --role R --fingerprint FP   登记电子签章指纹（预留通道）
  sign         --role R      密码通道签核（交互式输入专用密码）
  seal-sign    --role R --seal-fingerprint FP   电子签章通道签核
  revoke       --role R      撤销某角色签核
  status                     打印各角色签核状态
  check                      门禁校验：全部签核→exit 0，否则→exit 1
      --enforce-if-model-code  仅当检测到 G3/G4 模型代码时才阻断（自我武装）

设计：``status`` / ``check`` 只读且**不依赖 passlib**（纯 stdlib），使 CI 无需
安装依赖即可运行门禁校验。``set-password`` / ``sign`` 才懒加载 passlib。
"""
from __future__ import annotations

import argparse
import getpass
import sys
from datetime import datetime, timezone
from pathlib import Path

# 让脚本可独立运行：把 apps/api 加入 import 路径
_API_ROOT = Path(__file__).resolve().parents[2]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from services import phase_c_signoff as sg  # noqa: E402

# G3/G4 模型代码存在性标记（审计门禁只在这些代码落地后才「武装」阻断）
_MODEL_CODE_MARKERS = (
    _API_ROOT / "core" / "model3d" / "spotting",
    _API_ROOT / "core" / "model3d" / "fusion",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _model_code_present() -> list[str]:
    """检测 G3/G4 模型代码是否已落地（存在含 .py 的 spotting/ 或 fusion/ 目录）。"""
    present: list[str] = []
    for marker in _MODEL_CODE_MARKERS:
        if marker.is_dir() and any(marker.rglob("*.py")):
            present.append(str(marker.relative_to(_API_ROOT)))
    return present


def cmd_set_password(args: argparse.Namespace) -> int:
    state = sg.load()
    pwd = getpass.getpass(f"为「{args.role}」设定专用密码: ")
    confirm = getpass.getpass("再次输入确认: ")
    if pwd != confirm:
        print("两次输入不一致，已取消。", file=sys.stderr)
        return 2
    sg.save(sg.set_password(state, args.role, pwd))
    print(f"OK：已为「{args.role}」设定专用密码（仅存哈希）。")
    return 0


def cmd_set_seal(args: argparse.Namespace) -> int:
    state = sg.load()
    sg.save(sg.set_seal(state, args.role, args.fingerprint))
    print(f"OK：已为「{args.role}」登记电子签章指纹（预留通道启用）。")
    return 0


def cmd_sign(args: argparse.Namespace) -> int:
    state = sg.load()
    pwd = getpass.getpass(f"输入「{args.role}」专用密码完成签核: ")
    state = sg.sign_with_password(state, args.role, pwd, at=_now_iso(), note=args.note)
    sg.save(state)
    print(f"OK：「{args.role}」已通过【密码】通道完成人工审核。")
    _print_gate(state)
    return 0


def cmd_seal_sign(args: argparse.Namespace) -> int:
    state = sg.load()
    payload = {"fingerprint": args.seal_fingerprint}
    state = sg.sign_with_seal(state, args.role, payload, at=_now_iso(), note=args.note)
    sg.save(state)
    print(f"OK：「{args.role}」已通过【电子签章】通道完成人工审核。")
    _print_gate(state)
    return 0


def cmd_revoke(args: argparse.Namespace) -> int:
    state = sg.load()
    sg.save(sg.revoke(state, args.role))
    print(f"OK：已撤销「{args.role}」签核。")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    state = sg.load()
    print(f"审计文档: {state.audit_doc}   门禁: {state.gate}")
    print("-" * 60)
    for r in state.roles:
        mark = "✔" if r.signed else "✘"
        channels = []
        if r.has_password:
            channels.append("密码")
        if r.has_seal:
            channels.append("签章")
        chans = "/".join(channels) or "未设凭据"
        via = f" via {r.method}@{r.signed_at}" if r.signed else ""
        print(f"  [{mark}] {r.role:<14} 可用通道: {chans}{via}")
    print("-" * 60)
    _print_gate(state)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    state = sg.load()
    approved = state.is_approved
    if args.enforce_if_model_code:
        code = _model_code_present()
        if not code:
            print("门禁未武装：尚无 G3/G4 模型代码（spotting/、fusion/），check 通过（顾问态）。")
            return 0
        print(f"检测到 G3/G4 模型代码 → 门禁已武装：{code}")
    if approved:
        print("APPROVED：所有必需角色人工审核完成，门禁通过。")
        return 0
    print(f"BLOCKED：待签核角色 → {list(state.pending_roles)}", file=sys.stderr)
    print("上线部署前须完成人工审核（密码或电子签章任一通道）。", file=sys.stderr)
    if args.warn_only:
        # 告警态（CI 用）：模型代码已在库，阻断合入是马后炮；真正的合规阻断点在
        # 部署前（不带 --warn-only 的严格 check）。此处只醒目告警、不阻断流水线。
        print(
            f"::warning::Phase C 人工审核门禁尚未签核（待签 {list(state.pending_roles)}）——"
            "CI 放行，但部署上线前必须完成真人签核。",
        )
        return 0
    return 1


def _print_gate(state: sg.SignoffState) -> None:
    if state.is_approved:
        print("→ 门禁状态: APPROVED（人工审核已完成）")
    else:
        print(f"→ 门禁状态: PENDING，待签核 {list(state.pending_roles)}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Phase C 合规人工审核门禁")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("set-password", help="相关人员自设专用密码")
    sp.add_argument("--role", required=True)
    sp.set_defaults(func=cmd_set_password)

    sp = sub.add_parser("set-seal", help="登记电子签章指纹（预留通道）")
    sp.add_argument("--role", required=True)
    sp.add_argument("--fingerprint", required=True)
    sp.set_defaults(func=cmd_set_seal)

    sp = sub.add_parser("sign", help="密码通道签核")
    sp.add_argument("--role", required=True)
    sp.add_argument("--note", default=None)
    sp.set_defaults(func=cmd_sign)

    sp = sub.add_parser("seal-sign", help="电子签章通道签核")
    sp.add_argument("--role", required=True)
    sp.add_argument("--seal-fingerprint", required=True)
    sp.add_argument("--note", default=None)
    sp.set_defaults(func=cmd_seal_sign)

    sp = sub.add_parser("revoke", help="撤销某角色签核")
    sp.add_argument("--role", required=True)
    sp.set_defaults(func=cmd_revoke)

    sp = sub.add_parser("status", help="打印签核状态")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("check", help="门禁校验（CI / 部署前消费）")
    sp.add_argument(
        "--enforce-if-model-code",
        action="store_true",
        help="仅当检测到 G3/G4 模型代码时才阻断（自我武装）",
    )
    sp.add_argument(
        "--warn-only",
        action="store_true",
        help="未签核时只告警不阻断（CI 用；部署前请勿加此标志，须严格阻断）",
    )
    sp.set_defaults(func=cmd_check)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except sg.SignoffError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
