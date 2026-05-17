# Domofon Controller

Intercom (domofon) controller based on RP2040 (Raspberry Pi Pico) with Telemetrix firmware. Monitors line voltage via ADC, controls MOSFET relays for answer/door-open, provides two-way audio bridge through USB sound card, and sends Telegram notifications with inline controls.

## Hardware

- **MCU:** RP2040 Pico (Telemetrix4RpiPico UF2 firmware)
- **Host:** Linux PC (USB connection to Pico)
- **MOSFET board:** 4-channel IRF540N with 4N35 optocouplers (IAR P202D)
- **Audio:** USB sound card + audio transformer 600:600Ω
- **Line connection:** Voltage divider (10kΩ / 3.3kΩ) → ADC

## Measured Line Voltages

| State | Voltage | ADC (12-bit) |
|---|---|---|
| Ringing | 1.3–4.2V (oscillating) | ~300–1250 |
| Conversation | 6.5V (stable) | ~1880 |
| Door open | 9.8V (stable, ~4s) | ~2900 |

## FSM States

`IDLE → RINGING → CONVERSATION → DOOR_OPEN → CONVERSATION → IDLE`

Each transition requires 3 consecutive samples (debounce ~300ms).

## Project Structure

```
├── src/
│   ├── config.py              — ADC thresholds, GPIO pins, Pico ID
│   ├── fsm.py                 — DoorFSM state machine
│   ├── intercom_monitor.py    — Telemetrix ADC + relay control
│   ├── audio_bridge.py        — PulseAudio full-duplex bridge
│   ├── telegram_bot.py        — Telegram inline buttons
│   └── main.py                — Entry point, asyncio event loop
├── tests/                     — 44 pytest tests
├── off-hook.py                — Manual MOSFET toggle utility
├── adc-calibrate.py           — PSU + ADC calibration script
├── adc-mosfet-test.py         — MOSFET switching + ADC test
├── requirements.txt
├── AGENTS.md                  — Project context for AI assistants
└── README.md
```

## Setup

1. Flash `Telemetrix4RpiPico.uf2` onto Pico
2. Assemble circuit (voltage divider + MOSFET + transformer)
3. `pip3 install -r requirements.txt`
4. Set `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID` in `src/config.py`
5. Run `python3 src/main.py`

## Utilities

```bash
# Toggle MOSFET manually (answer relay on GP14)
python3 off-hook.py on    # pick up (wait for Enter to hang up)
python3 off-hook.py off   # hang up

# Calibrate ADC with programmable PSU
python3 adc-calibrate.py

# Test MOSFET switching + ADC response
python3 adc-mosfet-test.py
```

## Tests

```bash
python3 -m pytest tests/ -v
```

## Key Notes

- **Two Picos on same host:** The project uses `find_pico_board()` to locate the correct Pico by unique ID, avoiding conflicts when multiple Picos are connected.
- **TmxPicoAio event loop:** The constructor creates its own asyncio event loop. Do not use `asyncio.run()`. See AGENTS.md for the correct pattern.
- **MOSFET gate drive:** IRF540N requires ~10V Vgs for full enhancement. The optocoupler board needs separate 9-12V power on the output side.
