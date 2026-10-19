"""Step 2 - PV plant + inverter physics model.

A 120 kWp PV array on a 100 kW inverter in Athens (DC/AC ratio 1.2).
Given a moment in time, it calculates what a real inverter would measure.

    sun position -> irradiance on the panels (POA) -> cell temperature
    -> DC power -> inverter losses + clipping/curtailment -> AC power

Try it on its own:
    python -m simulator.pv_model            (prints a table for one day)
    python -m simulator.pv_model 2026-07-15
"""

from __future__ import annotations

import math
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Plant configuration (all the "datasheet" numbers in one place)
# ---------------------------------------------------------------------------


@dataclass
class PlantConfig:
    name: str = "Athens PV 100kW"
    latitude: float = 37.98          # deg North
    longitude: float = 23.73         # deg East
    tilt: float = 30.0               # panel tilt from horizontal [deg]
    azimuth: float = 180.0           # panel facing direction, 180 = South [deg]
    albedo: float = 0.2              # ground reflectance

    p_dc_stc: float = 120_000.0      # array peak power at STC [W]  (1000 W/m2, 25 degC)
    gamma_p: float = -0.0037         # power temperature coefficient [1/degC]  (-0.37 %/degC)
    noct: float = 45.0               # Nominal Operating Cell Temperature [degC]
    dc_losses: float = 0.03          # cables, mismatch, soiling [fraction]
    v_mp_stc: float = 750.0          # string MPP voltage at STC [V]
    beta_v: float = -0.0029          # voltage temperature coefficient [1/degC]

    p_ac_nom: float = 100_000.0      # inverter rated AC power [W]
    inv_self_consumption: float = 300.0   # fixed loss when running [W]
    inv_resistive_k: float = 1.7e-7       # loss that grows with P^2 [1/W]

    v_grid_phase: float = 230.0      # nominal phase voltage [V]
    f_grid: float = 50.0             # nominal grid frequency [Hz]


# SunSpec operating states (model 103, register "St")
ST_OFF, ST_SLEEPING, ST_STARTING, ST_MPPT, ST_THROTTLED = 1, 2, 3, 4, 5
ST_SHUTTING_DOWN, ST_FAULT, ST_STANDBY = 6, 7, 8
STATE_NAMES = {1: "OFF", 2: "SLEEPING", 3: "STARTING", 4: "MPPT", 5: "THROTTLED",
               6: "SHUTTING_DOWN", 7: "FAULT", 8: "STANDBY"}


# ---------------------------------------------------------------------------
# 0. Local time -> UTC (Greece: UTC+2 in winter, UTC+3 in summer)
# ---------------------------------------------------------------------------


def _last_sunday(year: int, month: int) -> datetime:
    d = datetime(year, month + 1, 1) - timedelta(days=1) if month < 12 else datetime(year, 12, 31)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def athens_utc_offset(t_local: datetime) -> int:
    """EU rule: summer time from last Sunday of March (03:00) to last Sunday of October (04:00)."""
    start = _last_sunday(t_local.year, 3).replace(hour=3)
    end = _last_sunday(t_local.year, 10).replace(hour=4)
    return 3 if start <= t_local.replace(tzinfo=None) < end else 2


# ---------------------------------------------------------------------------
# 1. Where is the sun?
# ---------------------------------------------------------------------------


def solar_position(t_utc: datetime, lat: float, lon: float) -> tuple[float, float]:
    """Return (elevation, azimuth) of the sun in degrees. Accuracy ~0.5 deg (NOAA method)."""
    doy = t_utc.timetuple().tm_yday
    hour = t_utc.hour + t_utc.minute / 60 + t_utc.second / 3600
    g = 2 * math.pi / 365 * (doy - 1 + (hour - 12) / 24)        # fractional year [rad]

    eq_time = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                        - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))  # [min]
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
            - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))              # [rad]

    true_solar_min = hour * 60 + eq_time + 4 * lon
    hour_angle = math.radians(true_solar_min / 4 - 180)

    lat_r = math.radians(lat)
    cos_zen = (math.sin(lat_r) * math.sin(decl)
               + math.cos(lat_r) * math.cos(decl) * math.cos(hour_angle))
    zen = math.acos(max(-1.0, min(1.0, cos_zen)))
    elevation = 90 - math.degrees(zen)

    # azimuth measured clockwise from North
    az = math.degrees(math.atan2(math.sin(hour_angle),
                                 math.cos(hour_angle) * math.sin(lat_r)
                                 - math.tan(decl) * math.cos(lat_r))) + 180
    return elevation, az % 360


# ---------------------------------------------------------------------------
# 2. How much sunlight reaches the panels?
# ---------------------------------------------------------------------------


def clear_sky_irradiance(elevation: float) -> tuple[float, float, float]:
    """Return (DNI, DHI, GHI) in W/m2 for a clear sky (Meinel air-mass model)."""
    if elevation <= 0:
        return 0.0, 0.0, 0.0
    zen = 90 - elevation
    air_mass = 1 / (math.cos(math.radians(zen)) + 0.50572 * (96.07995 - zen) ** -1.6364)
    dni = 1361 * 0.7 ** (air_mass ** 0.678)        # direct beam
    dhi = 0.1 * dni                                  # diffuse sky (rough clear-sky rule)
    ghi = dni * math.sin(math.radians(elevation)) + dhi
    return dni, dhi, ghi


