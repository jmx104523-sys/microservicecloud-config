"""
星弟搜索 — Telegram 采集 + 搜索机器人

- 采集源：管理员可新增/删除群、频道（持久化到 SQLite）
- 搜索入口：仅在配置的搜索群内可用（私聊仅管理员）
- 广告位：ads/ads.json
"""

from __future__ import annotations

import logging
import os
import time
from html import escape
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ads_loader import AdsConfig
from db import SearchIndex

load_dotenv()

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("xingdi-search")

ROOT = Path(__file__).resolve().parent

BOT_TOKEN = os.environ["BOT_TOKEN"]
BOT_NAME = os.environ.get("BOT_NAME", "星弟搜索")
ADMIN_IDS = {
    int(x.strip())
    for x in os.environ.get("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
# 启动时种子：搜索入口群 / 采集源（可被后续命令覆盖、追加）
SEED_SEARCH_CHAT_IDS = {
    int(x.strip())
    for x in os.environ.get("ALLOWED_CHAT_IDS", "").split(",")
    if x.strip().lstrip("-").isdigit()
}
SEED_SOURCE_CHAT_IDS = {
    int(x.strip())
    for x in os.environ.get("SOURCE_CHAT_IDS", os.environ.get("ALLOWED_CHAT_IDS", "")).split(",")
    if x.strip().lstrip("-").isdigit()
}
PAGE_SIZE = max(1, int(os.environ.get("PAGE_SIZE", "8")))
DB_PATH = os.environ.get("DB_PATH", str(ROOT / "data" / "search.db"))
ANTI_LOST_URL = os.environ.get("ANTI_LOST_URL", "").strip()
ADS_PATH = os.environ.get("ADS_PATH", str(ROOT / "ads" / "ads.json"))

index = SearchIndex(DB_PATH)
ads = AdsConfig(ADS_PATH)


def _is_admin(user_id: int | None) -> bool:
    return bool(user_id and user_id in ADMIN_IDS)


def _is_search_chat(chat_id: int | None) -> bool:
    return bool(chat_id and index.is_search_chat(chat_id))


def _is_source_chat(chat_id: int | None) -> bool:
    return bool(chat_id and index.is_source(chat_id))


def _detect_media_type(message) -> str:
    if message.photo:
        return "photo"
    if message.video:
        return "video"
    if message.document:
        return "document"
    if message.audio or message.voice:
        return "audio"
    if message.animation:
        return "animation"
    return "text"


def _message_text(message) -> str:
    parts = []
    if message.text:
        parts.append(message.text)
    if message.caption:
        parts.append(message.caption)
    return "\n".join(parts).strip()


def _render_ads_block() -> str:
    lines: list[str] = []
    if ads.top_ad:
        lines.append(escape(ads.top_ad))
    if ads.hot_searches:
        hot = " · ".join(escape(x) for x in ads.hot_searches[:8])
        lines.append(f"🔥 热搜: {hot}")
    if ads.service_links:
        links = []
        for item in ads.service_links[:8]:
            label = escape(str(item.get("label", "链接")))
            url = str(item.get("url", "")).strip()
            if url:
                links.append(f'<a href="{escape(url)}">{label}</a>')
            else:
                links.append(label)
        if links:
            lines.append("🔗 " + " | ".join(links))
    return "\n".join(lines)


def _search_keyboard(query: str, page: int, total: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    nav: list[InlineKeyboardButton] = []
    max_page = max(0, (total - 1) // PAGE_SIZE)
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"s:{page-1}:{query}"))
    if page < max_page:
        nav.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"s:{page+1}:{query}"))
    if nav:
        rows.append(nav)
    hot_row: list[InlineKeyboardButton] = []
    for term in ads.hot_searches[:3]:
        hot_row.append(InlineKeyboardButton(f"🔥{term[:8]}", callback_data=f"s:0:{term}"))
    if hot_row:
        rows.append(hot_row)
    if ANTI_LOST_URL:
        rows.append([InlineKeyboardButton("防失联", url=ANTI_LOST_URL)])
    return InlineKeyboardMarkup(rows) if rows else InlineKeyboardMarkup([])


