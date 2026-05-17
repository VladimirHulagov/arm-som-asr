import asyncio
import glob
import signal
import logging

from src.config import (
    TELEGRAM_TOKEN,
    TELEGRAM_CHAT_ID,
    RING_TIMEOUT_SEC,
    PICO_UNIQUE_ID,
)
from src.fsm import DoorFSM
from src.intercom_monitor import IntercomMonitor
from src.audio_bridge import AudioBridge
from src.telegram_bot import DomofonBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("domofon")


async def ring_timeout(monitor, fsm):
    await asyncio.sleep(RING_TIMEOUT_SEC)
    if fsm.state.name == "RINGING":
        await monitor.hangup()


def find_pico_board():
    from tmx_pico_aio import tmx_pico_aio

    target_id = [
        int(PICO_UNIQUE_ID[i : i + 2], 16)
        for i in range(0, len(PICO_UNIQUE_ID), 2)
    ]

    for port in sorted(glob.glob("/dev/ttyACM*")):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            b = tmx_pico_aio.TmxPicoAio(com_port=port)
            reported = list(getattr(b, "reported_pico_id", []))
            if reported == target_id:
                logger.info("Pico found on %s (ID: %s)", port, reported)
                return b, loop
            logger.info("Skipping %s: %s != %s", port, reported, target_id)
            loop.run_until_complete(b.shutdown())
            loop.close()
        except Exception:
            continue
    return None, None


async def run(board):
    fsm = DoorFSM()
    audio = AudioBridge()
    bot = DomofonBot(None, audio, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)
    timeout_handle = None

    async def on_event(event):
        nonlocal timeout_handle
        logger.info("event=%s state=%s", event, fsm.state.name)
        await bot.on_event(event)
        if event == "ringing":
            if timeout_handle is not None:
                timeout_handle.cancel()
            timeout_handle = asyncio.ensure_future(ring_timeout(monitor, fsm))
        elif event in ("answered", "missed", "hangup"):
            if timeout_handle is not None:
                timeout_handle.cancel()
                timeout_handle = None

    monitor = IntercomMonitor(fsm, on_event)
    bot.monitor = monitor

    await monitor.setup(board)
    await bot.start_async()
    logger.info("Domofon system started")

    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown_event.set)
    await shutdown_event.wait()

    logger.info("Shutting down...")
    if timeout_handle is not None:
        timeout_handle.cancel()
    await bot.stop()
    await audio.stop()
    await board.shutdown()
    logger.info("Domofon system stopped")


def main():
    board, loop = find_pico_board()
    if not board:
        logger.error("DOMOFON Pico not found on any /dev/ttyACM*")
        return

    try:
        loop.run_until_complete(run(board))
    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        try:
            loop.run_until_complete(board.shutdown())
        except Exception:
            pass
        loop.close()


if __name__ == "__main__":
    main()
