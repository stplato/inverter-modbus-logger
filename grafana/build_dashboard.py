"""Builds grafana/dashboards/pv-plant.json (dashboard-as-code). Run: python grafana/build_dashboard.py"""
import json
import sys

DS = {"type": "grafana-postgresql-datasource", "uid": "pv-pg"}

Q_ENERGY = """WITH d AS (
  SELECT energy_kwh - LAG(energy_kwh) OVER (PARTITION BY device_id ORDER BY time) AS delta
  FROM readings WHERE $__timeFilter(time) AND quality = 'GOOD'
)
SELECT coalesce(sum(delta) FILTER (WHERE delta > 0), 0) AS "Energy" FROM d"""

Q_YIELD = """WITH d AS (
  SELECT energy_kwh - LAG(energy_kwh) OVER (PARTITION BY device_id ORDER BY time) AS delta
  FROM readings WHERE $__timeFilter(time) AND quality = 'GOOD'
)
SELECT coalesce(sum(delta) FILTER (WHERE delta > 0), 0) / 120.0 AS "Specific yield" FROM d"""

Q_PEAK = """SELECT max(ac_kw) AS "Peak power" FROM readings
WHERE $__timeFilter(time) AND quality = 'GOOD'"""

Q_AVAIL = """SELECT 100.0 * count(*) FILTER (WHERE quality = 'GOOD') / nullif(count(*), 0) AS "Data availability"
FROM readings WHERE $__timeFilter(time)"""

Q_STATE_NOW = """SELECT coalesce(state, 'COMMS_LOST') AS "State" FROM readings
WHERE $__timeFilter(time) ORDER BY time DESC LIMIT 1"""

Q_ACTIVE_ALARMS = """SELECT count(*) AS "Active alarms" FROM alarms WHERE cleared_at IS NULL"""

Q_POWER = """SELECT time AS "time",
       ac_kw AS "AC power",
       dc_kw AS "DC power",
       CASE WHEN limit_enabled THEN limit_pct ELSE 100 END AS "Power limit"
FROM readings
WHERE $__timeFilter(time) AND quality = 'GOOD'
ORDER BY 1"""

Q_TEMP = """SELECT time AS "time", t_heatsink_c AS "Heatsink", t_cabinet_c AS "Cabinet"
FROM readings
WHERE $__timeFilter(time) AND quality = 'GOOD'
ORDER BY 1"""

Q_STATE_TL = """SELECT time AS "time", coalesce(state, 'COMMS_LOST') AS "State"
FROM readings WHERE $__timeFilter(time) ORDER BY 1"""

Q_ALARMS = """SELECT a.started_at AS "Started", a.cleared_at AS "Cleared",
       d.name AS "Device", a.code AS "Alarm", a.severity AS "Severity",
       CASE WHEN a.cleared_at IS NULL THEN 'ACTIVE' ELSE 'cleared' END AS "Status",
       to_char(coalesce(a.cleared_at, now()) - a.started_at, 'HH24:MI:SS') AS "Duration",
       a.message AS "Message"
FROM alarms a JOIN devices d USING (device_id)
WHERE a.started_at <= $__timeTo() AND coalesce(a.cleared_at, now()) >= $__timeFrom()
ORDER BY a.started_at DESC"""

ALL_QUERIES = {"energy": Q_ENERGY, "yield": Q_YIELD, "peak": Q_PEAK, "avail": Q_AVAIL,
               "state_now": Q_STATE_NOW, "active_alarms": Q_ACTIVE_ALARMS, "power": Q_POWER,
               "temp": Q_TEMP, "state_tl": Q_STATE_TL, "alarms": Q_ALARMS}

STATE_COLORS = {"MPPT": "green", "THROTTLED": "yellow", "SLEEPING": "#6e7681",
                "STARTING": "blue", "FAULT": "red", "COMMS_LOST": "purple", "OFF": "#6e7681",
                "STANDBY": "blue"}


def target(sql, fmt):
    return [{"refId": "A", "datasource": DS, "editorMode": "code", "format": fmt,
             "rawQuery": True, "rawSql": sql}]


def thresholds(steps):
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for v, c in steps]}


def stat(pid, title, sql, unit, x, decimals=1, steps=None, desc="", text=False):
    p = {
        "id": pid, "type": "stat", "title": title, "description": desc, "datasource": DS,
        "gridPos": {"h": 4, "w": 4, "x": x, "y": 0},
        "targets": target(sql, "table"),
        "fieldConfig": {"defaults": {"unit": unit, "decimals": decimals,
                                     "thresholds": thresholds(steps or [(None, "blue")]),
                                     "color": {"mode": "thresholds"}}, "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "colorMode": "background" if steps else "value", "graphMode": "none",
                    "textMode": "value", "justifyMode": "center", "orientation": "auto"},
    }
    if text:
        p["options"]["reduceOptions"]["fields"] = "/^State$/"
        p["fieldConfig"]["defaults"]["mappings"] = [{
            "type": "value",
            "options": {k: {"color": v, "index": i} for i, (k, v) in enumerate(STATE_COLORS.items())}}]
        p["fieldConfig"]["defaults"]["color"] = {"mode": "fixed", "fixedColor": "text"}
        p["options"]["colorMode"] = "background"
    return p


