import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.audio_bridge import AudioBridge
from src.config import (
    PULSEAUDIO_PC_SINK,
    PULSEAUDIO_PC_SOURCE,
    PULSEAUDIO_USB_SINK,
    PULSEAUDIO_USB_SOURCE,
)


def _make_mock_proc(stdout=b"0"):
    proc = AsyncMock()
    proc.wait = AsyncMock()
    proc.stdout = AsyncMock()
    proc.stdout.read = AsyncMock(return_value=stdout)
    return proc


class TestStart:
    @pytest.mark.asyncio
    async def test_start_loads_two_loopback_modules(self):
        proc = _make_mock_proc(b"42")
        with patch(
            "src.audio_bridge.asyncio.create_subprocess_exec",
            return_value=proc,
        ) as mock_exec:
            bridge = AudioBridge()
            await bridge.start()

        assert mock_exec.call_count == 2
        mock_exec.assert_any_call(
            "pacmd",
            "load-module",
            f"module-loopback sink={PULSEAUDIO_PC_SINK} source={PULSEAUDIO_USB_SOURCE}",
        )
        mock_exec.assert_any_call(
            "pacmd",
            "load-module",
            f"module-loopback sink={PULSEAUDIO_USB_SINK} source={PULSEAUDIO_PC_SOURCE}",
        )

    @pytest.mark.asyncio
    async def test_start_idempotent(self):
        proc = _make_mock_proc(b"42")
        with patch(
            "src.audio_bridge.asyncio.create_subprocess_exec",
            return_value=proc,
        ) as mock_exec:
            bridge = AudioBridge()
            await bridge.start()
            await bridge.start()

        assert mock_exec.call_count == 2

    @pytest.mark.asyncio
    async def test_start_tracks_active_state(self):
        proc = _make_mock_proc(b"42")
        with patch(
            "src.audio_bridge.asyncio.create_subprocess_exec",
            return_value=proc,
        ):
            bridge = AudioBridge()
            assert bridge.is_active is False
            await bridge.start()
            assert bridge.is_active is True


class TestStop:
    @pytest.mark.asyncio
    async def test_stop_unloads_modules(self):
        load_proc = _make_mock_proc(b"42")
        unload_proc = _make_mock_proc(b"")
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return load_proc
            return unload_proc

        with patch(
            "src.audio_bridge.asyncio.create_subprocess_exec",
            side_effect=side_effect,
        ) as mock_exec:
            bridge = AudioBridge()
            await bridge.start()
            assert bridge.is_active is True
            await bridge.stop()
            assert bridge.is_active is False

        assert mock_exec.call_count == 4
        mock_exec.assert_any_call("pacmd", "unload-module", "42")

    @pytest.mark.asyncio
    async def test_stop_when_not_active_is_noop(self):
        with patch(
            "src.audio_bridge.asyncio.create_subprocess_exec"
        ) as mock_exec:
            bridge = AudioBridge()
            await bridge.stop()

        mock_exec.assert_not_called()
