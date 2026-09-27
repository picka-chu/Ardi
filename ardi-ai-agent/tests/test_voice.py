"""Tests for voice-note understanding (transcribe -> text pipeline)."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("TELEGRAM_TOKEN", "123:fake")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import pytest

import bot.handlers as h
from ai.gemini import transcribe_voice


def _voice(size=1000, payload=b"fake-audio-bytes"):
    async def _dl():
        return bytearray(payload)

    async def _gf():
        return SimpleNamespace(download_as_bytearray=_dl)

    return SimpleNamespace(file_size=size, mime_type="audio/ogg", get_file=_gf)


def _update(voice=None):
    replies, actions = [], []

    async def _reply(text, **kw):
        replies.append(text)

    async def _action(kind):
        actions.append(kind)

    msg = SimpleNamespace(voice=voice, reply_text=_reply, reply_chat_action=_action)
    upd = SimpleNamespace(message=msg, effective_chat=SimpleNamespace(id=111))
    return upd, replies, actions


def _ctx(**data):
    return SimpleNamespace(user_data=dict(data))


class TestTranscribeVoice:
    async def test_empty_bytes_no_api(self):
        assert await transcribe_voice(b"") is None
        assert await transcribe_voice(None) is None


class TestVoiceRouter:
    async def test_ignores_non_voice(self):
        upd, replies, _ = _update(voice=None)
        assert await h.handle_voice_message(upd, _ctx()) is None
        assert replies == []

    async def test_payment_wait_asks_receipt(self):
        upd, replies, _ = _update(voice=_voice())
        await h.handle_voice_message(upd, _ctx(state="awaiting_order_payment"))
        assert replies and "receipt" in replies[0].lower()


class TestCustomerVoice:
    async def test_transcribes_and_delegates(self, monkeypatch):
        seen = {}

        async def fake_customer(update, context, _text_override=None):
            seen["text"] = _text_override

        async def fake_transcribe(audio, mime="audio/ogg"):
            assert audio == b"fake-audio-bytes"
            return "ዋጋ ስንት ነው?"

        monkeypatch.setattr(h, "transcribe_voice", fake_transcribe)
        monkeypatch.setattr(h, "handle_customer_message", fake_customer)

        upd, replies, _ = _update(voice=_voice())
        await h.handle_customer_voice(upd, _ctx(customer_chat_active=True))
        assert seen["text"] == "ዋጋ ስንት ነው?"
        assert replies == []  # delegate owns the reply

    async def test_unclear_audio_asks_retype(self, monkeypatch):
        async def fake_transcribe(audio, mime="audio/ogg"):
            return None

        monkeypatch.setattr(h, "transcribe_voice", fake_transcribe)
        upd, replies, _ = _update(voice=_voice())
        await h.handle_customer_voice(upd, _ctx(customer_chat_active=True))
        assert replies and "🎤" in replies[0]

    async def test_oversize_rejected_without_transcribe(self, monkeypatch):
        called = []

        async def fake_transcribe(audio, mime="audio/ogg"):
            called.append(True)
            return "hi"

        monkeypatch.setattr(h, "transcribe_voice", fake_transcribe)
        upd, replies, _ = _update(voice=_voice(size=h.MAX_VOICE_BYTES + 1))
        await h.handle_customer_voice(upd, _ctx(customer_chat_active=True))
        assert called == []
        assert replies and "too long" in replies[0].lower()

    async def test_inactive_chat_delegates_out(self, monkeypatch):
        """Outside a customer chat the text pipeline exits silently."""
        async def fake_transcribe(audio, mime="audio/ogg"):
            return "hello"

        async def fake_customer(update, context, _text_override=None):
            assert not context.user_data.get("customer_chat_active")
            return None

        monkeypatch.setattr(h, "transcribe_voice", fake_transcribe)
        monkeypatch.setattr(h, "handle_customer_message", fake_customer)
        upd, replies, _ = _update(voice=_voice())
        assert await h.handle_customer_voice(upd, _ctx()) is None