def _format_results(query: str, page: int, hits, total: int) -> str:
    ads_block = _render_ads_block()
    header = f"🔍 <b>{escape(BOT_NAME)}</b> · 关键词: <code>{escape(query)}</code>\n"
    if ads_block:
        header += ads_block + "\n"
    header += f"共 <b>{total}</b> 条 · 第 {page + 1}/{(max(total - 1, 0) // PAGE_SIZE) + 1} 页\n\n"
    if not hits:
        return header + "没有找到相关内容。"
    body = []
    for i, hit in enumerate(hits, start=1 + page * PAGE_SIZE):
        snippet = hit.text.replace("\n", " ").strip()
        if len(snippet) > 120:
            snippet = snippet[:120] + "…"
        media = f"[{hit.media_type}] " if hit.media_type != "text" else ""
        body.append(
            f"{i}. {media}<b>{escape(hit.chat_title or str(hit.chat_id))}</b>\n"
            f"   {escape(snippet)}\n"
            f"   <code>chat={hit.chat_id} msg={hit.message_id}</code>"
        )
    return header + "\n\n".join(body)


def _format_source_line(s) -> str:
    uname = f"@{s.username}" if s.username else "-"
    flag = "✅" if s.enabled else "⏸"
    n = index.count_by_chat(s.chat_id)
    return f"{flag} <code>{s.chat_id}</code> [{s.chat_type}] {escape(s.title or '')} {uname} · {n}条"


async def _resolve_chat(context: ContextTypes.DEFAULT_TYPE, token: str):
    token = token.strip()
    if not token:
        raise ValueError("空目标")
    if token.lstrip("-").isdigit():
        chat_id: int | str = int(token)
    else:
        chat_id = token if token.startswith("@") else f"@{token}"
    return await context.bot.get_chat(chat_id)


async def _reject_if_not_search_entry(update: Update) -> bool:
    chat = update.effective_chat
    user = update.effective_user
    if not chat:
        return True
    if chat.type == ChatType.PRIVATE:
        if _is_admin(user.id if user else None):
            return False
        if update.message:
            await update.message.reply_text(f"{BOT_NAME} 仅在指定搜索群内可用。管理员可私聊配置采集源。")
        return True
    if not _is_search_chat(chat.id):
        if update.message:
            await update.message.reply_text("本群未配置为搜索入口。管理员可用 /add_search 添加。")
        return True
    return False


def _index_message(message, chat) -> None:
    if not message or not chat:
        return
    if not _is_source_chat(chat.id):
        return
    text = _message_text(message)
    if not text:
        return
    index.upsert_message(
        chat_id=chat.id,
        message_id=message.message_id,
        chat_title=chat.title or getattr(chat, "username", None) or "",
        text=text,
        media_type=_detect_media_type(message),
        created_at=int(message.date.timestamp()) if message.date else int(time.time()),
    )


# ---------------------- 用户命令 ----------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if chat and chat.type == ChatType.PRIVATE and _is_admin(user.id if user else None):
        s = index.stats()
        await update.message.reply_text(
            f"👋 {BOT_NAME} 管理端\n"
            f"采集源: {s['sources']} · 搜索群: {s['search_chats']} · 消息: {s['messages']}\n\n"
            "采集管理:\n"
            "/add_source @频道或群 或 chat_id\n"
            "/del_source chat_id\n"
            "/list_sources\n"
            "也可直接转发群/频道消息给我，自动添加采集源\n\n"
            "搜索入口:\n"
            "/add_search @群 或 chat_id\n"
            "/del_search chat_id\n"
            "/list_search\n\n"
            "/stats /reload_ads /help"
        )
        return
    if await _reject_if_not_search_entry(update):
        return
    s = index.stats()
    await update.message.reply_text(
        f"👋 {BOT_NAME}\n"
        f"搜索：/search 关键词  或  ?关键词\n"
        f"索引: {s['messages']} 条 / 采集源 {s['sources']} 个"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat and update.effective_chat.type == ChatType.PRIVATE and _is_admin(
        update.effective_user.id if update.effective_user else None
    ):
        await update.message.reply_text(
            "<b>管理员说明</b>\n"
            "1. @BotFather → /setprivacy → <b>Disable</b>（群消息才能采集）\n"
            "2. Bot 加入采集群/频道并设管理员（频道必须是管理员才能收到帖子）\n"
            "3. /add_source 添加采集源；/add_search 添加搜索入口群\n"
            "4. 私聊转发一条目标群/频道消息，可快速添加采集源",
            parse_mode="HTML",
        )
        return
    if await _reject_if_not_search_entry(update):
        return
    await update.message.reply_text(
        f"<b>{escape(BOT_NAME)}</b>\n• /search 关键词\n• ?关键词",
        parse_mode="HTML",
    )


async def myid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return
    await update.message.reply_text(
        f"user_id: <code>{user.id}</code>\nchat_id: <code>{chat.id}</code>\ntype: {chat.type}",
        parse_mode="HTML",
    )


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id if update.effective_user else None):
        return
    s = index.stats()
    await update.message.reply_text(
        f"📊 消息 {s['messages']} · 已索引会话 {s['indexed_chats']}\n"
        f"采集源 {s['sources']} · 搜索入口 {s['search_chats']}"
    )


