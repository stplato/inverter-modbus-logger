# Project 1 — Solar Inverter Simulator + Modbus Data Logger

> Portfolio project #1 of 5 · RES + IoT + Performance Engineering track
> Author: Stamatis Platokoukis · Built step by step with Claude as a pair programmer

## What this project does

A real PV plant is a set of field devices (inverters, meters, weather stations) that a SCADA system
polls over **Modbus TCP**. This project rebuilds that chain on one laptop:

```
 ┌──────────────────────────┐   Modbus TCP    ┌──────────────────────┐        ┌──────────────┐
 │  Inverter Simulator      │  (port 5020)    │  Data Logger         │  SQL   │  PostgreSQL  │
 │  - PV physics model      │ ◄────────────── │  - polls every 5 s   │ ─────► │  readings,   │
 │  - SunSpec register map  │   read regs     │  - decodes + scales  │        │  alarms      │
 │  - fault injection       │                 │  - detects alarms    │        │              │
 └──────────────────────────┘                 └──────────────────────┘        └──────────────┘
      "the field device"                        "the SCADA poller"              "the historian"
```

**Simulator** pretends to be a 100 kW PV inverter. It computes realistic values (AC/DC power,
voltage, current, frequency, temperature, daily energy) from a simulated sun curve and publishes
them in **SunSpec** registers, the standard map used by SMA, Fronius, SolarEdge, Huawei and others.

**Data Logger** behaves like the SCADA/EMS you worked with at Power Factors: it connects to the
inverter, reads the registers, applies the scale factors, checks for alarms, and stores everything
in a database.

## What I will be able to explain in an interview after this

- How Modbus works: holding vs input registers, function codes 03/04/06/16, 16-bit registers,
  32-bit values across two registers, byte/word order, unit IDs, timeouts.
- What SunSpec is and why scale factors exist (`value = raw × 10^SF`).
- How a SCADA poller is designed: polling interval, error handling, reconnection, data quality flags.
- How to model time-series measurement data in SQL.
- How to package a multi-service system with Docker Compose.

## Tech stack

| Area | Tool | Why |
|---|---|---|
| Language | Python 3.14 | Industry standard for energy data work |
| Modbus | `pymodbus` 3.16 (new `SimData`/`SimDevice` API) | The most used Python Modbus library |
| Database | PostgreSQL 16 | Same family as your Power Factors experience |
| DB access | `psycopg` 3 | Modern PostgreSQL driver |
| Testing | `pytest` | Proves the code works, interviewers look for it |
| Packaging | Docker + Docker Compose | Runs the whole system with one command |
| Version control | Git + GitHub | Your public portfolio |

## Folder structure

```
01-inverter-modbus-logger/
├── README.md                  ← this file (plan + progress tracker)
├── requirements.txt / .gitignore
├── step1_modbus_basics/       ← Step 1: 20-line server + client (learning sandbox)
├── common/
│   └── sunspec.py             ← SunSpec models 1, 103, 123 (+ vendor 64001), encode/decode helpers
├── simulator/                 ← THE FIELD DEVICE
│   ├── pv_model.py            ← Step 2: sun position → POA → cell temp → DC → AC, clipping
│   ├── sunspec_map.py         ← Step 3: measurements → SunSpec registers with scale factors
│   └── server.py              ← Step 4: live Modbus TCP server with a fast simulated clock
├── logger/                    ← THE SCADA
│   ├── decoder.py             ← Step 5: discover models, read blocks, apply scale factors
│   ├── poller.py              ← Step 5: poll loop, CSV storage, COMMS_LOST + reconnect
│   ├── db.py                  ← Step 6C: PostgreSQL historian, store-and-forward buffer
│   ├── alarms.py              ← Step 7B: alarm manager (device events, thresholds with delay + hysteresis, comms)
│   ├── set_limit.py           ← Step 5: send a curtailment command (model 123)
│   └── probe.py               ← "Modbus Poll" tool: send ONE read/write and see the bytes
├── tests/                     ← pytest unit tests (21 tests)
├── data/                      ← CSV output of the logger (not in Git)
└── docs/                      ← notes and learning log
```

Coming in Steps 6–10: `sql/schema.sql`, `logger/db.py`, `logger/alarms.py`, full `docker-compose.yml`, `grafana/` dashboards.

## Quick start: everything with one command (Step 8)

