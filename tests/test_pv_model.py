"""Physics sanity checks for the PV model. Run: python -m pytest"""

from datetime import datetime

from simulator.pv_model import (ST_MPPT, ST_SLEEPING, ST_THROTTLED, Inverter, PlantConfig,
                                dc_power, solar_position)

CFG = PlantConfig()


def test_sun_is_down_at_midnight_and_up_at_noon():
    el_night, _ = solar_position(datetime(2026, 6, 21, 21, 0), CFG.latitude, CFG.longitude)
    el_noon, az_noon = solar_position(datetime(2026, 6, 21, 10, 25), CFG.latitude, CFG.longitude)
    assert el_night < 0
    assert 74 < el_noon < 76          # 90 - 37.98 + 23.44 = 75.5 deg at the solstice
    assert 170 < az_noon < 190        # sun is in the South


def test_hot_cells_produce_less_power():
    assert dc_power(CFG, 1000, 60) < dc_power(CFG, 1000, 25)


def test_night_means_sleeping_and_zero_power():
    s = Inverter(seed=1).step(datetime(2026, 7, 15, 2, 0))
    assert s.state == ST_SLEEPING and s.ac_w == 0


def test_inverter_clips_at_rated_power_on_cool_bright_day():
    s = Inverter(seed=1).step(datetime(2026, 4, 15, 13, 30))
    assert s.state == ST_THROTTLED
    assert s.ac_w == CFG.p_ac_nom


def test_power_limit_curtails_output():
    inv = Inverter(seed=1, power_limit_pct=60, power_limit_enabled=True)
    s = inv.step(datetime(2026, 4, 15, 13, 30))
    assert s.ac_w == 60_000 and s.state == ST_THROTTLED


def test_energy_counter_only_goes_up():
    inv = Inverter(seed=1)
    start = inv.energy_wh
    for h in range(6, 21):
        inv.step(datetime(2026, 7, 15, h, 0))
    assert inv.energy_wh > start
    assert inv.step(datetime(2026, 7, 15, 12, 0)).state in (ST_MPPT, ST_THROTTLED)


# --- Step 7: faults --------------------------------------------------------------

from datetime import timedelta

from common.sunspec import decode_events
from simulator.pv_model import ST_FAULT, ST_STARTING


def _run(inv, start, minutes, inject_at=None, kind=None):
    states, t = [], start
    for i in range(minutes):
        if i == inject_at:
            inv.inject(kind, t)
        s = inv.step(t)
        states.append(s)
        t += timedelta(minutes=1)
    return states


def test_grid_trip_disconnects_waits_then_ramps():
    s = _run(Inverter(seed=1), datetime(2026, 4, 15, 12, 0), 25, inject_at=2, kind="grid_trip")
    assert s[2].state == ST_FAULT and s[2].ac_w == 0
    assert decode_events(s[2].events) == ["GRID_DISCONNECT", "OVER_FREQUENCY"]
    assert s[5].state == ST_STARTING                 # reconnect delay
    assert 0 < s[6].ac_w < 20_000                    # ramp starts low ...
    assert s[20].ac_w == 100_000                     # ... and reaches full power


def test_fan_failure_derates_power():
    s = _run(Inverter(seed=1), datetime(2026, 4, 15, 12, 0), 60, inject_at=0, kind="fan_failure")
    assert s[-1].t_heatsink > 69                     # hot heatsink
    assert s[-1].ac_w < 95_000                       # derated below the 100 kW clipping level
