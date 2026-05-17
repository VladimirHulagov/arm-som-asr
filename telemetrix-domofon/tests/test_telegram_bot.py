import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.telegram_bot import DomofonBot


def _make_bot():
    monitor = AsyncMock()
    audio = AsyncMock()
    bot = DomofonBot(monitor, audio, token="fake-token", chat_id=12345)
    bot._app = MagicMock()
    bot._app.bot = AsyncMock()
    return bot, monitor, audio


class TestOnRinging:
    @pytest.mark.asyncio
    async def test_notification_sent(self):
        bot, _, _ = _make_bot()
        with patch.object(bot, "_send_message", new_callable=AsyncMock) as mock_send:
            await bot.on_event("ringing")
        mock_send.assert_called_once()
        text = mock_send.call_args.args[0]
        assert "Вызов домофона" in text
        keyboard = mock_send.call_args.args[1]
        assert keyboard is not None
        assert len(keyboard) == 1
        assert len(keyboard[0]) == 2
        assert keyboard[0][0].callback_data == "answer"
        assert keyboard[0][1].callback_data == "ignore"


class TestOnAnswered:
    @pytest.mark.asyncio
    async def test_audio_bridge_started(self):
        bot, _, audio = _make_bot()
        with patch.object(bot, "_edit_message", new_callable=AsyncMock):
            await bot.on_event("answered")
        audio.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_conversation_keyboard_sent(self):
        bot, _, _ = _make_bot()
        with patch.object(bot, "_edit_message", new_callable=AsyncMock) as mock_edit:
            await bot.on_event("answered")
        mock_edit.assert_called_once()
        text = mock_edit.call_args.args[0]
        assert "Разговор с посетителем" in text
        keyboard = mock_edit.call_args.args[1]
        assert keyboard is not None
        buttons = [b.callback_data for row in keyboard for b in row]
        assert "open_door" in buttons
        assert "hangup" in buttons


class TestOnHangup:
    @pytest.mark.asyncio
    async def test_audio_bridge_stopped(self):
        bot, _, audio = _make_bot()
        with patch.object(bot, "_edit_message", new_callable=AsyncMock):
            await bot.on_event("hangup")
        audio.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_keyboard_on_hangup(self):
        bot, _, _ = _make_bot()
        with patch.object(bot, "_edit_message", new_callable=AsyncMock) as mock_edit:
            await bot.on_event("hangup")
        assert len(mock_edit.call_args.args) == 1


class TestOnDoorOpen:
    @pytest.mark.asyncio
    async def test_door_notification_sent(self):
        bot, _, _ = _make_bot()
        with patch.object(bot, "_edit_message", new_callable=AsyncMock) as mock_edit:
            await bot.on_event("door_open")
        mock_edit.assert_called_once()
        text = mock_edit.call_args.args[0]
        assert "Дверь открыта" in text
        keyboard = mock_edit.call_args.args[1]
        assert keyboard is not None
        buttons = [b.callback_data for row in keyboard for b in row]
        assert "hangup" in buttons


class TestOnDoorClosed:
    @pytest.mark.asyncio
    async def test_returns_to_conversation(self):
        bot, _, _ = _make_bot()
        with patch.object(bot, "_edit_message", new_callable=AsyncMock) as mock_edit:
            await bot.on_event("door_closed")
        text = mock_edit.call_args.args[0]
        assert "Разговор с посетителем" in text
        keyboard = mock_edit.call_args.args[1]
        assert keyboard is not None
        buttons = [b.callback_data for row in keyboard for b in row]
        assert "open_door" in buttons
        assert "hangup" in buttons


class TestOnMissed:
    @pytest.mark.asyncio
    async def test_missed_notification(self):
        bot, _, _ = _make_bot()
        with patch.object(bot, "_edit_message", new_callable=AsyncMock) as mock_edit:
            await bot.on_event("missed")
        text = mock_edit.call_args.args[0]
        assert "Вызов пропущен" in text


class TestCallbackHandlers:
    def _make_update(self, data):
        update = MagicMock()
        update.callback_query = AsyncMock()
        update.callback_query.data = data
        return update

    @pytest.mark.asyncio
    async def test_answer_callback(self):
        bot, monitor, _ = _make_bot()
        update = self._make_update("answer")
        await bot._callback_handler(update, MagicMock())
        monitor.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_hangup_callback(self):
        bot, monitor, _ = _make_bot()
        update = self._make_update("hangup")
        await bot._callback_handler(update, MagicMock())
        monitor.hangup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_open_door_callback(self):
        bot, monitor, _ = _make_bot()
        update = self._make_update("open_door")
        await bot._callback_handler(update, MagicMock())
        monitor.open_door.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ignore_callback_triggers_missed(self):
        bot, _, _ = _make_bot()
        update = self._make_update("ignore")
        with patch.object(bot, "on_event", new_callable=AsyncMock) as mock_event:
            await bot._callback_handler(update, MagicMock())
        mock_event.assert_awaited_once_with("missed")