Needs only [Docker Desktop](https://www.docker.com/products/docker-desktop/):
```powershell
docker compose up -d --build        # build + start database, inverter simulator, logger and Grafana
# open http://localhost:3000          -> live dashboard (Step 10)
docker compose logs -f logger       # watch the SCADA logger live (Ctrl + C to stop watching)
python -m logger.set_limit 60       # commands from your PC still work (port 5020)
docker compose stop                 # stop everything; data is kept
```
Choose the simulated day: `$env:SIM_START="2026-04-17 05:00"; docker compose up -d --build`

## How to run the whole system (manually, for development)

Open **three** PowerShell windows in the project folder. In each, first run
`.\.venv\Scripts\Activate.ps1`.

**Window 1 — the inverter** (a whole day passes in ~15 minutes at 60x):
```powershell
python -m simulator.server
python -m simulator.server --start "2026-04-15 10:00" --speed 120    # spring day, clipping at noon
python -m simulator.server --weather cloudy                          # passing clouds
```

**Before starting:** the database must be running (Step 6):
```powershell
docker compose up -d
```

**Window 2 — the SCADA logger** (one line every 5 s → PostgreSQL + CSV backup):
```powershell
python -m logger.poller
```

**Window 3 — commands and checks:**
```powershell
python -m logger.set_limit 60        # curtail to 60 % -> logger shows THROTTLED, 60.0 kW
python -m logger.set_limit off       # remove the limit
python -m logger.set_limit 150       # invalid -> exception code 03
python -m simulator.inject grid_trip # Step 7: 51.6 Hz -> disconnect, reconnect delay, ramp
python -m simulator.inject fan_failure   # Step 7: heatsink overheats -> derating
python -m simulator.inject none      # clear the fan failure
python -m simulator.pv_model 2026-07-15   # print one full day from the physics model
python -m pytest                     # run the unit tests
python -m logger.probe read 40070 2  # one FC03 request, raw bytes shown
python -m logger.probe write 40084 5000   # try to write a read-only register
```

Try stopping Window 1 with Ctrl + C: the logger prints `COMMS_LOST`, retries with
1 s, 2 s, 4 s … waits, and reconnects by itself when you start the server again.

### SunSpec map of the simulator

| Address | Model | Content |
|---|---|---|
| 40000–40001 | — | `SunS` marker |
| 40002–40069 | 1 Common | Manufacturer `RES-Lab`, model `SIM-INV-100K`, SN `GR-ATH-0001` |
| 40070–40121 | 103 Three-phase inverter | A, V, W, Hz, VA, VAr, PF, WH (32-bit), DC A/V/W, temperatures, state |
| 40122–40147 | 123 Immediate controls | `WMaxLimPct` @ 40127, `WMaxLim_Ena` @ 40131 (writable) |
| 40148–40153 | 64001 Vendor | Simulated clock (not standard SunSpec) |
| 40154–40157 | 64002 Vendor | **Test** fault injection `TestFault` @ 40156 (Step 7, simulation only) |
| 40158–40159 | — | `0xFFFF 0` end marker |

## Time plan (8–10 h/week)

| Step | Topic | Hours | Status |
|---|---|---|---|
| 0 | Environment setup (Python, venv, Git, GitHub repo) | 2–3 | ✅ done |
| 1 | Modbus fundamentals: a 20-line server and client | 3–4 | ✅ done |
| 2 | PV + inverter physics model | 4–5 | ✅ walkthrough done |
| 3 | SunSpec register map | 4–5 | ✅ walkthrough done |
| 4 | Live Modbus TCP server | 3–4 | ✅ walkthrough done |
| 5 | Data logger: polling, decoding, error handling | 4–5 | ✅ walkthrough done |
| 6 | PostgreSQL schema + storing readings (Docker for the DB) | 5–6 | ✅ done |
| 7 | Alarms + fault injection (offline, over-temperature, grid trip) | 3–4 | ✅ done |
| 8 | Docker Compose: whole system with one command | 3–4 | ✅ done |
| 10 | **Grafana dashboard** on the PostgreSQL historian (done before Step 9) | 4–6 | hands-on |
| 9 | Final README with dashboard screenshots, GitHub public, CV line | 3–4 | last |

**Approach (chosen 19 Oct 2026, option A):** Steps 2–5 were built in one go so the whole system
can be seen working. Each file is then reviewed in its own session: explanation → a small change
with a prediction → checkpoint questions. Steps 6–8 are built hands-on. Nothing goes on the CV
until each file's checkpoints are passed.

## Progress tracker

- [x] **Step 0 — Environment setup** (venv, Git, GitHub: https://github.com/stplato/inverter-modbus-logger)
  - Checkpoint: a venv isolates each project's packages; `requirements.txt` makes it reproducible.
- [x] **Step 1 — Modbus fundamentals** (`step1_modbus_basics/`, notes in `docs/step1-modbus-notes.md`)
  - Checkpoints passed: holding vs input registers, FC03, scaling, exception 02, unit ID errors.
- [x] **Step 0b — See the whole system run** (3 windows above, curtailment, comms loss)
- [x] **Walkthrough 1 (Step 2) — `simulator/pv_model.py`**
  - [x] Exercise: change `tilt` to 10° and 60°, predict and compare daily energy in July and December
  - [x] Checkpoint: *Why does the inverter clip in April but not in July, although July has more sun?*
  - [x] Checkpoint: *What is POA, and why is it higher than GHI on a tilted panel in winter?*
  - [x] Checkpoint: *What does a DC/AC ratio of 1.2 mean and why do designers choose it?*
- [x] **Walkthrough 2 (Step 3) — `common/sunspec.py` + `simulator/sunspec_map.py`**
  - [x] Exercise: change `W_SF` to 0 and run at noon — what breaks and why?
  - [x] Checkpoint: *Why is the energy counter (WH) 32-bit and split over two registers?*
  - [x] Checkpoint: *How does a SCADA find model 103 without knowing its address in advance?*
- [x] **Walkthrough 3 (Step 4) — `simulator/server.py`**
  - [x] Exercise: run with `--trace` and decode one FC03 request by hand
  - [x] Checkpoint: *Why does the server reject a write to the W register?*
- [x] **Walkthrough 4 (Step 5) — `logger/`**
  - [x] Exercise: stop the server for 30 s; look at the CSV — what do the COMMS_LOST rows mean for KPIs?
  - [x] Checkpoint: *Why read a whole model in one request instead of one register at a time?*
  - [x] Checkpoint: *What is exponential backoff and why not retry every 0.1 s?*
  - [x] Checkpoint: *The logger once showed E_logged = -90 kWh. Why, and how does the fix handle a counter reset?*
- [x] **Step 6 — PostgreSQL** (hands-on, ~5–6 h)
  - [x] 6A: Docker Desktop installed, `docker compose up -d`, first SQL (CREATE / INSERT / SELECT / aggregates)
  - [x] 6B: design the tables (`devices`, `readings`, `alarms`) → `sql/schema.sql`
    - [x] Checkpoint: *Why does `readings` store only `device_id`, not the inverter's name and serial number?*
  - [x] 6C: logger writes every poll to PostgreSQL (`logger/db.py`, psycopg), store-and-forward + DB backoff
  - [x] 6D: KPI queries (`sql/kpi_queries.sql`): daily energy (LAG), specific yield, peak, state hours (LEAD), availability, hourly profile
- [x] **Step 7 — Alarms + fault injection** (hands-on)
  - [x] 7A: fault injection (`simulator/inject.py`: fan_failure, grid_trip), SunSpec `Evt1` events, derating, reconnect delay + ramp
    - [x] Exercise: SCADA missed the over-frequency between two polls → latched events / event logs / push protocols
  - [x] 7B: alarm manager (`logger/alarms.py`): raise/clear in `alarms` table, delay + hysteresis, COMMS_LOST alarm
    - [x] Checkpoint: *What happens with "alarm when T > 70, clear when T ≤ 70" if T hovers around 70 °C?*
- [x] **Step 8 — Docker Compose** (hands-on): `docker compose up -d --build` runs db + simulator + logger
- [ ] **Step 10 — Grafana dashboard** (hands-on, ~4–6 h; done before Step 9)
  - [ ] Grafana container in `docker-compose.yml`, PostgreSQL data source provisioned automatically
  - [ ] Panels: AC power / DC voltage / temperature over the day, inverter state (MPPT / THROTTLED / SLEEPING)
  - [ ] KPI cards: energy today, peak power, data availability %
  - [ ] Alarm table (from Step 7); clipping and curtailment visible on the power curve
  - [ ] Dashboard saved as JSON in `grafana/` so it's version-controlled
- [ ] **Step 9 — Finish and publish** (last)
  - [ ] README with architecture diagram and dashboard screenshots
  - [ ] Repo switched to public, CV "Projects" line written

## Troubleshooting notes

- **`(.venv)` shows but `python` is the global one:** Windows' App execution alias
  (`AppData\Local\Microsoft\WindowsApps\python.exe`) wins over the venv. Check with
  `Get-Command python` and `python -c "import sys; print(sys.prefix)"`. Fix: Settings →
  *Manage app execution aliases* → turn off `python.exe` and `python3.exe`.
- **`ModuleNotFoundError: No module named 'simulator'`:** run from the project folder and use
  `python -m simulator.server` (with `-m` and dots), not `python simulator/server.py`.
- **`Address already in use` / `WinError 10048`:** another server is still running on port 5020.
  Close the other window, or use `--port 5021` on both server and logger.
- **pymodbus 3.16:** the old `ModbusSlaveContext`/`ModbusDeviceContext` datastore is deprecated.
  This project uses the new `SimData` / `SimDevice` API so it keeps working with pymodbus 4.