async def reload_ads_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id if update.effective_user else None):
        await update.message.reply_text("无权限")
        return
    ads.reload()
    await update.message.reply_text(
        f"✅ 广告已重载 · 热搜 {len(ads.hot_searches)} · 链接 {len(ads.service_links)}"
    )


# ---------------------- 采集源管理 ----------------------
async def add_source_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id if update.effective_user else None):
        await update.message.reply_text("无权限")
        return
    if not context.args:
        await update.message.reply_text("用法: /add_source @username 或 chat_id\n也可转发目标群/频道消息给我")
        return
    try:
        chat = await _resolve_chat(context, context.args[0])
    except (TelegramError, ValueError) as e:
        await update.message.reply_text(f"无法解析目标: {e}")
        return
    if chat.type not in (ChatType.CHANNEL, ChatType.GROUP, ChatType.SUPERGROUP):
        await update.message.reply_text("只能添加群或频道")
        return
    index.add_source(
        chat_id=chat.id,
        title=chat.title or "",
        username=chat.username or "",
        chat_type=chat.type,
        created_at=int(time.time()),
    )
    await update.message.reply_text(
        f"✅ 已添加采集源\n<code>{chat.id}</code> {escape(chat.title or '')} "
        f"{'@' + chat.username if chat.username else ''}\n"
        f"类型: {chat.type}\n请确保 Bot 已在该群/频道且有权读取消息。",
        parse_mode="HTML",
    )


async def del_source_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id if update.effective_user else None):
        await update.message.reply_text("无权限")
        return
    if not context.args or not context.args[0].lstrip("-").isdigit():
        await update.message.reply_text("用法: /del_source chat_id")
        return
    chat_id = int(context.args[0])
    ok = index.remove_source(chat_id)
    await update.message.reply_text("✅ 已删除采集源" if ok else "未找到该采集源")


async def list_sources_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id if update.effective_user else None):
        await update.message.reply_text("无权限")
        return
    sources = index.list_sources()
    if not sources:
        await update.message.reply_text("暂无采集源。用 /add_source 添加。")
        return
    lines = ["📡 <b>采集源列表</b>"] + [_format_source_line(s) for s in sources]
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def add_search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id if update.effective_user else None):
        await update.message.reply_text("无权限")
        return
    if not context.args:
        await update.message.reply_text("用法: /add_search @群username 或 chat_id")
        return
    try:
        chat = await _resolve_chat(context, context.args[0])
    except (TelegramError, ValueError) as e:
        await update.message.reply_text(f"无法解析目标: {e}")
        return
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await update.message.reply_text("搜索入口只能是群/超级群")
        return
    index.add_search_chat(chat_id=chat.id, title=chat.title or "", username=chat.username or "")
    await update.message.reply_text(
        f"✅ 已添加搜索入口\n<code>{chat.id}</code> {escape(chat.title or '')}",
        parse_mode="HTML",
    )


async def del_search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id if update.effective_user else None):
        await update.message.reply_text("无权限")
        return
    if not context.args or not context.args[0].lstrip("-").isdigit():
        await update.message.reply_text("用法: /del_search chat_id")
        return
    ok = index.remove_search_chat(int(context.args[0]))
    await update.message.reply_text("✅ 已删除搜索入口" if ok else "未找到")