def plane_of_array(cfg: PlantConfig, elevation: float, sun_az: float,
                   dni: float, dhi: float, ghi: float) -> float:
    """Irradiance on the tilted panel surface (POA) [W/m2]: beam + sky diffuse + ground."""
    if elevation <= 0:
        return 0.0
    el, tilt = math.radians(elevation), math.radians(cfg.tilt)
    cos_aoi = (math.sin(el) * math.cos(tilt)
               + math.cos(el) * math.sin(tilt) * math.cos(math.radians(sun_az - cfg.azimuth)))
    beam = dni * max(cos_aoi, 0.0)
    sky = dhi * (1 + math.cos(tilt)) / 2
    ground = ghi * cfg.albedo * (1 - math.cos(tilt)) / 2
    return beam + sky + ground


def ambient_temperature(t_local: datetime) -> float:
    """Simple Athens climate: seasonal mean + daily swing (min ~06:00, max ~15:00) [degC]."""
    doy = t_local.timetuple().tm_yday
    seasonal_mean = 18.5 + 9.5 * math.cos(2 * math.pi * (doy - 205) / 365)
    hour = t_local.hour + t_local.minute / 60
    daily = 5.0 * math.cos(2 * math.pi * (hour - 15) / 24)
    return seasonal_mean + daily


# ---------------------------------------------------------------------------
# 3. PV array and inverter
# ---------------------------------------------------------------------------


def cell_temperature(cfg: PlantConfig, poa: float, t_amb: float) -> float:
    """NOCT model: cells heat up ~ (NOCT-20) degC above ambient at 800 W/m2."""
    return t_amb + poa / 800 * (cfg.noct - 20)


def dc_power(cfg: PlantConfig, poa: float, t_cell: float) -> float:
    """Available DC power at the maximum power point [W]."""
    p = cfg.p_dc_stc * (poa / 1000) * (1 + cfg.gamma_p * (t_cell - 25))
    return max(0.0, p * (1 - cfg.dc_losses))


def dc_voltage(cfg: PlantConfig, poa: float, t_cell: float) -> float:
    """MPP voltage: falls with temperature, slightly with low light [V]."""
    if poa < 5:
        return 0.0
    low_light = 1 + 0.03 * math.log(poa / 1000)
    return cfg.v_mp_stc * (1 + cfg.beta_v * (t_cell - 25)) * low_light


def inverter_losses(cfg: PlantConfig, p_dc: float) -> float:
    """Fixed self-consumption + resistive losses that grow with power squared [W]."""
    return cfg.inv_self_consumption + cfg.inv_resistive_k * p_dc ** 2


# ---------------------------------------------------------------------------
# 4. The inverter as a device with state (what the Modbus server exposes)
# ---------------------------------------------------------------------------


@dataclass
class Snapshot:
    """Everything the inverter 'measures' at one moment, in engineering units."""
    time_local: datetime
    sun_elevation: float = 0.0
    poa: float = 0.0               # W/m2
    t_amb: float = 0.0             # degC
    t_cell: float = 0.0            # degC
    p_dc_available: float = 0.0    # W (what the panels could give)
    dc_w: float = 0.0
    dc_v: float = 0.0
    dc_a: float = 0.0
    ac_w: float = 0.0
    ac_va: float = 0.0
    ac_var: float = 0.0
    pf: float = 1.0
    v_phase: tuple[float, float, float] = (0.0, 0.0, 0.0)
    a_phase: tuple[float, float, float] = (0.0, 0.0, 0.0)
    hz: float = 0.0
    energy_wh: float = 0.0
    t_heatsink: float = 0.0
    t_cabinet: float = 0.0
    state: int = ST_SLEEPING
    power_limit_pct: float = 100.0
    power_limit_enabled: bool = False


