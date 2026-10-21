# Learning log — interview preparation

Short answers in my own words, written after each checkpoint.

## Step 0 — Environment
- **Virtual environment:** isolates each project's Python packages so versions don't conflict;
  `requirements.txt` makes the environment reproducible on any machine.

## Step 1 — Modbus basics (details in `step1-modbus-notes.md`)
- **Holding vs input register:** input = read-only measurements (FC04); holding = read/write settings (FC03 read, FC06/FC16 write).
- **Scaling:** registers hold only whole numbers, so 230.4 V is sent as 2304 and the register map gives the factor (÷10).
- **Exception 02 (Illegal data address):** the register doesn't exist → wrong register map or off-by-one (40001 vs address 0).
  Reading a block that runs past the last register fails completely.
- **Wrong unit ID:** real device → timeout; behind a gateway → exception 0B.

## Walkthrough 1 — `pv_model.py`

### Tilt exercise (Athens, clear sky, 120 kWp)

| Tilt | 15 July | 21 December |
|---|---|---|
| 10° | **855 kWh** | 361 kWh |
| 30° | 799 kWh | 482 kWh |
| 60° | 594 kWh | **568 kWh** |

- **Rule:** a panel produces most when sunlight hits it straight on, so the best tilt ≈ **90° − sun elevation at noon**.
  July: 90 − 74 = 16° (low tilt wins). December: 90 − 29 = 61° (steep tilt wins).
- **Why fixed plants in Greece use ~25–30°:** a fixed panel must work all year. The best *annual* tilt is a bit below
  the latitude (~38°) because summer has more sun hours, so it counts more. Lower tilt also means less shading
  between rows (more MW per hectare) and less wind load on the structure.

### Checkpoint 1 — Why clipping in April but not in July?

| 13:30 | POA | T_amb | T_cell | P_dc available | P_ac |
|---|---|---|---|---|---|
| 15 April | 1020 W/m² | 21.7 °C | 53.6 °C | 106.2 kW | **100.0 (clipped)** |
| 15 July | 1018 W/m² | 32.5 °C | 64.3 °C | 101.2 kW | 99.2 |

- Same sunlight, but in July the air is ~11 °C hotter, so the cells are ~11 °C hotter.
- Power temperature coefficient γ = −0.37 %/°C → 11 °C × 0.37 % ≈ **4 % less DC power** in July.
- In April the array gives more DC than the 100 kW inverter can convert → it clips. In July, heat losses
  keep DC just below the limit.
- **Interview sentence:** "PV output depends on irradiance *and* cell temperature. Cool, bright spring days
  give the highest power and the most clipping; hot summer days give more energy (longer days) but lower peak power."

### Checkpoint 2 — Why is POA higher than GHI in winter?

21 Dec, 12:30, sun elevation 28.6°: **GHI 438 W/m²** (flat ground) vs **POA 722 W/m²** (30° south) → +65 %.

- **Cosine law:** light received = direct beam × cos(AOI), AOI = angle of incidence (sunbeam vs. surface normal).
- Flat ground: AOI 61.4° → cos = 0.48. Panel at 30°: AOI 31.4° → cos = 0.85 → ~77 % more beam light.
  Diffuse light doesn't gain from tilting, so the total gain is +65 %.
- **Interview sentence:** "POA is irradiance on the module plane. In winter the sun is low, so tilt reduces the angle
  of incidence and POA is far above GHI. Performance KPIs like PR must be calculated with POA, not GHI."

### Checkpoint 3 — Why oversize the DC side (DC/AC ratio 1.2)?

| Day | Ratio 1.0 (100 kWp) | Ratio 1.2 (120 kWp) | Lost to clipping |
|---|---|---|---|
| 15 April | 666 kWh | 792 kWh | 6 kWh |
| 15 July | 666 kWh | 799 kWh | 0 kWh |
| 21 December | 402 kWh | 482 kWh | 0 kWh |

- +20 % panels → **+19–20 % energy every day**, while clipping loses **<1 %**, and only at a few noon hours on cool bright days.
- An inverter rated for 100 kW sits mostly at part load: in the morning, evening, winter and on cloudy days the panels
  give far less than their peak. Extra panels fill those hours.
- Panels are cheap; the inverter, transformer and **grid connection capacity (MW)** are expensive and limited.
  Oversizing uses the expensive grid connection more hours per year.
- Typical utility PV: DC/AC 1.2–1.4. With a battery, the "clipped" energy can be stored instead of lost.
- **Interview sentence:** "Oversizing the DC side gives about 20 % more energy from the same inverter and grid connection,
  for a clipping loss of around 1 %. The connection capacity is the scarce, expensive part, so you load it more hours per year."

