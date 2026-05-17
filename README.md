# ARM SOM ASR

Wyoming satellite + intercom bridge for Khadas VIM2 (aarch64).

## Components

### `wyoming-satellite/`
Wyoming satellite service for Home Assistant voice assistant.
- Mic: `arecord` via ALSA (plughw:1,0, 16kHz mono S16_LE)
- Speaker: `aplay` via ALSA (plughw:1,0, 22050Hz mono S16_LE)
- Port: 10700

### `telemetrix-domofon/`
Intercom controller using Telemetrix + RP2040 Pico.
- Monitors intercom line via ADC
- FSM: IDLE → RINGING → CONVERSATION → DOOR_OPEN
- Telegram bot for remote control (answer, hangup, open door)
- Audio bridge via PulseAudio loopback

### `intercom_bridge.py`
WebSocket bridge to Home Assistant intercom_native integration.
- Connects to HA via WebSocket API
- Bridges mic/speaker audio to ESP32 Smart 86 Box

### `khadas_intercom_bridge.py`
Direct TCP bridge to ESP32 intercom.
- Binary protocol: header [type:u8][flags:u8][length:u16LE]
- Audio: 16kHz mono int16, 1024 bytes per chunk

### `intercom_peer.py`
Async TCP peer implementing the intercom_native protocol.
- Supports incoming/outgoing calls
- Auto-answer with configurable delay
- Audio bridge via asyncio subprocesses

## Hardware

- Khadas VIM2 (Amlogic S912, aarch64)
- USB sound card (UACDemoV10)
- RP2040 Pico (ADC for intercom line monitoring)

## Deployment

### Wyoming Satellite

```bash
# Install
python3 -m venv /opt/wyoming-venv
/opt/wyoming-venv/bin/pip install wyoming-satellite

# Copy service
cp wyoming-satellite/wyoming-satellite.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now wyoming-satellite
```

### Domofon Controller

```bash
cd telemetrix-domofon
pip install -r requirements.txt
python3 src/main.py
```
