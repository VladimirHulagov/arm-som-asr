import asyncio

from src.config import (
    PULSEAUDIO_PC_SINK,
    PULSEAUDIO_PC_SOURCE,
    PULSEAUDIO_USB_SINK,
    PULSEAUDIO_USB_SOURCE,
)


class AudioBridge:
    def __init__(self):
        self.is_active = False
        self._loaded_modules = []

    async def _pacmd(self, *args):
        return await asyncio.create_subprocess_exec("pacmd", *args)

    async def start(self):
        if self.is_active:
            return

        proc1 = await self._pacmd(
            "load-module",
            f"module-loopback sink={PULSEAUDIO_PC_SINK} source={PULSEAUDIO_USB_SOURCE}",
        )
        await proc1.wait()
        stdout = await self._get_module_index(proc1)
        if stdout is not None:
            self._loaded_modules.append(stdout)

        proc2 = await self._pacmd(
            "load-module",
            f"module-loopback sink={PULSEAUDIO_USB_SINK} source={PULSEAUDIO_PC_SOURCE}",
        )
        await proc2.wait()
        stdout = await self._get_module_index(proc2)
        if stdout is not None:
            self._loaded_modules.append(stdout)

        self.is_active = True

    async def _get_module_index(self, proc):
        stdout_data = await proc.stdout.read() if proc.stdout else b""
        text = stdout_data.decode().strip()
        if text.isdigit():
            return int(text)
        return None

    async def stop(self):
        if not self.is_active:
            return

        for idx in self._loaded_modules:
            await self._pacmd("unload-module", str(idx))

        self._loaded_modules.clear()
        self.is_active = False
