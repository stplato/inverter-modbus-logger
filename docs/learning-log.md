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
