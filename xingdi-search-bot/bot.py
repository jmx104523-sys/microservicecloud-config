"""
星弟搜索 — Telegram 群内搜索机器人

约束：
- 仅在 ALLOWED_CHAT_IDS 指定的群内可用（私聊仅管理员可配置）
- 消息写入本地 SQLite FTS5 索引（大数据方案可替换为 ES/Meilisearch）
- 广告位 / 热搜从 ads/ads.json 读取，预留扩展
"""

from __future__ import annotations

import logging
import os
from html import escape
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
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
ALLOWED_CHAT_IDS = {
    int(x.strip())
    for x in os.environ.get("ALLOWED_CHAT_IDS", "").split(",")
    if x.strip().lstrip("-").isdigit()
}
ADMIN_IDS = {
    int(x.strip())
    for x in os.environ.get("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
PAGE_SIZE = max(1, int(os.environ.get("PAGE_SIZE", "8")))
DB_PATH = os.environ.get("DB_PATH", str(ROOT / "data" / "search.db"))
ANTI_LOST_URL = os.environ.get("ANTI_LOST_URL", "").strip()
ADS_PATH = os.environ.get("ADS_PATH", str(ROOT / "ads" / "ads.json"))

index = SearchIndex(DB_PATH)
ads = AdsConfig(ADS_PATH)


def _is_admin(user_id: int | None) -> bool:
    return bool(user_id and user_id in ADMIN_IDS)


def _is_allowed_chat(chat_id: int | None) -> bool:
    if chat_id is None:
        return False
    if not ALLOWED_CHAT_IDS:
        return False
    return chat_id in ALLOWED_CHAT_IDS


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
        # 群消息深链：需要公开 username；私有群仅展示摘要
        body.append(
            f"{i}. {media}<b>{escape(hit.chat_title or str(hit.chat_id))}</b>\n"
            f"   {escape(snippet)}\n"
            f"   <code>chat={hit.chat_id} msg={hit.message_id}</code>"
        )
    return header + "\n\n".join(body)


async def _reject_if_not_allowed(update: Update) -> bool:
    """返回 True 表示应拒绝继续处理。"""
    chat = update.effective_chat
    user = update.effective_user
    if not chat:
        return True
    if chat.type == ChatType.PRIVATE:
        if _is_admin(user.id if user else None):
            return False
        if update.message:
            await update.message.reply_text(f"{BOT_NAME} 仅在指定群内可用。")
        return True
    if not _is_allowed_chat(chat.id):
        if update.message:
            await update.message.reply_text("本群未授权使用搜索。请联系管理员配置 ALLOWED_CHAT_IDS。")
        return True
    return False


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_not_allowed(update):
        return
    stats = index.stats()
    await update.message.reply_text(
        f"👋 {BOT_NAME}\n"
        f"搜索：/search 关键词  或  ?关键词\n"
        f"索引: {stats['messages']} 条消息 / {stats['chats']} 个群\n"
        f"管理员: /reload_ads /stats /myid",
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_not_allowed(update):
        return
    await update.message.reply_text(
        f"<b>{escape(BOT_NAME)}</b> 用法\n"
        "• /search 关键词\n"
        "• ?关键词（群内快捷搜索）\n"
        "• 仅授权群可用；群消息自动进索引\n"
        "• 广告位编辑 ads/ads.json 后 /reload_ads\n\n"
        "<b>部署注意</b>\n"
        "1. @BotFather → /setprivacy → Disable（才能索引群消息）\n"
        "2. 把 Bot 拉进授权群\n"
        "3. 环境变量 ALLOWED_CHAT_IDS 填群 chat id（/myid 可查）",
        parse_mode="HTML",
    )


async def myid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return
    await update.message.reply_text(
        f"user_id: <code>{user.id}</code>\n"
        f"chat_id: <code>{chat.id}</code>\n"
        f"chat_type: {chat.type}",
        parse_mode="HTML",
    )


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id if update.effective_user else None):
        return
    s = index.stats()
    await update.message.reply_text(
        f"📊 索引统计\n消息: {s['messages']}\n群数: {s['chats']}\n授权群: {len(ALLOWED_CHAT_IDS)}"
    )


async def reload_ads_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id if update.effective_user else None):
        await update.message.reply_text("无权限")
        return
    ads.reload()
    await update.message.reply_text(
        f"✅ 广告配置已重载\n"
        f"热搜: {len(ads.hot_searches)} · 服务链接: {len(ads.service_links)}"
    )


async def do_search(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str, page: int = 0) -> None:
    query = (query or "").strip()
    if not query:
        await update.effective_message.reply_text("请输入关键词，例如：/search 电影")
        return
    hits, total = index.search(query, offset=page * PAGE_SIZE, limit=PAGE_SIZE)
    text = _format_results(query, page, hits, total)
    kb = _search_keyboard(query, page, total)
    await update.effective_message.reply_text(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)


async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_not_allowed(update):
        return
    query = " ".join(context.args or []).strip()
    await do_search(update, context, query, 0)


async def on_text_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """授权群内：索引普通消息；以 ? / ？ 开头则触发搜索。"""
    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat:
        return
    if chat.type == ChatType.PRIVATE:
        return
    if not _is_allowed_chat(chat.id):
        return

    text = _message_text(message)
    if not text:
        return

    # 搜索触发：?关键词 或 ？关键词
    if text.startswith("?") or text.startswith("？"):
        await do_search(update, context, text[1:].strip(), 0)
        return

    index.upsert_message(
        chat_id=chat.id,
        message_id=message.message_id,
        chat_title=chat.title or "",
        text=text,
        media_type=_detect_media_type(message),
        created_at=int(message.date.timestamp()) if message.date else 0,
    )


async def on_media_index(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """索引带 caption 的媒体消息（不触发搜索）。"""
    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat or not _is_allowed_chat(chat.id):
        return
    text = _message_text(message)
    if not text:
        return
    index.upsert_message(
        chat_id=chat.id,
        message_id=message.message_id,
        chat_title=chat.title or "",
        text=text,
        media_type=_detect_media_type(message),
        created_at=int(message.date.timestamp()) if message.date else 0,
    )


async def on_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    chat = update.effective_chat
    if chat and chat.type != ChatType.PRIVATE and not _is_allowed_chat(chat.id):
        await query.answer("本群未授权", show_alert=True)
        return

    # s:{page}:{keyword...}
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


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("请设置 BOT_TOKEN")
    if not ALLOWED_CHAT_IDS:
        raise SystemExit("请设置 ALLOWED_CHAT_IDS（至少一个群 chat id）")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("myid", myid_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("reload_ads", reload_ads_cmd))
    app.add_handler(CallbackQueryHandler(on_page_callback, pattern=r"^s:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_search))
    app.add_handler(
        MessageHandler(
            (filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.ANIMATION | filters.AUDIO | filters.VOICE)
            & filters.Caption(True),
            on_media_index,
        )
    )

    logger.info(
        "星弟搜索启动 | allowed=%s admins=%s db=%s",
        ALLOWED_CHAT_IDS,
        ADMIN_IDS,
        DB_PATH,
    )
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
