import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    bot_token: str
    approver_ids: frozenset[int]

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise ValueError("请设置环境变量 BOT_TOKEN")

        raw_ids = os.getenv("APPROVER_IDS", "").strip()
        approver_ids = frozenset(
            int(uid.strip())
            for uid in raw_ids.split(",")
            if uid.strip().isdigit()
        )
        return cls(bot_token=token, approver_ids=approver_ids)
