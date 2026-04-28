import json
import logging
import logging.handlers
import os
import urllib.request
from pathlib import Path


class TelegramErrorHandler(logging.Handler):
    """Sends ERROR/CRITICAL log records to a Telegram chat via Bot API (no extra deps)."""

    def __init__(self, bot_token: str, chat_id: str) -> None:
        super().__init__(level=logging.ERROR)
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        logger = logging.getLogger(__name__)
        logger.debug(
            "[TelegramErrorHandler.__init__] Initialized: chat_id=%s", chat_id
        )

    def emit(self, record: logging.LogRecord) -> None:
        logger = logging.getLogger(__name__)
        logger.debug(
            "[TelegramErrorHandler.emit] Sending error notification: level=%s logger=%s",
            record.levelname,
            record.name,
        )
        try:
            text = self._format_record(record)
            payload = json.dumps(
                {"chat_id": self._chat_id, "text": text, "parse_mode": "HTML"}
            ).encode()
            req = urllib.request.Request(
                self._url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status != 200:
                    self.handleError(record)
        except Exception:
            self.handleError(record)

    def _format_record(self, record: logging.LogRecord) -> str:
        icon = "🔴" if record.levelno >= logging.ERROR else "🟡"
        header = f"{icon} <b>[{record.levelname}]</b> {record.name}"
        message = self.format(record)
        # Telegram HTML allows <b> tags; strip problematic characters from message body
        body = message.replace("<", "&lt;").replace(">", "&gt;")
        return f"{header}\n{body}"


def setup_logging(
    log_level: str | None = None,
    bot_token: str | None = None,
    chat_id: str | None = None,
) -> None:
    level_str = log_level or os.getenv("LOG_LEVEL", "DEBUG")
    level = getattr(logging, level_str.upper(), logging.DEBUG)

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "bot.log"

    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    formatter = logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")

    root = logging.getLogger()
    root.setLevel(level)

    # Remove existing handlers to avoid duplicate output on re-init
    root.handlers.clear()

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if bot_token and chat_id:
        tg_handler = TelegramErrorHandler(bot_token=bot_token, chat_id=chat_id)
        tg_handler.setFormatter(formatter)
        root.addHandler(tg_handler)

    # Suppress noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)
    logger.info("[setup_logging] Logging initialized: level=%s, file=%s", level_str.upper(), log_file)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