async def list_search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id if update.effective_user else None):
        await update.message.reply_text("无权限")
        return
    items = index.list_search_chats()
    if not items:
        await update.message.reply_text("暂无搜索入口。用 /add_search 添加。")
        return
    lines = ["🔎 <b>搜索入口群</b>"] + [_format_source_line(s) for s in items]
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def on_admin_forward(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """私聊转发消息 → 自动添加采集源。"""
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat:
        return
    if chat.type != ChatType.PRIVATE or not _is_admin(user.id):
        return
    origin = message.forward_from_chat or message.sender_chat
    if not origin:
        return
    if origin.type not in (ChatType.CHANNEL, ChatType.GROUP, ChatType.SUPERGROUP):
        await message.reply_text("只能从群或频道转发")
        return
    index.add_source(
        chat_id=origin.id,
        title=origin.title or "",
        username=origin.username or "",
        chat_type=origin.type,
        created_at=int(time.time()),
    )
    await message.reply_text(
        f"✅ 已从转发添加采集源\n<code>{origin.id}</code> {escape(origin.title or '')} "
        f"{'@' + origin.username if origin.username else ''}\n"
        f"记得把 Bot 拉进该群/频道并设管理员。",
        parse_mode="HTML",
    )


# ---------------------- 搜索 + 采集 ----------------------
async def do_search(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str, page: int = 0) -> None:
    query = (query or "").strip()
    if not query:
        await update.effective_message.reply_text("请输入关键词，例如：/search 电影")
        return
    hits, total = index.search(query, offset=page * PAGE_SIZE, limit=PAGE_SIZE)
    text = _format_results(query, page, hits, total)
    kb = _search_keyboard(query, page, total)
    await update.effective_message.reply_text(
        text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True
    )


async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_not_search_entry(update):
        return
    await do_search(update, context, " ".join(context.args or []).strip(), 0)


async def on_group_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat or chat.type == ChatType.PRIVATE:
        return

    text = _message_text(message)
    # 搜索入口：?关键词
    if _is_search_chat(chat.id) and text and (text.startswith("?") or text.startswith("？")):
        await do_search(update, context, text[1:].strip(), 0)
        return

    # 采集源：索引
    _index_message(message, chat)


async def on_media_or_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message or update.channel_post
    chat = update.effective_chat
    if not message or not chat:
        return
    _index_message(message, chat)


async def on_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    chat = update.effective_chat
    if chat and chat.type != ChatType.PRIVATE and not _is_search_chat(chat.id):
        await query.answer("本群未授权搜索", show_alert=True)
        return
    parts = query.data.split(":", 2)
    if len(parts) != 3 or parts[0] != "s":
        await query.answer()
        return
    try:
        page = int(parts[1])
    except ValueError:
        await query.answer("页码无效", show_alert=True)
        return
    keyword = parts[2]
    hits, total = index.search(keyword, offset=page * PAGE_SIZE, limit=PAGE_SIZE)
    text = _format_results(keyword, page, hits, total)
    kb = _search_keyboard(keyword, page, total)
    await query.answer()
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)


def _seed_from_env() -> None:
    for cid in SEED_SEARCH_CHAT_IDS:
        index.add_search_chat(chat_id=cid, title="", username="")
    for cid in SEED_SOURCE_CHAT_IDS:
        index.add_source(chat_id=cid, title="", username="", chat_type="supergroup", created_at=int(time.time()))
    # 已知群补充信息
    if -1003757523411 in SEED_SEARCH_CHAT_IDS or -1003757523411 in SEED_SOURCE_CHAT_IDS:
        index.add_search_chat(chat_id=-1003757523411, title="星弟担保搜索群", username="xddbssq")
        index.add_source(
            chat_id=-1003757523411,
            title="星弟担保搜索群",
            username="xddbssq",
            chat_type="supergroup",
            created_at=int(time.time()),
        )


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("请设置 BOT_TOKEN")
    if not ADMIN_IDS:
        raise SystemExit("请设置 ADMIN_IDS")

    _seed_from_env()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("myid", myid_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("reload_ads", reload_ads_cmd))
    app.add_handler(CommandHandler("add_source", add_source_cmd))
    app.add_handler(CommandHandler("del_source", del_source_cmd))
    app.add_handler(CommandHandler("list_sources", list_sources_cmd))
    app.add_handler(CommandHandler("add_search", add_search_cmd))
    app.add_handler(CommandHandler("del_search", del_search_cmd))
    app.add_handler(CommandHandler("list_search", list_search_cmd))
    app.add_handler(CallbackQueryHandler(on_page_callback, pattern=r"^s:"))
    app.add_handler(MessageHandler(filters.FORWARDED & filters.ChatType.PRIVATE, on_admin_forward))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_group_text))
    app.add_handler(
        MessageHandler(
            (
                filters.PHOTO
                | filters.VIDEO
                | filters.Document.ALL
                | filters.ANIMATION
                | filters.AUDIO
                | filters.VOICE
                | filters.UpdateType.CHANNEL_POSTS
            ),
            on_media_or_channel,
        )
    )

    logger.info(
        "星弟搜索启动 | admins=%s search_seeds=%s source_seeds=%s db=%s",
        ADMIN_IDS,
        SEED_SEARCH_CHAT_IDS,
        SEED_SOURCE_CHAT_IDS,
        DB_PATH,
    )
    app.run_polling(
        allowed_updates=["message", "channel_post", "edited_channel_post", "callback_query"]
    )


if __name__ == "__main__":
    main()
