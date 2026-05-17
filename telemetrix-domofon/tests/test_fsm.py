import pytest

from src.fsm import DoorFSM, State


def _ringing_readings():
    return [1000, 2000, 800, 2200, 600]


def _conversation_readings():
    return [1800, 1850, 1820, 1840, 1830]


def _door_open_readings():
    return [2750, 2780, 2760, 2770, 2790]


def _idle_readings():
    return [100, 110, 105, 108, 102]


class TestInitialState:
    def test_starts_in_idle(self):
        fsm = DoorFSM()
        assert fsm.state == State.IDLE


class TestIdleToRinging:
    def test_high_variance_triggers_after_3_calls(self):
        fsm = DoorFSM()
        readings = _ringing_readings()
        assert fsm.process_readings(readings) is None
        assert fsm.process_readings(readings) is None
        event = fsm.process_readings(readings)
        assert event == "ringing"
        assert fsm.state == State.RINGING

    def test_low_variance_stays_idle(self):
        fsm = DoorFSM()
        readings = _idle_readings()
        for _ in range(5):
            fsm.process_readings(readings)
        assert fsm.state == State.IDLE


class TestRingingToConversation:
    def test_stable_high_voltage_transitions(self):
        fsm = DoorFSM()
        ring = _ringing_readings()
        for _ in range(3):
            fsm.process_readings(ring)
        assert fsm.state == State.RINGING
        conv = _conversation_readings()
        assert fsm.process_readings(conv) is None
        assert fsm.process_readings(conv) is None
        event = fsm.process_readings(conv)
        assert event == "answered"
        assert fsm.state == State.CONVERSATION

    def test_unstable_stays_ringing(self):
        fsm = DoorFSM()
        ring = _ringing_readings()
        for _ in range(3):
            fsm.process_readings(ring)
        assert fsm.state == State.RINGING
        unstable = [1800, 2000, 1700, 2100, 1600]
        for _ in range(5):
            fsm.process_readings(unstable)
        assert fsm.state == State.RINGING


class TestConversationToDoorOpen:
    def test_high_voltage_opens_door(self):
        fsm = DoorFSM()
        ring = _ringing_readings()
        for _ in range(3):
            fsm.process_readings(ring)
        conv = _conversation_readings()
        for _ in range(3):
            fsm.process_readings(conv)
        assert fsm.state == State.CONVERSATION
        door = _door_open_readings()
        assert fsm.process_readings(door) is None
        assert fsm.process_readings(door) is None
        event = fsm.process_readings(door)
        assert event == "door_open"
        assert fsm.state == State.DOOR_OPEN

    def test_low_voltage_hangs_up(self):
        fsm = DoorFSM()
        ring = _ringing_readings()
        for _ in range(3):
            fsm.process_readings(ring)
        conv = _conversation_readings()
        for _ in range(3):
            fsm.process_readings(conv)
        assert fsm.state == State.CONVERSATION
        idle = _idle_readings()
        assert fsm.process_readings(idle) is None
        assert fsm.process_readings(idle) is None
        event = fsm.process_readings(idle)
        assert event == "hangup"
        assert fsm.state == State.IDLE


class TestDoorOpenToConversation:
    def test_voltage_drop_returns_to_conversation(self):
        fsm = DoorFSM()
        ring = _ringing_readings()
        for _ in range(3):
            fsm.process_readings(ring)
        conv = _conversation_readings()
        for _ in range(3):
            fsm.process_readings(conv)
        door = _door_open_readings()
        for _ in range(3):
            fsm.process_readings(door)
        assert fsm.state == State.DOOR_OPEN
        back = [1900, 1950, 1920, 1940, 1930]
        assert fsm.process_readings(back) is None
        assert fsm.process_readings(back) is None
        event = fsm.process_readings(back)
        assert event == "door_closed"
        assert fsm.state == State.CONVERSATION


class TestDebounce:
    def test_two_calls_dont_trigger(self):
        fsm = DoorFSM()
        readings = _ringing_readings()
        fsm.process_readings(readings)
        fsm.process_readings(readings)
        assert fsm.state == State.IDLE

    def test_three_calls_do_trigger(self):
        fsm = DoorFSM()
        readings = _ringing_readings()
        fsm.process_readings(readings)
        fsm.process_readings(readings)
        event = fsm.process_readings(readings)
        assert event == "ringing"
        assert fsm.state == State.RINGING


class TestManualCommands:
    def test_answer_from_ringing(self):
        fsm = DoorFSM()
        ring = _ringing_readings()
        for _ in range(3):
            fsm.process_readings(ring)
        assert fsm.state == State.RINGING
        event = fsm.answer()
        assert event == "answered"
        assert fsm.state == State.CONVERSATION

    def test_answer_from_idle_fails(self):
        fsm = DoorFSM()
        event = fsm.answer()
        assert event is None
        assert fsm.state == State.IDLE

    def test_hangup_from_conversation(self):
        fsm = DoorFSM()
        ring = _ringing_readings()
        for _ in range(3):
            fsm.process_readings(ring)
        conv = _conversation_readings()
        for _ in range(3):
            fsm.process_readings(conv)
        assert fsm.state == State.CONVERSATION
        event = fsm.hangup()
        assert event == "hangup"
        assert fsm.state == State.IDLE

    def test_hangup_from_door_open(self):
        fsm = DoorFSM()
        ring = _ringing_readings()
        for _ in range(3):
            fsm.process_readings(ring)
        conv = _conversation_readings()
        for _ in range(3):
            fsm.process_readings(conv)
        door = _door_open_readings()
        for _ in range(3):
            fsm.process_readings(door)
        assert fsm.state == State.DOOR_OPEN
        event = fsm.hangup()
        assert event == "hangup"
        assert fsm.state == State.IDLE

    def test_hangup_from_idle_fails(self):
        fsm = DoorFSM()
        event = fsm.hangup()
        assert event is None
        assert fsm.state == State.IDLE

    def test_open_door_from_conversation(self):
        fsm = DoorFSM()
        ring = _ringing_readings()
        for _ in range(3):
            fsm.process_readings(ring)
        conv = _conversation_readings()
        for _ in range(3):
            fsm.process_readings(conv)
        assert fsm.state == State.CONVERSATION
        event = fsm.open_door()
        assert event == "door_open"
        assert fsm.state == State.DOOR_OPEN

    def test_open_door_from_idle_fails(self):
        fsm = DoorFSM()
        event = fsm.open_door()
        assert event is None
        assert fsm.state == State.IDLE

    def test_open_door_from_ringing_fails(self):
        fsm = DoorFSM()
        ring = _ringing_readings()
        for _ in range(3):
            fsm.process_readings(ring)
        event = fsm.open_door()
        assert event is None
        assert fsm.state == State.RINGING

    def test_hangup_from_ringing_returns_missed(self):
        fsm = DoorFSM()
        ring = _ringing_readings()
        for _ in range(3):
            fsm.process_readings(ring)
        assert fsm.state == State.RINGING
        event = fsm.hangup()
        assert event == "missed"
        assert fsm.state == State.IDLE
