import os
import logging
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()


class Settings:
    def __init__(self):
        logger.debug("[Settings.__init__] Loading configuration from environment")

        self.BOT_TOKEN: str = self._require("BOT_TOKEN")
        self.GROUP_CHAT_ID: str = self._require("GROUP_CHAT_ID")
        self.DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///data/whatcher.db")
        self.LOG_LEVEL: str = os.getenv("LOG_LEVEL", "DEBUG").upper()
        self.CHECK_TIMES: list[str] = os.getenv("CHECK_TIMES", "7:00,13:00,20:00").split(",")

        logger.debug("[Settings.__init__] DATABASE_URL=%s", self.DATABASE_URL)
        logger.debug("[Settings.__init__] LOG_LEVEL=%s", self.LOG_LEVEL)
        logger.debug("[Settings.__init__] CHECK_TIMES=%s", self.CHECK_TIMES)
        logger.debug("[Settings.__init__] BOT_TOKEN=***%s", self.BOT_TOKEN[-4:])
        logger.debug("[Settings.__init__] GROUP_CHAT_ID=%s", self.GROUP_CHAT_ID)

        logger.info("[Settings.__init__] Configuration loaded successfully")

    def _require(self, key: str) -> str:
        value = os.getenv(key)
        if not value:
            logger.error("[Settings._require] Missing required environment variable: %s", key)
            raise ValueError(
                f"Required environment variable '{key}' is not set. "
                f"Copy .env.example to .env and fill in the value."
            )
        return value
