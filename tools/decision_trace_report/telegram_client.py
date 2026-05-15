"""Telegram Bot API wrapper for heartbeat messages.

Failure to deliver the heartbeat must NOT crash the report tool —
the rendered file is the artifact; Telegram is the heartbeat.
"""
import logging

import requests

log = logging.getLogger(__name__)


class TelegramClient:
    """Posts to https://api.telegram.org/bot<token>/sendMessage."""

    def __init__(self, bot_token: str, chat_id: str, timeout_s: float = 10.0):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout_s = timeout_s

    @property
    def base_url(self) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}"

    def send_message(self, text: str) -> None:
        """Send a plain-text message. Swallows network/HTTP errors."""
        try:
            response = requests.post(
                f"{self.base_url}/sendMessage",
                json={"chat_id": self.chat_id, "text": text},
                timeout=self.timeout_s,
            )
            response.raise_for_status()
        except Exception as exc:
            log.warning("telegram heartbeat send failed: %s", exc)
