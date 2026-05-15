"""Tests for TelegramClient. No live HTTP."""
from unittest.mock import MagicMock

from tools.decision_trace_report.telegram_client import TelegramClient


def test_send_message_posts_to_bot_api(monkeypatch):
    """send_message POSTs to api.telegram.org/bot<token>/sendMessage
    with the chat_id and text."""
    captured = {}

    def fake_post(url, data=None, json=None, timeout=None, **kwargs):
        captured["url"] = url
        captured["payload"] = json or data
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"ok": True}
        response.raise_for_status = MagicMock()
        return response

    monkeypatch.setattr("requests.post", fake_post)

    client = TelegramClient(bot_token="abc123", chat_id="-100456")
    client.send_message("hello")

    assert captured["url"] == "https://api.telegram.org/botabc123/sendMessage"
    assert captured["payload"]["chat_id"] == "-100456"
    assert captured["payload"]["text"] == "hello"


def test_send_message_swallows_telegram_errors(monkeypatch, caplog):
    """Telegram failure must not crash the report tool — log and move
    on. Failure to deliver the heartbeat does not invalidate the
    rendered report file."""
    import requests

    def fake_post(*args, **kwargs):
        raise requests.ConnectionError("Telegram unreachable")

    monkeypatch.setattr("requests.post", fake_post)

    client = TelegramClient(bot_token="abc123", chat_id="-100456")
    # Must not raise.
    client.send_message("hello")