def build():
    panels = [
        stat(1, "Energy (selected period)", Q_ENERGY, "kwatth", 0,
             desc="Sum of positive energy-counter differences (LAG) - robust to counter resets"),
        stat(2, "Specific yield", Q_YIELD, "none", 4, decimals=2,
             desc="kWh per kWp installed (120 kWp). Sanity check: > 8 per day is physically impossible"),
        stat(3, "Peak AC power", Q_PEAK, "kwatt", 8),
        stat(4, "Data availability", Q_AVAIL, "percent", 12,
             steps=[(None, "red"), (95, "orange"), (99, "green")],
             desc="Share of polls that returned GOOD data (not plant availability!)"),
        stat(5, "Inverter state (latest)", Q_STATE_NOW, "none", 16, text=True),
        stat(6, "Active alarms", Q_ACTIVE_ALARMS, "none", 20, decimals=0,
             steps=[(None, "green"), (1, "red")]),
        {
            "id": 7, "type": "timeseries", "title": "AC / DC power and power limit (kW)",
            "description": "Flat top at 100 kW = clipping. Flat top below 100 kW with the limit line = curtailment.",
            "datasource": DS, "gridPos": {"h": 10, "w": 16, "x": 0, "y": 4},
            "targets": target(Q_POWER, "time_series"),
            "fieldConfig": {"defaults": {"unit": "kwatt", "min": 0, "custom": {"lineWidth": 2, "fillOpacity": 10,
                                                                               "spanNulls": False}},
                            "overrides": [
                                {"matcher": {"id": "byName", "options": "AC power"},
                                 "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "green"}}]},
                                {"matcher": {"id": "byName", "options": "DC power"},
                                 "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "orange"}},
                                                {"id": "custom.fillOpacity", "value": 0}]},
                                {"matcher": {"id": "byName", "options": "Power limit"},
                                 "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}},
                                                {"id": "custom.fillOpacity", "value": 0},
                                                {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 10]}}]},
                            ]},
            "options": {"legend": {"displayMode": "list", "placement": "bottom"},
                        "tooltip": {"mode": "multi"}},
        },
        {
            "id": 8, "type": "timeseries", "title": "Temperatures (°C)",
            "description": "HIGH_TEMP alarm: raised above 70 °C for 3 polls, cleared below 65 °C (hysteresis)",
            "datasource": DS, "gridPos": {"h": 10, "w": 8, "x": 16, "y": 4},
            "targets": target(Q_TEMP, "time_series"),
            "fieldConfig": {"defaults": {"unit": "celsius", "custom": {"lineWidth": 2,
                                                                       "thresholdsStyle": {"mode": "line+area"}},
                                         "thresholds": thresholds([(None, "transparent"), (65, "#ff990022"),
                                                                   (70, "#ff000033")])},
                            "overrides": []},
            "options": {"legend": {"displayMode": "list", "placement": "bottom"},
                        "tooltip": {"mode": "multi"}},
        },
        {
            "id": 9, "type": "state-timeline", "title": "Inverter state over time",
            "datasource": DS, "gridPos": {"h": 4, "w": 24, "x": 0, "y": 14},
            "targets": target(Q_STATE_TL, "time_series"),
            "fieldConfig": {"defaults": {"color": {"mode": "fixed", "fixedColor": "text"},
                                         "mappings": [{"type": "value", "options": {
                                             k: {"color": v, "index": i}
                                             for i, (k, v) in enumerate(STATE_COLORS.items())}}],
                                         "custom": {"fillOpacity": 80, "lineWidth": 0}},
                            "overrides": []},
            "options": {"showValue": "auto", "mergeValues": True, "rowHeight": 0.9,
                        "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True}},
        },
        {
            "id": 10, "type": "table", "title": "Alarms",
            "datasource": DS, "gridPos": {"h": 9, "w": 24, "x": 0, "y": 18},
            "targets": target(Q_ALARMS, "table"),
            "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}}},
                            "overrides": [
                                {"matcher": {"id": "byName", "options": "Severity"},
                                 "properties": [{"id": "custom.cellOptions", "value": {"type": "color-background"}},
                                                {"id": "mappings", "value": [{"type": "value", "options": {
                                                    "CRITICAL": {"color": "red", "index": 0},
                                                    "WARNING": {"color": "orange", "index": 1},
                                                    "INFO": {"color": "blue", "index": 2}}}]}]},
                                {"matcher": {"id": "byName", "options": "Status"},
                                 "properties": [{"id": "custom.cellOptions", "value": {"type": "color-text"}},
                                                {"id": "mappings", "value": [{"type": "value", "options": {
                                                    "ACTIVE": {"color": "red", "index": 0},
                                                    "cleared": {"color": "green", "index": 1}}}]}]},
                            ]},
            "options": {"showHeader": True, "cellHeight": "sm"},
        },
    ]
    return {
        "uid": "pv-plant",
        "title": "PV Plant - Athens 100 kW (simulated)",
        "description": "Project 1 - SunSpec inverter simulator + Modbus logger + PostgreSQL historian",
        "tags": ["pv", "scada", "sunspec"],
        "timezone": "browser",
        "editable": True,
        "graphTooltip": 1,                       # shared crosshair across panels
        "refresh": "5s",
        # The simulator runs in SIMULATED time (April 2026), not real time -> fixed default range.
        # 2026-04-16 06:00-22:00 Athens = 03:00-19:00 UTC. Change with the time picker.
        "time": {"from": "2026-04-16T03:00:00.000Z", "to": "2026-04-16T19:00:00.000Z"},
        "timepicker": {"refresh_intervals": ["5s", "10s", "30s", "1m", "5m"]},
        "schemaVersion": 39,
        "version": 1,
        "panels": panels,
    }


if __name__ == "__main__":
    if sys.argv[1:] == ["--queries"]:
        print(json.dumps(ALL_QUERIES))
    else:
        out = str(__import__("pathlib").Path(__file__).parent / "dashboards" / "pv-plant.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(build(), f, indent=2, ensure_ascii=False)
        print("written", out)
