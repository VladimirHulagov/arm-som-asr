from enum import Enum, auto

from src.config import (
    DEBOUNCE_COUNT,
    THR_ANSWERED,
    THR_DOOR_CLOSE,
    THR_DOOR_OPEN,
    THR_IDLE,
    THR_RING_VARIANCE,
    THR_STABILITY,
    WINDOW_SIZE,
)


class State(Enum):
    IDLE = auto()
    RINGING = auto()
    CONVERSATION = auto()
    DOOR_OPEN = auto()


_TRANSITION_EVENTS = {
    (State.IDLE, State.RINGING): "ringing",
    (State.RINGING, State.CONVERSATION): "answered",
    (State.CONVERSATION, State.DOOR_OPEN): "door_open",
    (State.DOOR_OPEN, State.CONVERSATION): "door_closed",
    (State.CONVERSATION, State.IDLE): "hangup",
    (State.DOOR_OPEN, State.IDLE): "hangup",
    (State.RINGING, State.IDLE): "missed",
}


class DoorFSM:
    def __init__(self):
        self.state = State.IDLE
        self._debounce_counter = 0
        self._pending_target = None

    def process_readings(self, readings):
        avg = sum(readings) / len(readings)
        variance = max(readings) - min(readings)
        target = self._evaluate(avg, variance)
        return self._apply(target)

    def answer(self):
        if self.state == State.RINGING:
            return self._transition(State.CONVERSATION)
        return None

    def hangup(self):
        if self.state in (State.CONVERSATION, State.DOOR_OPEN, State.RINGING):
            return self._transition(State.IDLE)
        return None

    def open_door(self):
        if self.state == State.CONVERSATION:
            return self._transition(State.DOOR_OPEN)
        return None

    def _evaluate(self, avg, variance):
        if self.state == State.IDLE:
            if variance > THR_RING_VARIANCE:
                return State.RINGING
        elif self.state == State.RINGING:
            if avg > THR_ANSWERED and variance < THR_STABILITY:
                return State.CONVERSATION
        elif self.state == State.CONVERSATION:
            if avg > THR_DOOR_OPEN:
                return State.DOOR_OPEN
            if avg < THR_IDLE:
                return State.IDLE
        elif self.state == State.DOOR_OPEN:
            if avg < THR_DOOR_CLOSE:
                return State.CONVERSATION
        return None

    def _apply(self, target):
        if target is None:
            self._debounce_counter = 0
            self._pending_target = None
            return None
        if target == self._pending_target:
            self._debounce_counter += 1
        else:
            self._pending_target = target
            self._debounce_counter = 1
        if self._debounce_counter >= DEBOUNCE_COUNT:
            self._debounce_counter = 0
            self._pending_target = None
            return self._transition(target)
        return None

    def _transition(self, target):
        event = _TRANSITION_EVENTS.get((self.state, target))
        self.state = target
        self._debounce_counter = 0
        self._pending_target = None
        return event
