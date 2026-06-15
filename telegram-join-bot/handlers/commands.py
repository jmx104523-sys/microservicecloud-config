from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

START_TEXT = """👋 <b>加入审批机器人</b>

本机器人用于管理群组/频道的<b>加入申请</b>，确保新成员必须经过管理员同意才能进入。

<b>设置步骤：</b>
1. 将本机器人添加为群组/频道管理员
2. 授予权限：<b>邀请用户</b>（或管理聊天）
3. 在群组/频道设置中开启 <b>批准新成员</b>
4. 管理员先私聊本机器人发送 /start（用于接收审批通知）

<b>命令：</b>
/start - 显示帮助
/status - 查看机器人在当前聊天的状态

开启「批准新成员」后，用户申请加入时会通知管理员，由管理员点击按钮同意或拒绝。"""


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(START_TEXT, parse_mode=ParseMode.HTML)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return

    chat = update.effective_chat
    if chat.type == "private":
        await update.message.reply_text("请在群组或频道中使用 /status 命令。")
        return

    try:
        bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
    except Exception as exc:
        await update.message.reply_text(f"无法获取机器人状态：{exc}")
        return

    is_admin = bot_member.status in ("administrator", "creator")
    status_lines = [
        f"<b>聊天：</b>{chat.title}",
        f"<b>类型：</b>{chat.type}",
        f"<b>机器人是否为管理员：</b>{'是' if is_admin else '否'}",
    ]

    if is_admin and bot_member.can_invite_users is not None:
        status_lines.append(
            f"<b>邀请用户权限：</b>{'有' if bot_member.can_invite_users else '无'}"
        )

    if not is_admin:
        status_lines.append("\n⚠️ 请将机器人设为管理员并授予「邀请用户」权限。")
    else:
        status_lines.append(
            "\n✅ 请确保已在群组/频道设置中开启「批准新成员」。"
        )

    await update.message.reply_text(
        "\n".join(status_lines),
        parse_mode=ParseMode.HTML,
    )


def register_command_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