@dataclass
class Inverter:
    cfg: PlantConfig = field(default_factory=PlantConfig)
    weather: str = "clear"              # "clear" or "cloudy"
    seed: int | None = None
    energy_wh: float = 152_340_000.0    # lifetime counter (152 MWh already produced)
    power_limit_pct: float = 100.0      # curtailment setpoint (SunSpec WMaxLimPct)
    power_limit_enabled: bool = False   # SunSpec WMaxLim_Ena
    _last_time: datetime | None = None
    _last_w: float | None = None
    _cloud: float = 1.0

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)

    def _cloud_factor(self, dt_s: float) -> float:
        """Random-walk cloud cover: 1.0 = clear, 0.2 = heavy cloud."""
        if self.weather != "cloudy":
            return 1.0
        steps = max(1, int(dt_s / 60))
        for _ in range(steps):
            self._cloud += self.rng.gauss(0, 0.08)
            self._cloud += (0.75 - self._cloud) * 0.05      # drift back to ~0.75
            self._cloud = min(1.0, max(0.2, self._cloud))
        return self._cloud

    def step(self, t_local: datetime) -> Snapshot:
        """Advance the inverter to time t_local and return its measurements."""
        cfg = self.cfg
        dt_s = 0.0 if self._last_time is None else (t_local - self._last_time).total_seconds()
        t_utc = (t_local - timedelta(hours=athens_utc_offset(t_local))).replace(tzinfo=timezone.utc)

        elevation, sun_az = solar_position(t_utc, cfg.latitude, cfg.longitude)
        dni, dhi, ghi = clear_sky_irradiance(elevation)
        cloud = self._cloud_factor(dt_s)
        dni, dhi, ghi = dni * cloud ** 2, dhi * (2 - cloud), ghi * cloud  # clouds kill beam, add diffuse
        poa = plane_of_array(cfg, elevation, sun_az, dni, dhi, ghi)

        t_amb = ambient_temperature(t_local)
        t_cell = cell_temperature(cfg, poa, t_amb)
        p_dc_avail = dc_power(cfg, poa, t_cell)

        snap = Snapshot(time_local=t_local, sun_elevation=elevation, poa=poa,
                        t_amb=t_amb, t_cell=t_cell, p_dc_available=p_dc_avail,
                        power_limit_pct=self.power_limit_pct,
                        power_limit_enabled=self.power_limit_enabled)

        # --- inverter decision: sleep, MPPT, or throttled ------------------
        limit_w = cfg.p_ac_nom
        if self.power_limit_enabled:
            limit_w = cfg.p_ac_nom * self.power_limit_pct / 100

        if p_dc_avail < 2 * cfg.inv_self_consumption:
            snap.state = ST_SLEEPING
            p_ac = 0.0
            p_dc = 0.0
        else:
            p_ac_avail = p_dc_avail - inverter_losses(cfg, p_dc_avail)
            if p_ac_avail > limit_w:
                snap.state = ST_THROTTLED          # clipping or curtailment
                p_ac = limit_w
                p_dc = p_ac + inverter_losses(cfg, p_ac)   # inverter moves off the MPP
            else:
                snap.state = ST_MPPT
                p_ac = p_ac_avail
                p_dc = p_dc_avail

        # --- electrical values with a little measurement noise -------------
        n = self.rng.gauss
        snap.hz = cfg.f_grid + n(0, 0.01)
        snap.v_phase = tuple(cfg.v_grid_phase + n(0, 0.8) for _ in range(3))
        snap.pf = 1.0
        snap.ac_w = p_ac
        snap.ac_va = p_ac / snap.pf
        snap.ac_var = math.sqrt(max(snap.ac_va ** 2 - p_ac ** 2, 0))
        snap.a_phase = tuple(p_ac / 3 / v for v in snap.v_phase)
        snap.dc_w = p_dc
        snap.dc_v = dc_voltage(cfg, poa, t_cell) if p_dc > 0 else 0.0
        snap.dc_a = p_dc / snap.dc_v if snap.dc_v > 0 else 0.0

        # --- temperatures and energy counter --------------------------------
        load = p_ac / cfg.p_ac_nom
        snap.t_heatsink = t_amb + 28 * load + n(0, 0.2)
        snap.t_cabinet = t_amb + 9 * load + n(0, 0.1)
        if dt_s > 0 and self._last_w is not None:
            self.energy_wh += (self._last_w + p_ac) / 2 * dt_s / 3600   # trapezoid
        snap.energy_wh = self.energy_wh

        self._last_time = t_local
        self._last_w = p_ac
        return snap


# ---------------------------------------------------------------------------
# Run this file directly to see one simulated day
# ---------------------------------------------------------------------------


def _print_day(date_str: str | None = None) -> None:
    day = datetime.fromisoformat(date_str) if date_str else datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0)
    inv = Inverter(seed=1)
    start_wh = None
    print(f"{inv.cfg.name} - {day:%Y-%m-%d}  (120 kWp DC / 100 kW AC)\n")
    print(" time   sun_el  POA    T_amb  T_cell   P_dc_avail   P_ac      state")
    print("        [deg]  [W/m2]  [C]    [C]      [kW]         [kW]")
    for minutes in range(0, 24 * 60 + 1, 30):
        t = day + timedelta(minutes=minutes)
        s = inv.step(t)
        start_wh = s.energy_wh if start_wh is None else start_wh
        if s.sun_elevation > -5 or minutes % 180 == 0:
            print(f" {t:%H:%M}  {s.sun_elevation:6.1f}  {s.poa:5.0f}  {s.t_amb:5.1f}  {s.t_cell:5.1f}"
                  f"   {s.p_dc_available / 1000:8.1f}   {s.ac_w / 1000:8.1f}    {STATE_NAMES[s.state]}")
    print(f"\nDaily energy: {(inv.energy_wh - start_wh) / 1000:.0f} kWh"
          f"  (specific yield {(inv.energy_wh - start_wh) / inv.cfg.p_dc_stc:.2f} kWh/kWp)")


if __name__ == "__main__":
    _print_day(sys.argv[1] if len(sys.argv) > 1 else None)