## Walkthrough 2 — SunSpec register map

### Exercise: `W_SF = 0` (scale factor broken on purpose)
- With W_SF = 0, power is sent in whole watts in one int16 register (max **32,767**).
- At **32,782 W** (08:36) our firmware refused: `ValueError: 32782 does not fit in int16` → server crashed →
  SCADA saw **COMMS_LOST** with backoff 1-2-4-8-16-30 s.
- A real firmware without the check **wraps around**: 32,782 → 0x800E → read as int16 = **−32,754 W** (negative power at noon!).
- **Field rule:** negative power at midday, or jumps of ~65.5 kW (65,536) → suspect 16-bit overflow:
  wrong scale factor or wrong data type (int16/uint16) in the SCADA mapping.
- Our firmware uses W_SF = 1 (10 W steps) → 100 kW = 10,000, far below 32,767.
- Read Python errors **bottom-up**: the last line is the cause.

### Checkpoint 1 — Why is WH 32-bit over two registers?
- One register max = 65,535 Wh = 65.5 kWh → a 100 kW plant would wrap to 0 in ~40 minutes.
- Two registers (32-bit) max = 4,295 MWh. Value = **high word × 65,536 + low word**
  (152,340,000 = 2324 × 65,536 + 34,336).
- Field trap: **word order** — some vendors send the low word first. SunSpec fixes high word first.
- **Interview sentence:** "Lifetime energy counters exceed the 65,535 limit of a 16-bit register within an hour, so they
  use 32 bits across two registers: value = high × 65,536 + low. You must check the word order in the register map."

### Checkpoint 2 — How does the SCADA find model 103 without knowing its address?
- **Discovery:** check `SunS` at 40000, then walk the chain. Each model starts with **[ID][L]**;
  next model address = **current + 2 + L**, until ID = 0xFFFF.
- Ours: 1 @ 40002 (L=66) → 103 @ 40070 (L=50) → 123 @ 40122 (L=24) → 64001 @ 40148 (L=4) → end @ 40154.
- Different brands put models in different places; discovery lets one driver work for all.
- **Interview sentence:** "SunSpec devices start with a 'SunS' marker at 40000, followed by a chain of models, each beginning
  with its ID and length. The SCADA walks the chain (address + 2 + L), so one driver works across manufacturers."

## Walkthrough 3 — Live server (`simulator/server.py`) + probe tool

### Decoding real frames (`python -m logger.probe read 40070 2`)
```
SENT      00 01 | 00 00 | 00 06 | 01 | 03 | 9c 86 | 00 02
          trans.  proto.  length  dev  FC03  start   count
RECEIVED  00 01 00 00 00 07 01 | 03 | 04 | 00 67 | 00 32
                                 FC   bytes  103     50
```
- Two bytes form ONE 16-bit number: 0x9C86 = 156 × 256 + 134 = **40070** (start address). Calculator → Programmer mode.
- 0x0067 = **103** = model ID; 0x0032 = **50** = model length L → the [ID][L] header on the wire.
- W @ 40084 (= 40070 + 2 + 12): `[2666, 1]` → 2666 × 10¹ = **26,660 W = 26.66 kW** (value depends on the simulated time).

### Why the inverter refuses `write 40084 5000` → exception 02
- 5000 is the **value**, 40084 is the **address**; the address exists (we just read it).
- 40084 is a **measurement** (AC power). Measurements come from physics; nobody can "write" what the inverter produces.
  Only **setpoints/commands** are writable (here: model 123 Conn, WMaxLimPct, WMaxLim_Ena).
- The server answers **exception 02** (address not writable); other vendors may answer 01 or 03 — check the manual.
- Security/safety: a device that accepted random writes could be damaged or mis-reported. Read-only measurements protect data integrity.
- **Interview sentence:** "Measurement registers are read-only; only control registers like the active-power limit accept
  writes. Writing a measurement returns an exception, typically 02 Illegal Data Address."

## Walkthrough 4 — The logger (`logger/decoder.py` + `logger/poller.py`)

### Exercise: 45 s comms failure (20 Oct, 13:37:26 → 13:38:11)
- CSV: **243 GOOD / 23 COMMS_LOST** → data availability 243/266 = **91.4 %**.
- After reconnect: re-discovery + "energy counter went BACKWARDS" (our server restart = inverter restart).

