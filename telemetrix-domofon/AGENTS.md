# Домофонный контроллер на базе Telemetrix + RP2040

## Статус: реализация завершена, 44/44 тестов проходят

## Pico ID (USB Serial → Telemetrix reported_pico_id)

| Назначение | USB Serial | reported_pico_id (Telemetrix) |
|---|---|---|
| **DOMOFON** | `REDACTED_PICO_ID` | `[83, 3, 40, 71, 40, 234, 0, 0]` |
| **DS18B20** | `E6632C859330212D` | `[230, 99, 44, 133, 147, 48, 0, 0]` |

Примечание: Telemetrix firmware обрезает последние 2 байта unique ID (всегда `[0, 0]`).

## Компоненты проекта

### Аппаратное обеспечение
- **MCU:** RP2040 (Raspberry Pi Pico)
- **Хост:** Linux ПК (USB-подключение к Pico)
- **Аудио:** USB звуковая карта (line-in / line-out) + аудиотрансформатор 600:600Ω
- **Изоляция:** опторазвязка 4N35
- **Управление:** 4-канальная MOSFET-плата IAR P202D (IRF540N + EL 4N35)
- **Подключение к линии:** делитель напряжения (10k / 3.3k) → ADC Pico

### Программное обеспечение
- **Firmware:** Telemetrix4RpiPico (UF2)
- **Python-клиент:** tmx-pico-aio (asyncio)
- **Аудиомост:** PulseAudio loopback (full-duplex)
- **Управление:** Telegram Bot API (inline-кнопки: ответить, открыть, сбросить)

## Измеренные уровни напряжения линии домофона

| Состояние | Напряжение | Длительность |
|---|---|---|
| Вызов (гудки) | 1.28V → 4.25V → 2.31V → 4.20V (колебания) | ~5 сек |
| Снятие трубки, разговор | 6.50V (стабильно) | -- |
| Команда открытия двери | 9.80V (стабильно) | ~4 сек |
| Переходный процесс | 7.16V | ~1 сек |
| Возврат в разговор | 6.50V | -- |

## GPIO RP2040

| Пин | Назначение | Направление |
|---|---|---|
| GP26 (ADC0) | Измерение напряжения линии (через делитель) | Analog Input |
| GP14 | MOSFET «снять трубку» (ответить) | Digital Output |
| GP15 | MOSFET «открыть дверь» | Digital Output |

## Конечный автомат (FSM)

### Состояния
- `IDLE` — ожидание вызова
- `RINGING` — обнаружены колебания напряжения (вызов)
- `CONVERSATION` — трубка снята, напряжение ~6.5V
- `DOOR_OPEN` — команда открытия, напряжение ~9.8V

### Переходы

| Из | В | Условие (ADC, 12-bit, 0-4095) | Событие |
|---|---|---|---|
| IDLE | RINGING | Разброс в окне > 300 | "ringing" |
| RINGING | CONVERSATION | avg > 1800 AND разброс < 100 | "answered" |
| CONVERSATION | DOOR_OPEN | avg > 2700 | "door_open" |
| DOOR_OPEN | CONVERSATION | avg < 2400 | "door_closed" |
| CONVERSATION | IDLE | avg < 800 | "hangup" |
| RINGING | IDLE | таймаут / manual | "missed" |

Дребезг: каждый переход требует удержания условия 300 мс (3 семпла при ~100 мс).

## ADC калибровка (PSU + делитель 10k/3.3k)

| PSU V | ADC raw | ADC V | Примечание |
|---|---|---|---|
| 0.50 | 158 | 0.127 | |
| 1.00 | 296 | 0.239 | |
| 1.50 | 440 | 0.355 | |
| 2.00 | 570 | 0.459 | |
| 3.00 | 856 | 0.690 | вызов (нижняя граница) |
| 4.00 | 1144 | 0.922 | |
| 5.00 | 1433 | 1.155 | вызов (верхняя граница) |
| 6.50 | 1882 | 1.517 | разговор |
| 9.80 | 2889 | 2.328 | открытие двери |
| 10.00 | 2954 | 2.381 | |

Коэффициент делителя: ~0.248 (теория: 3.3k / (10k + 3.3k))

## MOSFET подключение

Плата IAR P202D (IRF540N + EL 4N35):
- **Power VCC/GND:** питание gate драйвера (нужен 9-12V, изолированный от линии)
- **Канал 1 (GP14):** ответить — R_answer через MOSFET drain-source поперёк линии
- **Канал 2 (GP15):** открыть дверь — R_door через MOSFET drain-source поперёк линии
- IRF540N требует Vgs ~10V для полного открытия
- На плате LED индикаторы соединены с power VCC → утечка обратно в линию при общем GND

## Критические особенности tmx-pico-aio

### 1. Нельзя использовать `asyncio.run()` с TmxPicoAio
Конструктор создаёт свой event loop внутри. Правильный паттерн:
```python
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
board = tmx_pico_aio.TmxPicoAio(com_port="/dev/ttyACM0")
loop.run_until_complete(some_async_func(board))
loop.close()
```

### 2. Нельзя использовать `pico_instance_id` при нескольких Pico
При несовпадении ID на первом порту падает с RuntimeError, не пробуя следующие. Решение — `find_pico_board()`:
```python
def find_pico_board(target_id):
    for port in sorted(glob.glob("/dev/ttyACM*")):
        b = tmx_pico_aio.TmxPicoAio(com_port=port)
        reported = list(getattr(b, 'reported_pico_id', []))
        if reported == target_id:
            return b, loop  # вернуть уже подключённый board
        loop.run_until_complete(b.shutdown())
        loop.close()
```

### 3. `shutdown()` вызывает USB-ресет Pico
Нельзя: найти board → shutdown → подключить снова. Нужно вернуть уже подключённый board и использовать его event loop.

### 4. Один event loop на всё
Dispatcher board'а работает на loop из конструктора. Новый loop → коллбэки не срабатывают.
