"""
Telegram 入群/入频道审核机器人

用户申请加入频道或群 → 通知管理员 → 管理员点击按钮通过/拒绝。

频道/群侧需先开启「批准新成员」，见 /help。
"""

import logging
import os
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    CommandHandler,
    ContextTypes,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = {int(x.strip()) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}
NOTIFY_CHAT_ID = os.environ.get("NOTIFY_CHAT_ID", "").strip() or None
WELCOME_MESSAGE = os.environ.get("WELCOME_MESSAGE", "").strip() or None
ALLOWED_CHAT_IDS = {
    int(x.strip())
    for x in os.environ.get("ALLOWED_CHAT_IDS", "").split(",")
    if x.strip().lstrip("-").isdigit()
}

HELP_TEXT = """<b>入群/入频道审核 Bot 配置说明</b>

<b>一、创建 Bot</b>
1. 找 @BotFather → /newbot → 拿到 Token
2. 设置环境变量 BOT_TOKEN

<b>二、频道设置（必须）</b>
1. 频道 → 编辑 → 隐私与成员
2. 打开 <b>批准新成员 (Approve New Members)</b>
3. 把本 Bot 加为频道管理员
4. 勾选权限：<b>邀请用户 (Invite users)</b>

<b>三、群组设置（必须）</b>
1. 群设置 → 群组类型 → 私有
2. 打开 <b>批准新成员</b>
3. 把本 Bot 加为群管理员
4. 勾选权限：<b>邀请用户</b>（可选：封禁用户）

<b>四、环境变量</b>
• ADMIN_IDS — 能点审批按钮的管理员 user id
• NOTIFY_CHAT_ID — 可选，通知发到管理群
• WELCOME_MESSAGE — 可选，通过后私聊欢迎语
• ALLOWED_CHAT_IDS — 可选，只处理指定频道/群 id

<b>五、命令</b>
/start — 检查 Bot 状态
/help — 本说明
/myid — 查看你的 user id（填 ADMIN_IDS 用）

开启审核后，用户点邀请链接会进入「待审批」，
Bot 会推送申请，管理员点 ✅通过 / ❌拒绝 即可。"""


def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def _chat_allowed(chat_id: int) -> bool:
    return not ALLOWED_CHAT_IDS or chat_id in ALLOWED_CHAT_IDS


def _format_user(req) -> str:
    user = req.from_user
    parts = [f"<b>{user.full_name}</b>"]
    if user.username:
        parts.append(f"@{user.username}")
    parts.append(f"ID: <code>{user.id}</code>")
    if req.bio:
        parts.append(f"简介: {req.bio[:200]}")
    return "\n".join(parts)


def _chat_label(chat) -> str:
    if chat.username:
        return f"@{chat.username}"
    return f"ID: <code>{chat.id}</code>"


def _approval_keyboard(chat_id: int, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ 通过", callback_data=f"approve:{chat_id}:{user_id}"),
                InlineKeyboardButton("❌ 拒绝", callback_data=f"decline:{chat_id}:{user_id}"),
            ]
        ]
    )


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user and _is_admin(update.effective_user.id):
        await update.message.reply_text(
            "入群审核 Bot 已运行。\n"
            "请确保频道/群已开启「批准新成员」，且 Bot 有「邀请用户」管理员权限。\n"
            "发送 /help 查看完整配置说明。",
        )
    else:
        await update.message.reply_text("本 Bot 用于管理员审批加入申请，普通用户无需操作。")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode="HTML")


async def myid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    await update.message.reply_text(
        f"你的 user id: <code>{user.id}</code>\n"
        f"用户名: @{user.username or '无'}\n\n"
        f"把 {user.id} 填入环境变量 ADMIN_IDS。",
        parse_mode="HTML",
    )


async def on_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    req = update.chat_join_request
    chat = req.chat
    user = req.from_user

    if not _chat_allowed(chat.id):
        logger.info("忽略未授权频道/群 chat_id=%s", chat.id)
        return

    chat_type = "频道" if chat.type == "channel" else "群组"
    text = (
        f"📥 <b>新的加入申请</b>\n\n"
        f"类型: {chat_type}\n"
        f"名称: <b>{chat.title}</b>\n"
        f"{_chat_label(chat)}\n\n"
        f"{_format_user(req)}\n\n"
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    if req.invite_link and req.invite_link.name:
        text += f"\n邀请链接: {req.invite_link.name}"

    keyboard = _approval_keyboard(chat.id, user.id)
    targets: list[int | str] = list(ADMIN_IDS)
    if NOTIFY_CHAT_ID:
        targets.append(NOTIFY_CHAT_ID)

    # 保存 user_chat_id，审批通过后用于私聊欢迎语
    context.application.bot_data[f"join:{chat.id}:{user.id}"] = req.user_chat_id

    for target in targets:
        try:
            await context.bot.send_message(
                chat_id=target,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("通知失败 target=%s", target)


async def _send_welcome(context: ContextTypes.DEFAULT_TYPE, user_chat_id: int) -> None:
    if not WELCOME_MESSAGE:
        return
    try:
        await context.bot.send_message(chat_id=user_chat_id, text=WELCOME_MESSAGE)
    except Exception:
        logger.exception("发送欢迎语失败 user_chat_id=%s", user_chat_id)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return

    if not _is_admin(query.from_user.id):
        await query.answer("无权限", show_alert=True)
        return

    data = query.data or ""
    try:
        action, chat_id_str, user_id_str = data.split(":", 2)
        chat_id = int(chat_id_str)
        user_id = int(user_id_str)
    except ValueError:
        await query.answer("数据无效", show_alert=True)
        return

    if not _chat_allowed(chat_id):
        await query.answer("该频道/群未授权", show_alert=True)
        return

    if action == "approve":
        ok = await context.bot.approve_chat_join_request(chat_id=chat_id, user_id=user_id)
        label = "✅ 已通过"
        if ok:
            user_chat_id = context.application.bot_data.pop(f"join:{chat_id}:{user_id}", None)
            if user_chat_id:
                await _send_welcome(context, user_chat_id)
    elif action == "decline":
        ok = await context.bot.decline_chat_join_request(chat_id=chat_id, user_id=user_id)
        label = "❌ 已拒绝"
    else:
        await query.answer("未知操作", show_alert=True)
        return

    if not ok:
        await query.answer("操作失败，可能申请已处理", show_alert=True)
        return

    admin_name = query.from_user.full_name
    await query.answer(label)
    if query.message and query.message.text:
        await query.message.edit_text(
            f"{query.message.text}\n\n<b>{label}</b> — {admin_name}",
            parse_mode="HTML",
        )


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("请设置 BOT_TOKEN")
    if not ADMIN_IDS:
        raise SystemExit("请设置 ADMIN_IDS")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("myid", myid_cmd))
    app.add_handler(ChatJoinRequestHandler(on_join_request))
    app.add_handler(CallbackQueryHandler(on_callback, pattern=r"^(approve|decline):"))

    logger.info("Bot 启动，管理员: %s", ADMIN_IDS)
    app.run_polling(allowed_updates=["message", "callback_query", "chat_join_request"])


if __name__ == "__main__":
    main()
