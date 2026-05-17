from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler


class DomofonBot:
    def __init__(self, monitor, audio, token, chat_id):
        self.monitor = monitor
        self.audio = audio
        self._token = token
        self._chat_id = chat_id
        self._last_message_id = None
        self._app = None

    def _now(self):
        return datetime.now().strftime("%H:%M:%S")

    async def _send_message(self, text, keyboard=None):
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        msg = await self._app.bot.send_message(
            chat_id=self._chat_id,
            text=text,
            reply_markup=reply_markup,
        )
        self._last_message_id = msg.message_id
        return msg

    async def _edit_message(self, text, keyboard=None):
        if self._last_message_id is None:
            return await self._send_message(text, keyboard)
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        try:
            return await self._app.bot.edit_message_text(
                text=text,
                chat_id=self._chat_id,
                message_id=self._last_message_id,
                reply_markup=reply_markup,
            )
        except Exception:
            return await self._send_message(text, keyboard)

    async def on_event(self, event):
        ts = self._now()
        if event == "ringing":
            keyboard = [
                [
                    InlineKeyboardButton("Ответить", callback_data="answer"),
                    InlineKeyboardButton("Игнорировать", callback_data="ignore"),
                ]
            ]
            await self._send_message(f"🔔 Вызов домофона\n{ts}", keyboard)
        elif event == "answered":
            await self.audio.start()
            keyboard = [
                [
                    InlineKeyboardButton("🔓 Открыть дверь", callback_data="open_door"),
                    InlineKeyboardButton("📵 Положить трубку", callback_data="hangup"),
                ]
            ]
            await self._edit_message(f"📞 Разговор с посетителем\n{ts}", keyboard)
        elif event == "hangup":
            await self.audio.stop()
            await self._edit_message(f"❌ Разговор завершён\n{ts}")
        elif event == "door_open":
            keyboard = [
                [InlineKeyboardButton("📵 Завершить", callback_data="hangup")]
            ]
            await self._edit_message(f"🔓 Дверь открыта\n{ts}", keyboard)
        elif event == "door_closed":
            keyboard = [
                [
                    InlineKeyboardButton("🔓 Открыть дверь", callback_data="open_door"),
                    InlineKeyboardButton("📵 Положить трубку", callback_data="hangup"),
                ]
            ]
            await self._edit_message(f"📞 Разговор с посетителем\n{ts}", keyboard)
        elif event == "missed":
            await self._edit_message(f"🔕 Вызов пропущен\n{ts}")

    async def _callback_handler(self, update: Update, context):
        query = update.callback_query
        await query.answer()
        data = query.data
        if data == "answer":
            await self.monitor.answer()
        elif data == "hangup":
            await self.monitor.hangup()
        elif data == "open_door":
            await self.monitor.open_door()
        elif data == "ignore":
            await self.on_event("missed")

    def start(self):
        self._app = (
            Application.builder()
            .token(self._token)
            .build()
        )
        self._app.add_handler(CallbackQueryHandler(self._callback_handler))
        self._app.run_polling()

    async def start_async(self):
        self._app = (
            Application.builder()
            .token(self._token)
            .build()
        )
        self._app.add_handler(CallbackQueryHandler(self._callback_handler))
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()

    async def stop(self):
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