### Checkpoint 1 — Is COMMS_LOST plant downtime?
| | Real comms loss (cable, router) | Device restart / replacement |
|---|---|---|
| Inverter producing? | yes | no |
| Energy counter | keeps rising | goes backwards / resets |
| Logger | adds the jump after reconnect | ignores the backwards jump, warns |
| Plant availability | **not** downtime | **is** downtime |
- **Interview sentence:** "A comms loss isn't plant downtime. The inverter keeps producing and its energy counter keeps counting,
  so after reconnect the counter delta recovers the energy. That's why energy is calculated from counters, not from power
  samples. Data availability and plant availability are separate KPIs."

### Checkpoint 2 — Why read a whole model in one block?
1. **Consistent snapshot:** all values (and their scale factors) from the same instant. If W and W_SF are read separately and the
   inverter changes SF in between → 10× errors.
2. **Speed:** each request has fixed overhead. 100 inverters on one RS485 line: ~2.5 min/cycle with single reads vs ~5 s with block reads.
   FC03 max = 125 registers per request.
- **Interview sentence:** "Block reads give a consistent snapshot, so values and their scale factors always match, and they're much
  faster because each request has fixed overhead."

### Checkpoint 3 — Why exponential backoff (1 → 2 → 4 → … → 30 s)?
- Fast retries every 0.1 s × 100 inverters = **1,000 failed attempts/s**: floods the network, gateway, SCADA CPU and logs, for nothing.
- When the switch comes back, 100 clients hammering at once (**"thundering herd"**) can overload a gateway or a device still booting.
- Backoff = quick retry for short glitches (1 s, 2 s), patient for long outages, **capped** (30 s) so recovery is noticed fast.
  Real systems also add a little random **jitter** so clients don't retry in sync.
- **Interview sentence:** "Exponential backoff retries quickly after a short glitch but slows down during a long outage, so the SCADA
  doesn't flood the network or overload devices when they come back. A cap keeps recovery time short."

## Step 6B — Database design (`sql/schema.sql`)

### Why `readings` stores only `device_id` (normalization)
- 1 inverter × 1 reading / 5 s ≈ **6.3 million rows per year**.
- Repeating "RES-Lab SIM-INV-100K GR-ATH-0001" (~30 bytes) in every row ≈ 190 MB/year of duplicate text; an integer id ≈ 25 MB.
- Inverter replaced → design A: change millions of rows (and risk inconsistency); design B: one row in `devices`.
  Better still: the replacement gets a **new device row**, so history shows which unit produced what (warranty).
- **Interview sentence:** "The readings table stores only a device_id foreign key; device details live once in a devices
  table. That saves space on millions of time-series rows and means a change like an inverter replacement is made in one
  place, so the data stays consistent."

### Constraints = the database guards its own data quality (all tested 20 Oct)
| Constraint | Rule | What it rejected |
|---|---|---|
| `UNIQUE (serial_number)` | one row per physical inverter | the same inverter registered twice |
| `FOREIGN KEY device_id → devices` | a reading must belong to a known device | a reading for device 99 |
| `CHECK (quality IN ('GOOD','COMMS_LOST'))` | only valid quality flags | quality = 'BROKEN' |
| `PRIMARY KEY (device_id, time)` | one reading per device per instant | duplicate readings |

- Data types: `real` for measurements, `numeric(14,3)` for energy counters (exact), `timestamptz` for time.
- **Interview sentence:** "I let the database enforce data quality with constraints — unique serials, foreign keys and
  check constraints — so bad data is rejected at the door instead of being cleaned up later."

## Step 6C — Logger → PostgreSQL (`logger/db.py`)

### Store-and-forward (tested on my PC, 20 Oct)
- `docker compose stop` while logging → "database unavailable – buffering readings" → 11 readings kept in memory →
  `docker compose start` → "wrote 11 buffered readings". **59 printed = 59 rows in the DB: nothing lost.**
- **Interview sentence:** "The logger uses store-and-forward: if the historian is down, readings are buffered locally and
  written when it returns, so a database restart never creates a gap in the data."

### Bug found: a database problem slowed down polling
- During the outage, polls went from every 2 s to every ~3 s: each poll waited for the 3 s DB connect timeout.
- On a real site, a historian problem must **never** slow data acquisition from the devices.
- Fix: **reconnect backoff for the DB too** (5 → 10 → 20 → 30 s). Retest: only 2 slow polls in an 18 s outage, then normal.
- Professional systems go further: the DB writer runs in a **separate thread or process** with a queue between poller and writer.

### Other 6C details
- Device registered by serial number (`ON CONFLICT (serial_number) DO UPDATE ... RETURNING device_id`).
- Readings: `ON CONFLICT (device_id, time) DO NOTHING` → duplicate-safe.
- COMMS_LOST rows get an **estimated** device time (last good time + elapsed × clock speed).
