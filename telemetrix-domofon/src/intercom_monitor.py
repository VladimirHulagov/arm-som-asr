import asyncio
from collections import deque

from src.config import ADC_CHANNEL, PIN_ANSWER, PIN_DOOR, WINDOW_SIZE


class IntercomMonitor:
    def __init__(self, fsm, event_callback=None):
        self.fsm = fsm
        self.event_callback = event_callback
        self._window = deque(maxlen=WINDOW_SIZE)
        self._board = None

    def _on_analog(self, data):
        self._window.append(data[2])
        if len(self._window) < WINDOW_SIZE:
            return
        readings = list(self._window)
        event = self.fsm.process_readings(readings)
        if event and self.event_callback:
            asyncio.ensure_future(self.event_callback(event))

    async def setup(self, board):
        self._board = board
        board.set_pin_mode_analog_input(ADC_CHANNEL, differential=10, callback=self._on_analog)
        board.set_pin_mode_digital_output(PIN_ANSWER)
        board.set_pin_mode_digital_output(PIN_DOOR)

    async def answer(self):
        event = self.fsm.answer()
        if event:
            self._board.digital_write(PIN_ANSWER, 1)
            if self.event_callback:
                await self.event_callback(event)
        return event

    async def hangup(self):
        event = self.fsm.hangup()
        if event:
            self._board.digital_write(PIN_ANSWER, 0)
            self._board.digital_write(PIN_DOOR, 0)
            if self.event_callback:
                await self.event_callback(event)
        return event

    async def open_door(self):
        event = self.fsm.open_door()
        if event:
            self._board.digital_write(PIN_DOOR, 1)
            if self.event_callback:
                await self.event_callback(event)
        return event
