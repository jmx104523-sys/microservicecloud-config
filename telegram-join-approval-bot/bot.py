"""
入群/入频道申请：通知管理员，由管理员点击按钮审批。

配置见 .env.example
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


def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def _format_user(req) -> str:
    user = req.from_user
    parts = [f"<b>{user.full_name}</b>"]
    if user.username:
        parts.append(f"@{user.username}")
    parts.append(f"ID: <code>{user.id}</code>")
    if req.bio:
        parts.append(f"简介: {req.bio[:200]}")
    return "\n".join(parts)


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
            "有人申请加入频道/群时，会推送通知，点按钮即可审批。"
        )
    else:
        await update.message.reply_text("本 Bot 仅供管理员使用。")


async def on_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    req = update.chat_join_request
    chat = req.chat
    user = req.from_user

    text = (
        f"📥 <b>新的加入申请</b>\n\n"
        f"频道/群: <b>{chat.title}</b>\n"
        f"{'@' + chat.username if chat.username else f'ID: {chat.id}'}\n\n"
        f"{_format_user(req)}\n\n"
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    if req.invite_link and req.invite_link.name:
        text += f"\n邀请链接: {req.invite_link.name}"

    keyboard = _approval_keyboard(chat.id, user.id)
    targets: list[int | str] = list(ADMIN_IDS)
    if NOTIFY_CHAT_ID:
        targets.append(NOTIFY_CHAT_ID)

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

    if action == "approve":
        ok = await context.bot.approve_chat_join_request(chat_id=chat_id, user_id=user_id)
        label = "✅ 已通过"
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
    app.add_handler(ChatJoinRequestHandler(on_join_request))
    app.add_handler(CallbackQueryHandler(on_callback, pattern=r"^(approve|decline):"))

    logger.info("Bot 启动，管理员: %s", ADMIN_IDS)
    app.run_polling(allowed_updates=["message", "callback_query", "chat_join_request"])


if __name__ == "__main__":
    main()
