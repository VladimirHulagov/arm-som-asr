#!/usr/bin/env python3
import asyncio
import glob
import serial
import time

PSU_PORT = "/dev/ttyUSB0"
PSU_BAUD = 9600
PICO_ID = [83, 3, 40, 71, 40, 234, 0, 0]
ADC_CHANNEL = 0
MOSFET_PIN = 14
MOSFET2_PIN = 15
ADC_MAX = 4095
VREF = 3.3

PSU_VOLTAGES = [3.0, 6.5, 9.8]
MOSFET_STATES = [
    (0, 0, "OFF OFF"),
    (1, 0, "ANSWER"),
    (1, 1, "ANSWER+DOOR"),
    (0, 0, "OFF OFF"),
]


def psu_cmd(ser, cmd):
    ser.write(cmd.encode())
    return ser.read_until(b">").decode()


def psu_init(ser):
    ser.flush()
    for cmd in ["<09100000000>", "<01004580000>", "<03006920000>"]:
        psu_cmd(ser, cmd)


def psu_set_voltage(ser, v):
    val = "{:07.3f}".format(v).replace(".", "")
    return "OK" in psu_cmd(ser, f"<01{val}000>")


def psu_set_current(ser, a):
    val = "{:07.3f}".format(a).replace(".", "")
    psu_cmd(ser, f"<03{val}000>")


def psu_output_on(ser):
    return "OK" in psu_cmd(ser, "<07000000000>")


def psu_output_off(ser):
    return "OK" in psu_cmd(ser, "<08000000000>")


def psu_read_voltage(ser):
    ser.write(b"<02000000000>")
    data = ser.read_until(b">")
    return float(data[3:9].decode()) * 1e-3


def psu_read_current(ser):
    ser.write(b"<04000000000>")
    data = ser.read_until(b">")
    return float(data[3:9].decode()) * 1e-3


def find_pico(target_id):
    from tmx_pico_aio import tmx_pico_aio

    for port in sorted(glob.glob("/dev/ttyACM*")):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            b = tmx_pico_aio.TmxPicoAio(com_port=port)
            reported = list(getattr(b, "reported_pico_id", []))
            if reported == list(target_id):
                print(f"Pico found on {port}")
                return b, loop
            print(f"  Skipping {port}: {reported}")
            loop.run_until_complete(b.shutdown())
            loop.run_until_complete(asyncio.sleep(0.5))
            loop.close()
        except Exception:
            continue
    return None, None


def main():
    print("=== MOSFET + ADC Load Test ===\n")

    psu = serial.Serial(port=PSU_PORT, baudrate=PSU_BAUD, timeout=1)
    psu_init(psu)
    psu_set_current(psu, 1.0)
    psu_set_voltage(psu, 0.0)
    psu_output_off(psu)
    print("PSU initialized\n")

    board, loop = find_pico(PICO_ID)
    if not board:
        print("Pico not found!")
        psu.close()
        return

    latest = [None]

    async def on_adc(data):
        latest[0] = data[2]

    loop.run_until_complete(board.set_pin_mode_analog_input(ADC_CHANNEL, 0, on_adc))
    loop.run_until_complete(board.set_pin_mode_digital_output(MOSFET_PIN))
    loop.run_until_complete(board.set_pin_mode_digital_output(MOSFET2_PIN))
    loop.run_until_complete(asyncio.sleep(0.3))

    print("{:>8s} {:>14s} {:>8s} {:>8s} {:>8s} {:>8s}".format(
        "PSU set", "MOSFET", "PSU V", "PSU A", "ADC raw", "ADC V"
    ))
    print("-" * 62)

    try:
        psu_output_on(psu)

        for v_set in PSU_VOLTAGES:
            psu_set_voltage(psu, v_set)
            loop.run_until_complete(asyncio.sleep(0.8))

            for m1, m2, label in MOSFET_STATES:
                loop.run_until_complete(board.digital_write(MOSFET_PIN, m1))
                loop.run_until_complete(board.digital_write(MOSFET2_PIN, m2))
                loop.run_until_complete(asyncio.sleep(0.5))

                v_psu = psu_read_voltage(psu)
                i_psu = psu_read_current(psu)
                raw = latest[0]
                v_adc = raw * VREF / ADC_MAX if raw else 0

                print(f"{v_set:>8.2f} {label:>14s} "
                      f"{v_psu:>8.3f} {i_psu:>8.3f} {raw or 0:>8d} {v_adc:>8.3f}")
            print()

    finally:
        loop.run_until_complete(board.digital_write(MOSFET_PIN, 0))
        loop.run_until_complete(board.digital_write(MOSFET2_PIN, 0))
        loop.run_until_complete(board.digital_write(MOSFET2_PIN, 0))
        loop.run_until_complete(board.shutdown())
        loop.close()
        psu.close()


if __name__ == "__main__":
    main()
