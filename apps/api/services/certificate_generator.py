"""
创效激励兑现凭证 PDF 生成服务

使用 PyMuPDF（fitz）生成简洁的中文兑现凭证，无需外部字体依赖。
输出字节流，由调用方决定是否上传到 MinIO 或直接返回给前端。
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


def generate_certificate(
    proposal: dict[str, Any],
    distribution: dict[str, Any],
    approvals: list[dict[str, Any]],
    proposer_name: str,
    project_name: str,
) -> bytes:
    """
    生成兑现凭证 PDF，返回字节串。

    Args:
        proposal:     incentive_proposals 表行数据
        distribution: bonus_distributions 表行数据
        approvals:    proposal_approvals 列表（已签字）
        proposer_name: 提案人姓名
        project_name:  项目名称

    Returns:
        bytes: PDF 文件内容
    """
    try:
        import fitz  # type: ignore
    except ImportError as exc:
        raise RuntimeError("pymupdf 未安装，无法生成 PDF") from exc

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4

    # ── 颜色常量 ──────────────────────────────────────────────
    BLUE = (0.09, 0.46, 0.82)
    DARK = (0.15, 0.15, 0.15)
    GRAY = (0.5, 0.5, 0.5)
    WHITE = (1, 1, 1)
    GOLD = (0.85, 0.65, 0.13)

    def rect(x0, y0, x1, y1):
        return fitz.Rect(x0, y0, x1, y1)

    # ── 标题栏背景 ─────────────────────────────────────────────
    page.draw_rect(rect(0, 0, 595, 100), color=BLUE, fill=BLUE)

    # 主标题
    page.insert_text(
        (180, 48),
        "创效激励兑现凭证",
        fontsize=24,
        color=WHITE,
    )
    # 副标题
    page.insert_text(
        (210, 72),
        "INCENTIVE BONUS CERTIFICATE",
        fontsize=10,
        color=(0.85, 0.92, 1.0),
    )

    # 凭证编号（右上角）
    cert_no = f"CERT-{str(proposal.get('id', ''))[:8].upper()}"
    page.insert_text((430, 35), f"凭证编号", fontsize=8, color=WHITE)
    page.insert_text((430, 50), cert_no, fontsize=11, color=GOLD)

    y = 120

    # ── 分隔线辅助 ────────────────────────────────────────────
    def hline(y_pos: float) -> None:
        page.draw_line((40, y_pos), (555, y_pos), color=(0.85, 0.85, 0.85), width=0.5)

    def section_title(text: str, y_pos: float) -> float:
        page.draw_rect(rect(40, y_pos, 8, y_pos + 16), color=BLUE, fill=BLUE)
        page.insert_text((50, y_pos + 12), text, fontsize=11, color=BLUE)
        return y_pos + 24

    def kv(label: str, value: str, y_pos: float, label_x=40, value_x=180) -> float:
        page.insert_text((label_x, y_pos), label, fontsize=9, color=GRAY)
        page.insert_text((value_x, y_pos), value, fontsize=9, color=DARK)
        return y_pos + 18

    # ── 基本信息 ──────────────────────────────────────────────
    y = section_title("基本信息", y)
    y = kv("项目名称：", project_name or "—", y)
    y = kv("提案标题：", proposal.get("title", "—"), y)
    y = kv("提案人：", proposer_name or "—", y)
    y = kv("提案类型：", "A类（直接降本）" if proposal.get("proposal_type") == "A" else "B类（间接增收）", y)
    hline(y)
    y += 12

    # ── 经济核算结果 ───────────────────────────────────────────
    y = section_title("经济核算结果", y)
    net_saving = float(proposal.get("net_saving") or 0)
    bonus_pool = float(distribution.get("group_amount", 0)) + \
                 float(distribution.get("team_pool", 0)) + \
                 float(distribution.get("proposer_amount", 0))
    y = kv("净节约额：", f"¥ {net_saving:,.2f} 元", y)
    y = kv("激励奖金池：", f"¥ {bonus_pool:,.2f} 元", y)
    hline(y)
    y += 12

    # ── 铁三角分配明细 ─────────────────────────────────────────
    y = section_title("铁三角分配明细", y)

    # 表头背景
    page.draw_rect(rect(40, y, 555, y + 18), color=(0.93, 0.95, 1.0), fill=(0.93, 0.95, 1.0))
    page.insert_text((45, y + 13), "分配层级", fontsize=9, color=DARK)
    page.insert_text((220, y + 13), "比例", fontsize=9, color=DARK)
    page.insert_text((300, y + 13), "金额（元）", fontsize=9, color=DARK)
    page.insert_text((430, y + 13), "用途", fontsize=9, color=DARK)
    y += 22

    rows = [
        ("集团图纸深化创效中心", "20%", distribution.get("group_amount", 0), "年终评优 / 系统维护"),
        ("项目部奖金池",         "50%", distribution.get("team_pool", 0),   "项目经理二次分配"),
        ("直接提案人",           "30%", distribution.get("proposer_amount", 0), "次月随行工资发放"),
    ]
    for name, pct, amount, usage in rows:
        page.insert_text((45, y), name, fontsize=9, color=DARK)
        page.insert_text((220, y), pct, fontsize=9, color=BLUE)
        page.insert_text((300, y), f"¥ {float(amount):,.2f}", fontsize=9, color=DARK)
        page.insert_text((430, y), usage, fontsize=8, color=GRAY)
        y += 18
    hline(y)
    y += 12

    # ── 签字确认 ──────────────────────────────────────────────
    y = section_title("签字确认", y)
    role_label = {"project_manager": "项目经理", "economist": "经济师 / 商务总监"}
    for apv in approvals:
        if apv.get("signed_at") and apv.get("role") in role_label:
            signed_str = ""
            raw = apv.get("signed_at")
            if raw:
                if isinstance(raw, str):
                    signed_str = raw[:10]
                elif hasattr(raw, "strftime"):
                    signed_str = raw.strftime("%Y-%m-%d")
            label = role_label.get(apv["role"], apv["role"])
            y = kv(f"{label}：", f"✓ 已签字（{signed_str}）", y)
    hline(y)
    y += 12

    # ── 签发信息 ──────────────────────────────────────────────
    issued_at = datetime.now(timezone.utc).strftime("%Y年%m月%d日")
    y = kv("签发日期：", issued_at, y)
    y = kv("凭证编号：", cert_no, y)

    # ── 页脚免责声明 ──────────────────────────────────────────
    page.draw_rect(rect(0, 800, 595, 842), color=(0.96, 0.96, 0.96), fill=(0.96, 0.96, 0.96))
    page.insert_text(
        (40, 820),
        "本凭证由 CAD 图纸深化全过程管理平台自动生成，具有法律效力，请妥善保管。",
        fontsize=8,
        color=GRAY,
    )
    page.insert_text(
        (40, 834),
        f"铁三角比例（集团20%/项目50%/提案人30%）硬编码，总额不超过净节约额×激励比例。",
        fontsize=7,
        color=(0.7, 0.7, 0.7),
    )

    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()
