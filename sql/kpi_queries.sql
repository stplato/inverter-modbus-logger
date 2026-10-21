-- Step 6D - Daily KPI queries for the PV historian
--
-- Run all of them (from the project folder, database running):
--   Get-Content sql\kpi_queries.sql | docker compose exec -T db psql -U solar -d scada
-- Or copy one query at a time into the psql console.

SET TIME ZONE 'Europe/Athens';     -- "a day" = an Athens calendar day

-- ---------------------------------------------------------------------------
-- KPI 1. Daily energy (kWh) - the simple way: last counter value - first counter value
-- ---------------------------------------------------------------------------
SELECT  date(time)                           AS day,
        max(energy_kwh) - min(energy_kwh)    AS energy_kwh_simple
FROM    readings
WHERE   quality = 'GOOD'
GROUP BY date(time)
ORDER BY day;

-- ---------------------------------------------------------------------------
-- KPI 2. Daily energy (kWh) - the robust way: add up only counter INCREASES.
--        LAG() looks at the previous row of the same device. A counter that goes
--        backwards (inverter reset/replaced) is ignored, like in logger/poller.py.
-- ---------------------------------------------------------------------------
WITH deltas AS (
    SELECT  time,
            energy_kwh - LAG(energy_kwh) OVER (PARTITION BY device_id ORDER BY time) AS delta
    FROM    readings
    WHERE   quality = 'GOOD'
)
SELECT  date(time)                                  AS day,
        round(sum(delta) FILTER (WHERE delta > 0), 1)  AS energy_kwh,
        round(sum(delta) FILTER (WHERE delta > 0) / 120, 2) AS specific_yield_kwh_per_kwp
FROM    deltas
GROUP BY date(time)
ORDER BY day;

-- ---------------------------------------------------------------------------
-- KPI 3. Peak AC power of the day, and when it happened
-- ---------------------------------------------------------------------------
SELECT  time, ac_kw, state
FROM    readings
WHERE   quality = 'GOOD'
ORDER BY ac_kw DESC, time
LIMIT 1;

-- ---------------------------------------------------------------------------
-- KPI 4. Time spent in each operating state (hours)
--        LEAD() gives the NEXT reading's time -> each row "lasts" until the next one.
-- ---------------------------------------------------------------------------
WITH periods AS (
    SELECT  state, limit_enabled,
            LEAD(time) OVER (PARTITION BY device_id ORDER BY time) - time AS duration
    FROM    readings
    WHERE   quality = 'GOOD'
)
SELECT  state,
        CASE WHEN limit_enabled THEN 'curtailed (limit on)' ELSE 'normal' END AS reason,
        round(extract(epoch FROM sum(duration)) / 3600, 2) AS hours
FROM    periods
WHERE   duration IS NOT NULL
GROUP BY state, limit_enabled
ORDER BY hours DESC;

-- ---------------------------------------------------------------------------
-- KPI 5. Data availability (%) - how many polls returned good data
-- ---------------------------------------------------------------------------
SELECT  count(*)                                         AS polls,
        count(*) FILTER (WHERE quality = 'GOOD')         AS good,
        count(*) FILTER (WHERE quality = 'COMMS_LOST')   AS comms_lost,
        round(100.0 * count(*) FILTER (WHERE quality = 'GOOD') / count(*), 1) AS availability_pct
FROM    readings;

-- ---------------------------------------------------------------------------
-- KPI 6. Hourly energy profile (kWh per hour) - the classic "bell curve" of a PV day
-- ---------------------------------------------------------------------------
WITH deltas AS (
    SELECT  time,
            energy_kwh - LAG(energy_kwh) OVER (PARTITION BY device_id ORDER BY time) AS delta
    FROM    readings
    WHERE   quality = 'GOOD'
)
SELECT  date_trunc('hour', time)                      AS hour,
        round(sum(delta) FILTER (WHERE delta > 0), 1) AS energy_kwh,
        repeat('#', (coalesce(sum(delta) FILTER (WHERE delta > 0), 0) / 4)::int) AS bar
FROM    deltas
GROUP BY 1
ORDER BY 1;
