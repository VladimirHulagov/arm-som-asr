#!/usr/bin/env python3
import asyncio
import glob
import serial
import time

PSU_PORT = "/dev/ttyUSB0"
PSU_BAUD = 9600
PICO_ID = [83, 3, 40, 71, 40, 234, 0, 0]
ADC_CHANNEL = 0
ADC_MAX = 4095
VREF = 3.3

TEST_VOLTAGES = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.5, 7.0, 8.0, 9.8, 10.0]


def psu_init(ser):
    ser.flush()
    for cmd in [b"<09100000000>", b"<01004580000>", b"<03006920000>"]:
        ser.write(cmd)
        ser.read_until(b">")


def psu_set_voltage(ser, v):
    val = "{:07.3f}".format(v).replace(".", "")
    ser.write(f"<01{val}000>".encode())
    resp = ser.read_until(b">").decode()
    return "OK" in resp


def psu_set_current(ser, a):
    val = "{:07.3f}".format(a).replace(".", "")
    ser.write(f"<03{val}000>".encode())
    ser.read_until(b">")


def psu_output_on(ser):
    ser.write(b"<07000000000>")
    return "OK" in ser.read_until(b">").decode()


def psu_output_off(ser):
    ser.write(b"<08000000000>")
    return "OK" in ser.read_until(b">").decode()


def psu_read_voltage(ser):
    ser.write(b"<02000000000>")
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
    print("=== PSU + ADC Calibration ===\n")

    psu = serial.Serial(port=PSU_PORT, baudrate=PSU_BAUD, timeout=1)
    psu_init(psu)
    psu_set_current(psu, 0.1)
    psu_set_voltage(psu, 0.0)
    psu_output_off(psu)
    print("PSU initialized (output OFF)")

    board, loop = find_pico(PICO_ID)
    if not board:
        print("Pico not found!")
        psu.close()
        return

    latest_adc = [None]

    async def on_adc(data):
        latest_adc[0] = (data[2], data[2] * VREF / ADC_MAX)

    loop.run_until_complete(board.set_pin_mode_analog_input(ADC_CHANNEL, 0, on_adc))
    loop.run_until_complete(asyncio.sleep(0.5))

    print("\n{:>8s} {:>8s} {:>8s} {:>10s}".format(
        "PSU set", "PSU real", "ADC raw", "ADC V"
    ))
    print("-" * 42)

    try:
        psu_output_on(psu)
        for v_set in TEST_VOLTAGES:
            psu_set_voltage(psu, v_set)
            loop.run_until_complete(asyncio.sleep(1.0))
            v_real = psu_read_voltage(psu)
            loop.run_until_complete(asyncio.sleep(0.3))
            raw_v = latest_adc[0]
            if raw_v:
                raw, v_adc = raw_v
                print(f"{v_set:>8.2f} {v_real:>8.3f} {raw:>8d} {v_adc:>10.3f}")
            else:
                print(f"{v_set:>8.2f} {v_real:>8.3f} {'---':>8s} {'---':>10s}")
    finally:
        psu_set_voltage(psu, 0.0)
        psu_output_off(psu)
        print("\nPSU output OFF")
        loop.run_until_complete(board.shutdown())
        loop.close()
        psu.close()


if __name__ == "__main__":
    main()
