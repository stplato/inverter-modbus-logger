# Step 1 notes — How Modbus TCP really works

## The data model: 4 tables

| Table | Size | Access | Read FC | Write FC | Inverter example |
|---|---|---|---|---|---|
| Coils | 1 bit | read / write | 01 | 05, 15 | Start / stop command |
| Discrete inputs | 1 bit | read only | 02 | — | Breaker closed? |
| **Holding registers** | 16 bit | read / write | **03** | **06** (one), **16** (many) | Power limit, PF setpoint |
| **Input registers** | 16 bit | read only | **04** | — | AC power, voltage, temperature |

Key idea: a register is just a 16-bit number (0–65535). It has no unit and no decimal point.
The device's **register map** (its manual) tells you what each number means and how to scale it.
That's why the client divides the voltage by 10: `2304` → `230.4 V`.

> Note: many modern devices (SunSpec inverters included) put *everything*, even measurements,
> in holding registers and read them with FC03. Always check the register map.

## Anatomy of a real frame (from your server's trace)

Client asks: **"read 4 input registers starting at 0 from device 1"**

```
00 01 | 00 00 | 00 06 | 01 | 04 | 00 00 | 00 04
  │       │       │     │    │     │       └─ quantity: 4 registers
  │       │       │     │    │     └───────── start address: 0
  │       │       │     │    └─────────────── function code 04 = Read Input Registers
  │       │       │     └──────────────────── unit id (device id) = 1
  │       │       └────────────────────────── length of what follows = 6 bytes
  │       └────────────────────────────────── protocol id = 0 (always 0 for Modbus)
  └────────────────────────────────────────── transaction id = 1 (matches answer to question)
└───────────── MBAP header (7 bytes) ──────┘└────────── PDU ───────────┘
```

Server answers:

```
00 01 00 00 00 0b 01 | 04 | 08 | 11 a8 | 09 00 | 13 89 | 00 29
                     │    │    0x11a8  0x0900  0x1389  0x0029
                     │    │    = 4520  = 2304  = 5001  = 41
                     │    └─ byte count: 8 (4 registers × 2 bytes)
                     └────── function code echoed: 04
```

Each register is **2 bytes, big-endian** (most significant byte first).

## When something is wrong: exception responses

Reading register 500, which does not exist:

```
00 06 00 00 00 03 01 | 84 | 02
                       │    └─ exception code 02 = ILLEGAL DATA ADDRESS
                       └────── 0x84 = 0x04 + 0x80 → "FC04 failed"
```

| Code | Name | Typical cause in the field |
|---|---|---|
| 01 | Illegal function | Device doesn't support that FC (e.g. FC04 on a SunSpec device) |
| 02 | Illegal data address | Wrong register map / off-by-one address |
| 03 | Illegal data value | Value out of range, or quantity too large |
| 04 | Device failure | Internal error in the device |
| 0B | Gateway target failed to respond | Device behind a gateway is offline |

## The off-by-one trap (very common in commissioning)

Old documentation uses "register 40001" to mean *holding register at address 0*.
pymodbus (and the bytes on the wire) use the **0-based address**. So "40001" → `address=0`, "30005" → input register `address=4`.

## My explanations (checkpoint)

*Write your own answers here, in your words:*

1. Difference between a holding and an input register:
2. What function code 03 does:
3. Why the client divides 2304 by 10:
4. What exception code 02 means and when I'd see it on site:
