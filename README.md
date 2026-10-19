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
| Modbus | `pymodbus` 3.x | The most used Python Modbus library |
| Database | PostgreSQL 16 | Same family as your Power Factors experience |
| DB access | `psycopg` 3 | Modern PostgreSQL driver |
| Testing | `pytest` | Proves the code works, interviewers look for it |
| Packaging | Docker + Docker Compose | Runs the whole system with one command |
| Version control | Git + GitHub | Your public portfolio |

## Planned folder structure

```
01-inverter-modbus-logger/
├── README.md               ← this file (the plan + progress tracker)
├── requirements.txt
├── .gitignore
├── simulator/
│   ├── pv_model.py         ← sun curve + inverter physics
│   ├── sunspec_map.py      ← register addresses, scale factors
│   └── server.py           ← Modbus TCP server
├── logger/
│   ├── decoder.py          ← raw registers → engineering values
│   ├── poller.py           ← Modbus client loop
│   ├── alarms.py           ← alarm rules
│   └── db.py               ← PostgreSQL writes
├── sql/
│   └── schema.sql
├── tests/
├── docker-compose.yml
└── docs/                   ← notes, screenshots, interview answers
```

## Time plan (8–10 h/week → about 5 weeks)

| Step | Topic | Hours | Week |
|---|---|---|---|
| 0 | Environment setup (Python, venv, Git, GitHub repo) | 2–3 | 1 |
| 1 | Modbus fundamentals: a 20-line server and client | 3–4 | 1 |
| 2 | PV + inverter physics model | 4–5 | 1–2 |
| 3 | SunSpec register map (models 1 and 103) | 4–5 | 2 |
| 4 | Modbus TCP server exposing the simulated inverter | 3–4 | 2–3 |
| 5 | Data logger: polling, decoding, error handling | 4–5 | 3 |
| 6 | PostgreSQL schema + storing readings (Docker for the DB) | 5–6 | 3–4 |
| 7 | Alarms + fault injection (offline, over-temperature, grid trip) | 3–4 | 4 |
| 8 | Docker Compose: whole system with one command | 3–4 | 4–5 |
| 9 | Tests, final README, GitHub publish, CV line | 3–4 | 5 |
| | **Total** | **~35–45 h** | |

## How each step works

1. Claude explains the concept briefly (5–10 min of reading).
2. Claude writes or updates the code in this folder.
3. **You run it** on your PC and paste the output back.
4. **Checkpoint:** you explain in your own words what the code does, and answer 1–2 questions.
   Your answers go in `docs/learning-log.md`. This becomes your interview preparation.
5. We tick the step below and commit to Git.

## Progress tracker

- [x] **Step 0 — Environment setup**
  - [x] Python 3.14 installed (`python --version`)
  - [x] Git installed (`git --version`)
  - [x] VS Code installed with the Python extension
  - [x] Virtual environment created and activated
  - [x] `pymodbus` and `pytest` installed
  - [x] GitHub repo created: https://github.com/stplato/inverter-modbus-logger
  - [x] Checkpoint: *What is a virtual environment and why do we use one per project?*
    → It isolates each project's packages so versions don't conflict; `requirements.txt` makes it reproducible.
  - [x] Windows fix: disable the `python.exe` / `python3.exe` App execution aliases so `python` resolves to the venv
  - [x] Git configured, repo initialised, first commit
- [ ] **Step 1 — Modbus fundamentals**
  - [ ] Minimal server holding 10 registers (`step1_modbus_basics/server.py`)
  - [ ] Minimal client reading and writing them (`step1_modbus_basics/client.py`)
  - [ ] Read the raw frames in `docs/step1-modbus-notes.md`
  - [ ] Checkpoint: *What is the difference between a holding and an input register? What does function code 03 do?*
- [ ] **Step 2 — PV + inverter model**
  - [ ] Clear-sky irradiance curve for a site in Greece (sunrise → noon → sunset)
  - [ ] DC power from irradiance and module temperature
  - [ ] AC power through an inverter efficiency curve, with clipping at 100 kW
  - [ ] Checkpoint: *Why does an inverter clip, and why does power drop when modules are hot?*
- [ ] **Step 3 — SunSpec register map**
  - [ ] Model 1 (Common: manufacturer, model, serial)
  - [ ] Model 103 (Three-phase inverter: W, A, V, Hz, WH, temperature, status)
  - [ ] Scale factors encoded correctly
  - [ ] Checkpoint: *Why does SunSpec store `W = 12345` and `W_SF = -1` instead of 1234.5?*
- [ ] **Step 4 — Simulator server**
  - [ ] Registers update every second from the PV model
  - [ ] Simulated clock (run one day in a few minutes for testing)
- [ ] **Step 5 — Data logger**
  - [ ] Poll every 5 s, decode with scale factors
  - [ ] Handle timeouts and reconnect automatically
  - [ ] Data quality flag (good / stale / comms-lost)
- [ ] **Step 6 — PostgreSQL**
  - [ ] Docker Desktop installed, PostgreSQL running in a container
  - [ ] `schema.sql`: `devices`, `readings`, `alarms` tables
  - [ ] Logger writes readings; first SQL queries (daily energy, peak power)
- [ ] **Step 7 — Alarms + faults**
  - [ ] Simulator can inject faults on command
  - [ ] Logger raises and clears alarms, stores them with timestamps
- [ ] **Step 8 — Docker Compose**
  - [ ] `docker compose up` starts simulator, logger and database together
- [ ] **Step 9 — Finish and publish**
  - [ ] Unit tests for decoder and PV model
  - [ ] Final README with architecture diagram and screenshots
  - [ ] Pushed to GitHub, CV "Projects" line written

## Troubleshooting notes

- **`(.venv)` shows but `python` is the global one:** Windows' App execution alias
  (`AppData\Local\Microsoft\WindowsApps\python.exe`) wins over the venv. Check with
  `Get-Command python` and `python -c "import sys; print(sys.prefix)"`. Fix: Settings →
  *Manage app execution aliases* → turn off `python.exe` and `python3.exe`. Fallback: run
  `.\.venv\Scripts\python.exe` directly.

## Step 1 — how to run

Open **two** PowerShell windows in the project folder and activate the venv in both
(`.\.venv\Scripts\Activate.ps1`).

Window 1 (the device):
```powershell
python step1_modbus_basics/server.py
```

Window 2 (the SCADA):
```powershell
python step1_modbus_basics/client.py
```

Window 1 prints the raw bytes of every request and response. `docs/step1-modbus-notes.md`
explains them byte by byte.

> pymodbus 3.16 note: the old `ModbusSlaveContext`/`ModbusDeviceContext` datastore is deprecated.
> This project uses the new `SimData` / `SimDevice` API so it keeps working with pymodbus 4.
