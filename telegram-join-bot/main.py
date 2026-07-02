import logging

from telegram.ext import Application, ChatJoinRequestHandler

from config import Config
from handlers.commands import register_command_handlers
from handlers.join_request import handle_join_request, register_join_request_handlers

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main() -> None:
    config = Config.from_env()

    app = (
        Application.builder()
        .token(config.bot_token)
        .build()
    )
    app.bot_data["config"] = config

    register_command_handlers(app)
    register_join_request_handlers(app)
    app.add_handler(ChatJoinRequestHandler(handle_join_request))

    logger.info("机器人已启动，等待加入申请…")
    app.run_polling(allowed_updates=["message", "callback_query", "chat_join_request"])


if __name__ == "__main__":
    main()
