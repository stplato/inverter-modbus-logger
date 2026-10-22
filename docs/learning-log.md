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

## Step 6D — KPI queries (`sql/kpi_queries.sql`)

### Clean clear-sky day, 15 April (simulated), 120 kWp / 100 kW
| KPI | Result |
|---|---|
| Daily energy (robust, LAG) | **792.5 kWh** = physics model 792 kWh ✓ |
| Specific yield | **6.60 kWh/kWp** |
| Peak power | 100 kW at 12:22 (THROTTLED = clipping) |
| Hours: MPPT / THROTTLED (clipping) / SLEEPING | 10.7 / 2.2 / 2.8 h |
| Data availability | 99.6 % (237/238) |
- Clipping shows as a **flat top** in the hourly profile (~100 kWh/h for 3 hours).
- Curtailment shows as a **dip**: 60 % limit 12:00–13:30 → ~70 kWh/h, cost ≈ 59 kWh (7.5 % of the day) in my test run.

### SQL techniques
- `GROUP BY date(time)`, `count(*) FILTER (WHERE ...)`, `date_trunc('hour', ...)`.
- **Window functions:** `LAG()` = previous row (counter deltas), `LEAD()` = next row (durations), `PARTITION BY device_id`.

### Simple vs robust daily energy
- max − min breaks when a counter **resets** (inverter replaced: 152,700 − 0 = 152,700 kWh "in one day").
- Sum of positive LAG deltas handles resets (360 + 400 = 760 kWh).
- **But** robust ≠ bulletproof: two runs mixed under one device_id → counter zig-zag → **12,227 kWh / 101.9 kWh/kWp**.
  Found with a **physics sanity check** (>8 kWh/kWp/day is impossible), fixed with
  `DELETE FROM readings WHERE logged_at >= ...` (the logger's own clock column).
- **Interview sentence:** "Before trusting a KPI, I sanity-check it against physics. A daily specific yield of 100 kWh/kWp is
  impossible, so I checked the raw counters and found two devices' data mixed under one ID. Robust formulas handle counter
  resets, but not mapping errors — that's why device identity and data validation matter."

## Step 7A — Fault injection (`simulator/inject.py`, `pv_model.py`)

| Fault | Physics | Inverter reaction |
|---|---|---|
| `fan_failure` | heatsink heats ~2× faster | **derating** above 70 °C → settles ~90 kW / ~71 °C |
| `grid_trip` | frequency 51.6 Hz for 2 min | **FAULT** (GRID_DISCONNECT + OVER_FREQUENCY) → **STARTING** (3 min reconnect delay) → **ramp 10 %/min** |
- Events are reported in SunSpec model 103 **`Evt1`** bitfield (bit 4 GRID_DISCONNECT, 7 OVER_TEMP, 8 OVER_FREQUENCY ...).
- `TestFault` (model 64002) is test-only; real faults come from the real world.

### What the SCADA missed (21 Oct test)
- Inverter: 11:19 FAULT, 51.62 Hz, OVER_FREQUENCY. SCADA (poll every 2 sim-min): 11:18 MPPT → 11:20 STARTING.
- **An event shorter than the poll interval can fall between two polls**: SCADA saw the consequence (GRID_DISCONNECT),
  not the cause (OVER_FREQUENCY).
- Why it matters: wrong root cause (technician sent for nothing), **availability contracts exclude grid-caused downtime**
  (no proof → owner can't claim, O&M gets penalised), grid-code compliance can't be proven.
- Solutions: latched events, device event logs, fast polling of status registers, push protocols (IEC 61850, MQTT).
- **Interview sentence:** "Polling can miss events shorter than the poll interval. In my simulator the SCADA saw the grid
  disconnect but missed the over-frequency that caused it. Real systems use latched event bits, device event logs or push
  protocols like IEC 61850 and MQTT, because the root cause matters for O&M and for availability contracts."

## Step 7B — Alarm manager (`logger/alarms.py`)

### Alarm flapping (the 10-poll question)
- T hovering 69.7–71.1 °C with "ON > 70, OFF ≤ 70" → **4 alarms in 10 polls** (hundreds per day) = **flapping/chattering**.
- Moving the threshold (75 °C) doesn't fix it, it only moves it — and loses the early warning.
- Fix = **on-delay** (3 polls in a row > 70) + **hysteresis** (clear only < 65) → **1 alarm**. Proven in `tests/test_alarms.py`.
- **Interview sentence:** "To avoid alarm flapping I use an on-delay and hysteresis: raise only after the condition persists for several
  polls, and clear at a lower threshold than the raise threshold. One meaningful alarm beats hundreds the operators learn to ignore."

### Three kinds of alarm rules
| Type | Example | Raise | Clear |
|---|---|---|---|
| Device events (Evt1) | GRID_DISCONNECT, OVER_FREQUENCY | bit appears | bit disappears |
| SCADA threshold | HIGH_TEMP (WARNING) | > 70 °C for 3 polls | < 65 °C |
| Communication | COMMS_LOST (CRITICAL) | 3 failed polls | first good poll |
- One active alarm per device+code enforced by a **partial UNIQUE index** (`WHERE cleared_at IS NULL`).
- Alarm raises/clears are **store-and-forward** too (queued in order if the DB is down).

### Test run 22 Oct (fan failure → grid trip → comms loss)
| Alarm | Duration |
|---|---|
| HIGH_TEMP | 28 min, raised **once** while T hovered 70.7–71.5 °C |
| OVER_FREQUENCY | 1 min (caught with 1-min polling) |
| GRID_DISCONNECT | 3 min (incl. reconnect delay) |
| COMMS_LOST | 8 min |

### COMMS_LOST must not clear other alarms
- **Interview sentence:** "When a device stops communicating, I keep its existing alarms active, because unknown is not the same as
  cleared. The comms loss and the fault often share a root cause, like a substation outage, and a false all-clear is worse than an
  honest unknown."

### Clock jumps and overlapping experiments
- Restarting the simulator at an earlier time = device clock jumped back 14:11 → 11:01 → negative alarm duration and mixed counters
  (KPI 2 = 15,000 kWh). Real-world twin: inverter clock reset after power loss, wrong NTP sync.
- Fixes: logger warns "device clock jumped BACKWARDS"; alarm clear time never before raise time.
- Cleanup with `logged_at` (`DELETE FROM readings WHERE logged_at >= '2026-10-22'`) → back to 792.5 kWh.
- Rule: **one experiment = one simulated day**.

### Data lineage detective work (22 Oct)
- KPI 2 still 119.7 kWh/kWp after deleting today's rows → a third, forgotten run (Step 7A test, logged 21 Oct 15:50–16:02).
- Found by grouping on the logger's clock: `SELECT date_trunc('hour', logged_at), count(*), min(time), max(time) ... GROUP BY 1`
  (grouping by **minute** separates runs logged in the same hour).
- `DELETE FROM readings WHERE logged_at >= '2026-10-21 15:21+03'` → **792.5 kWh / 6.60 kWh/kWp** ✓
- **Interview sentence:** "Before computing KPIs I check data lineage — which source and which run each row came from. A logger
  timestamp column next to the device timestamp makes it possible to find and remove duplicate or replayed data."
