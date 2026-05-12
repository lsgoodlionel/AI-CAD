"""通知服务（企业微信 Webhook，预留扩展）"""
import logging
import httpx
from core.config import settings

logger = logging.getLogger(__name__)


async def send_wechat(text: str) -> None:
    """发送企业微信群机器人消息（未配置则静默跳过）"""
    if not settings.wechat_webhook_url:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                settings.wechat_webhook_url,
                json={"msgtype": "text", "text": {"content": text}},
            )
    except Exception as e:
        logger.warning("企业微信通知发送失败: %s", e)


async def notify_review_task(
    drawing_no: str,
    stage: str,
    reviewer_name: str,
) -> None:
    msg = f"【图纸审批】{drawing_no} 进入{stage}，请 {reviewer_name} 及时处理。"
    await send_wechat(msg)


async def notify_economic_signed(drawing_no: str, economist_name: str) -> None:
    msg = f"【二审签字】{drawing_no} 经济师 {economist_name} 已完成签字，图纸已解锁进入三审。"
    await send_wechat(msg)


async def notify_drawing_published(drawing_no: str, project_name: str) -> None:
    msg = f"【图纸发布】{project_name} - {drawing_no} 已通过三审并正式发布至班组。"
    await send_wechat(msg)


async def notify_proposal_submitted(title: str, proposer_name: str, project_name: str) -> None:
    msg = f"【创效提案】{project_name} 新提案「{title}」已由 {proposer_name} 提交，请商务部门及时核算。"
    await send_wechat(msg)


async def notify_proposal_sign_required(title: str, role_label: str) -> None:
    msg = f"【创效签字】提案「{title}」已完成经济核算，请 {role_label} 完成在线签字确认。"
    await send_wechat(msg)


async def notify_proposal_public_notice(title: str, project_name: str, notice_days: int) -> None:
    msg = f"【公示通知】{project_name} 提案「{title}」进入公示期（{notice_days} 天），公示期满后自动进入分配环节。"
    await send_wechat(msg)


async def notify_proposal_approved(title: str, proposer_name: str, bonus_pool: float) -> None:
    msg = f"【创效兑现】提案「{title}」（提案人：{proposer_name}）已完成奖金分配，奖金池 ¥{bonus_pool:,.2f} 元，请查收兑现凭证。"
    await send_wechat(msg)
