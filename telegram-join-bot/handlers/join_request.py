from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from config import Config

APPROVE_PREFIX = "approve:"
DECLINE_PREFIX = "decline:"


def _callback_data(action: str, chat_id: int, user_id: int) -> str:
    return f"{action}{chat_id}:{user_id}"


def _parse_callback_data(data: str) -> tuple[str, int, int] | None:
    for prefix in (APPROVE_PREFIX, DECLINE_PREFIX):
        if data.startswith(prefix):
            payload = data[len(prefix) :]
            chat_id_str, user_id_str = payload.split(":", 1)
            return prefix, int(chat_id_str), int(user_id_str)
    return None


def _format_user(user) -> str:
    parts = [user.full_name]
    if user.username:
        parts.append(f"@{user.username}")
    parts.append(f"(ID: `{user.id}`)")
    return " ".join(parts)


def _format_chat(chat) -> str:
    title = chat.title or "未知群组"
    chat_type = "频道" if chat.type == "channel" else "群组"
    return f"{title} ({chat_type}, ID: `{chat.id}`)"


async def _is_chat_admin(bot, chat_id: int, user_id: int) -> bool:
    member = await bot.get_chat_member(chat_id, user_id)
    return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)


def _can_approve(config: Config, user_id: int, is_admin: bool) -> bool:
    if not is_admin:
        return False
    if not config.approver_ids:
        return True
    return user_id in config.approver_ids


async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    request = update.chat_join_request
    if not request:
        return

    user = request.from_user
    chat = request.chat
    config: Config = context.bot_data["config"]

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ 同意加入",
                    callback_data=_callback_data(APPROVE_PREFIX, chat.id, user.id),
                ),
                InlineKeyboardButton(
                    "❌ 拒绝加入",
                    callback_data=_callback_data(DECLINE_PREFIX, chat.id, user.id),
                ),
            ]
        ]
    )

    text = (
        "🔔 <b>新的加入申请</b>\n\n"
        f"申请人：{_format_user(user)}\n"
        f"目标：{_format_chat(chat)}\n\n"
        "请管理员点击下方按钮审批。"
    )

    admins = await context.bot.get_chat_administrators(chat.id)
    notified = 0
    for admin in admins:
        if admin.user.is_bot:
            continue
        if config.approver_ids and admin.user.id not in config.approver_ids:
            continue
        try:
            await context.bot.send_message(
                chat_id=admin.user.id,
                text=text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
            notified += 1
        except Exception:
            # 管理员可能未私聊过机器人，无法发送私信
            pass

    if notified == 0:
        try:
            await context.bot.decline_chat_join_request(chat.id, user.id)
        except Exception:
            pass
        try:
            await context.bot.send_message(
                chat_id=chat.id,
                text=(
                    "⚠️ 收到加入申请，但无法通知管理员（请确保管理员已与机器人私聊 /start）。"
                    f"\n申请人：{user.full_name} (ID: {user.id})"
                ),
            )
        except Exception:
            pass


async def handle_approval_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query or not query.data:
        return

    parsed = _parse_callback_data(query.data)
    if not parsed:
        await query.answer("无效操作", show_alert=True)
        return

    action, chat_id, applicant_id = parsed
    operator = query.from_user
    config: Config = context.bot_data["config"]

    is_admin = await _is_chat_admin(context.bot, chat_id, operator.id)
    if not _can_approve(config, operator.id, is_admin):
        await query.answer("只有管理员可以审批加入申请", show_alert=True)
        return

    try:
        if action == APPROVE_PREFIX:
            await context.bot.approve_chat_join_request(chat_id, applicant_id)
            result = "已同意"
        else:
            await context.bot.decline_chat_join_request(chat_id, applicant_id)
            result = "已拒绝"
    except Exception as exc:
        await query.answer(f"操作失败：{exc}", show_alert=True)
        return

    await query.answer(f"{result}加入申请")
    if query.message:
        await query.message.edit_text(
            f"{query.message.text_html}\n\n"
            f"——\n<b>{operator.full_name}</b> {result}该申请",
            parse_mode=ParseMode.HTML,
            reply_markup=None,
        )


def register_join_request_handlers(app: Application) -> None:
    app.add_handler(
        CallbackQueryHandler(
            handle_approval_callback,
            pattern=f"^({APPROVE_PREFIX}|{DECLINE_PREFIX})",
        ),
        group=0,
    )
