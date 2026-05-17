#!/usr/bin/env python3
import asyncio
import glob
import sys
from tmx_pico_aio import tmx_pico_aio

PICO_ID = [83, 3, 40, 71, 40, 234, 0, 0]
PIN = 14


async def off_hook(board):
    await board.set_pin_mode_digital_output(PIN)
    await board.digital_write(PIN, 1)
    print(f"GP{PIN} ON (снять трубку)")


async def on_hook(board):
    await board.digital_write(PIN, 0)
    print(f"GP{PIN} OFF (положить трубку)")


def find_pico_board(target_id):
    for port in sorted(glob.glob("/dev/ttyACM*")):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            b = tmx_pico_aio.TmxPicoAio(com_port=port)
            reported = list(getattr(b, 'reported_pico_id', []))
            if reported == list(target_id):
                print(f"Pico found on {port}")
                return b, loop
            print(f"  Skipping {port}: {reported} != {target_id}")
            loop.run_until_complete(b.shutdown())
            loop.close()
        except Exception:
            continue
    return None, None


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "on"

    board, loop = find_pico_board(PICO_ID)
    if not board:
        print("DOMOFON Pico не найден!")
        sys.exit(1)

    if action == "on":
        loop.run_until_complete(off_hook(board))
        input("Нажмите Enter чтобы положить трубку... ")
        loop.run_until_complete(on_hook(board))
    elif action == "off":
        loop.run_until_complete(on_hook(board))
    else:
        print("Usage: off-hook.py [on|off]")

    try:
        loop.run_until_complete(board.shutdown())
        loop.run_until_complete(asyncio.sleep(0.5))
    except Exception:
        pass
    loop.close()


if __name__ == "__main__":
    main()
