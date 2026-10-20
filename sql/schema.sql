-- Step 6B - PV SCADA historian schema (PostgreSQL 16)
--
-- Load it (from the project folder, with the database running):
--   Get-Content sql\schema.sql | docker compose exec -T db psql -U solar -d scada
-- Safe to run again: it only creates what doesn't exist yet.

-- ---------------------------------------------------------------------------
-- 1. devices: ONE row per physical device (normalization: details stored once)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS devices (
    device_id      serial PRIMARY KEY,            -- the number every reading points to
    name           text        NOT NULL,          -- human name, e.g. 'INV-01'
    manufacturer   text,
    model          text,
    serial_number  text        NOT NULL UNIQUE,   -- a replacement inverter = a NEW row
    firmware       text,
    rated_kw       real,
    host           text        NOT NULL,          -- where the SCADA reaches it
    port           integer     NOT NULL,
    unit_id        integer     NOT NULL,          -- Modbus device id
    installed_at   timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 2. readings: ONE row per device per poll (the big time-series table)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS readings (
    time            timestamptz NOT NULL,         -- device time (simulated clock)
    device_id       integer     NOT NULL REFERENCES devices (device_id),  -- foreign key
    logged_at       timestamptz NOT NULL DEFAULT now(),                   -- logger's own clock
    quality         text        NOT NULL CHECK (quality IN ('GOOD', 'COMMS_LOST')),
    state           text,                         -- MPPT, THROTTLED, SLEEPING, ...
    ac_kw           real,                         -- measurements: real is precise enough
    dc_kw           real,
    dc_v            real,
    dc_a            real,
    v_phase_a       real,
    hz              real,
    pf_pct          real,
    t_heatsink_c    real,
    t_cabinet_c     real,
    energy_kwh      numeric(14, 3),               -- counter: exact decimals, never float
    limit_pct       real,
    limit_enabled   boolean,
    PRIMARY KEY (device_id, time)                 -- one reading per device per instant
);

-- Most queries ask "this device, this time range" -> the primary key index covers it.
-- This extra index speeds up "all devices, last hour" queries (dashboards).
CREATE INDEX IF NOT EXISTS readings_time_idx ON readings (time DESC);

-- ---------------------------------------------------------------------------
-- 3. alarms: ONE row per alarm event (filled in Step 7)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alarms (
    alarm_id     bigserial   PRIMARY KEY,
    device_id    integer     NOT NULL REFERENCES devices (device_id),
    code         text        NOT NULL,            -- e.g. 'COMMS_LOST', 'OVER_TEMP'
    severity     text        NOT NULL CHECK (severity IN ('INFO', 'WARNING', 'CRITICAL')),
    message      text,
    started_at   timestamptz NOT NULL,
    cleared_at   timestamptz                      -- NULL while the alarm is still active
);

CREATE INDEX IF NOT EXISTS alarms_active_idx ON alarms (device_id) WHERE cleared_at IS NULL;
