import asyncio
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from src.config import ADC_CHANNEL, PIN_ANSWER, PIN_DOOR, WINDOW_SIZE
from src.fsm import DoorFSM, State
from src.intercom_monitor import IntercomMonitor


def _make_data(value):
    return [0, ADC_CHANNEL, value, 0]


class TestAnalogCallback:
    def test_stores_readings_in_window(self):
        fsm = DoorFSM()
        monitor = IntercomMonitor(fsm)
        for i in range(5):
            monitor._on_analog(_make_data(100 + i))
        assert len(monitor._window) == 5

    def test_window_sliding(self):
        fsm = DoorFSM()
        monitor = IntercomMonitor(fsm)
        for i in range(10):
            monitor._on_analog(_make_data(i))
        assert len(monitor._window) == 5
        assert list(monitor._window) == [5, 6, 7, 8, 9]


class TestEventDetection:
    def test_ringing_triggers_callback(self):
        fsm = DoorFSM()
        callback = AsyncMock()
        monitor = IntercomMonitor(fsm, event_callback=callback)
        high_variance = [100, 2000, 100, 2000, 100]
        for _ in range(3):
            for v in high_variance:
                monitor._on_analog(_make_data(v))
        assert callback.call_count >= 1
        callback.assert_called_with("ringing")

    def test_stable_readings_no_event(self):
        fsm = DoorFSM()
        callback = AsyncMock()
        monitor = IntercomMonitor(fsm, event_callback=callback)
        for _ in range(20):
            monitor._on_analog(_make_data(50))
        callback.assert_not_called()


class TestSetup:
    def test_setup_calls_telemetrix(self):
        fsm = DoorFSM()
        monitor = IntercomMonitor(fsm)
        board = MagicMock()
        asyncio.run(monitor.setup(board))
        board.set_pin_mode_analog_input.assert_called_once_with(
            ADC_CHANNEL, differential=10, callback=monitor._on_analog
        )
        board.set_pin_mode_digital_output.assert_any_call(PIN_ANSWER)
        board.set_pin_mode_digital_output.assert_any_call(PIN_DOOR)

    def test_answer_activates_relay(self):
        fsm = DoorFSM()
        fsm.state = State.RINGING
        callback = AsyncMock()
        monitor = IntercomMonitor(fsm, event_callback=callback)
        board = MagicMock()
        asyncio.run(monitor.setup(board))
        result = asyncio.run(monitor.answer())
        assert result == "answered"
        board.digital_write.assert_called_with(PIN_ANSWER, 1)
        callback.assert_called_once_with("answered")

    def test_hangup_deactivates_relay(self):
        fsm = DoorFSM()
        fsm.state = State.CONVERSATION
        callback = AsyncMock()
        monitor = IntercomMonitor(fsm, event_callback=callback)
        board = MagicMock()
        asyncio.run(monitor.setup(board))
        result = asyncio.run(monitor.hangup())
        assert result == "hangup"
        assert board.digital_write.call_args_list == [
            call(PIN_ANSWER, 0),
            call(PIN_DOOR, 0),
        ]
        callback.assert_called_once_with("hangup")

    def test_open_door_pulses_relay(self):
        fsm = DoorFSM()
        fsm.state = State.CONVERSATION
        callback = AsyncMock()
        monitor = IntercomMonitor(fsm, event_callback=callback)
        board = MagicMock()
        asyncio.run(monitor.setup(board))
        result = asyncio.run(monitor.open_door())
        assert result == "door_open"
        board.digital_write.assert_called_with(PIN_DOOR, 1)
        callback.assert_called_once_with("door_open")
