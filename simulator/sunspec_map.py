"""Step 3 - Turn the inverter's measurements into SunSpec registers.

pv_model.Snapshot (engineering units: W, V, A, degC)
        |  choose a scale factor, divide, round to an integer
        v
list of 16-bit registers starting at 40000, ready for Modbus
"""

from __future__ import annotations

from datetime import datetime, timezone

from common.sunspec import (COMMON, CONTROLS, END_MODEL_ID, INVERTER_3PH, SIM_CLOCK, SIM_FAULT,
                            SUNS_MARKER, SUNSPEC_BASE, NOT_IMPL, FAULT_CODES, Model, encode_point)
from simulator.pv_model import Snapshot

# Scale factors this "inverter firmware" uses. Chosen so every value fits in 16 bits:
#   W up to 100 000 does NOT fit in int16 (max 32 767) -> W_SF = 1 (send 10 000 = 100 kW)
SCALE_FACTORS = {
    "A_SF": -1,        # 0.1 A resolution
    "V_SF": -1,        # 0.1 V
    "W_SF": 1,         # 10 W
    "Hz_SF": -2,       # 0.01 Hz
    "VA_SF": 1,
    "VAr_SF": 1,
    "PF_SF": -1,       # PF in percent, 0.1 % resolution
    "WH_SF": 0,        # 1 Wh
    "DCA_SF": -1,
    "DCV_SF": -1,
    "DCW_SF": 1,
    "Tmp_SF": -1,      # 0.1 degC
    "WMaxLimPct_SF": 0,  # 1 %
    "OutPFSet_SF": -3,
    "VArPct_SF": 0,
}

IDENTITY = {
    "Mn": "RES-Lab",
    "Md": "SIM-INV-100K",
    "Opt": "3PH",
    "Vr": "1.0.0",
    "SN": "GR-ATH-0001",
    "DA": 1,
    "Pad": 0,
}

# Where each model starts (address of its ID register). Computed once.
MODEL_ORDER = [COMMON, INVERTER_3PH, CONTROLS, SIM_CLOCK, SIM_FAULT]
MODEL_START: dict[int, int] = {}
_addr = SUNSPEC_BASE + 2
for _m in MODEL_ORDER:
    MODEL_START[_m.id] = _addr
    _addr += 2 + _m.length
END_ADDRESS = _addr                     # where 0xFFFF 0 goes
TOTAL_REGISTERS = END_ADDRESS + 2 - SUNSPEC_BASE


def _raw(value: float, sf: int) -> int:
    """Engineering value -> integer register value for a given scale factor."""
    return round(value / 10 ** sf)


def _fill_model(model: Model, values: dict) -> list[int]:
    """Build [ID, L, data...] for one model. Missing points get 'not implemented'."""
    data = [0] * model.length
    for p in model.points:
        if p.name in values:
            regs = encode_point(p, values[p.name])
        elif p.type == "string":
            regs = [0] * p.size
        elif p.size == 2:
            regs = [(NOT_IMPL[p.type] >> 16) & 0xFFFF, NOT_IMPL[p.type] & 0xFFFF]
        else:
            regs = [NOT_IMPL[p.type]]
        data[p.offset:p.offset + p.size] = regs
    return [model.id, model.length] + data


def inverter_values(s: Snapshot) -> dict:
    sf = SCALE_FACTORS
    v_ll = [s.v_phase[0] * 3 ** 0.5, s.v_phase[1] * 3 ** 0.5, s.v_phase[2] * 3 ** 0.5]
    return {
        "A": _raw(sum(s.a_phase), sf["A_SF"]),
        "AphA": _raw(s.a_phase[0], sf["A_SF"]),
        "AphB": _raw(s.a_phase[1], sf["A_SF"]),
        "AphC": _raw(s.a_phase[2], sf["A_SF"]),
        "A_SF": sf["A_SF"],
        "PPVphAB": _raw(v_ll[0], sf["V_SF"]),
        "PPVphBC": _raw(v_ll[1], sf["V_SF"]),
        "PPVphCA": _raw(v_ll[2], sf["V_SF"]),
        "PhVphA": _raw(s.v_phase[0], sf["V_SF"]),
        "PhVphB": _raw(s.v_phase[1], sf["V_SF"]),
        "PhVphC": _raw(s.v_phase[2], sf["V_SF"]),
        "V_SF": sf["V_SF"],
        "W": _raw(s.ac_w, sf["W_SF"]),
        "W_SF": sf["W_SF"],
        "Hz": _raw(s.hz, sf["Hz_SF"]),
        "Hz_SF": sf["Hz_SF"],
        "VA": _raw(s.ac_va, sf["VA_SF"]),
        "VA_SF": sf["VA_SF"],
        "VAr": _raw(s.ac_var, sf["VAr_SF"]),
        "VAr_SF": sf["VAr_SF"],
        "PF": _raw(s.pf * 100, sf["PF_SF"]),
        "PF_SF": sf["PF_SF"],
        "WH": _raw(s.energy_wh, sf["WH_SF"]),
        "WH_SF": sf["WH_SF"],
        "DCA": _raw(s.dc_a, sf["DCA_SF"]),
        "DCA_SF": sf["DCA_SF"],
        "DCV": _raw(s.dc_v, sf["DCV_SF"]),
        "DCV_SF": sf["DCV_SF"],
        "DCW": _raw(s.dc_w, sf["DCW_SF"]),
        "DCW_SF": sf["DCW_SF"],
        "TmpCab": _raw(s.t_cabinet, sf["Tmp_SF"]),
        "TmpSnk": _raw(s.t_heatsink, sf["Tmp_SF"]),
        "Tmp_SF": sf["Tmp_SF"],
        "St": s.state,
        "StVnd": s.state,
        "Evt1": s.events,          # Step 7: the inverter's own event flags
        "Evt2": 0,
    }


def control_values(limit_pct: float, limit_enabled: bool, connected: bool = True) -> dict:
    return {
        "Conn": 1 if connected else 0,
        "WMaxLimPct": _raw(limit_pct, SCALE_FACTORS["WMaxLimPct_SF"]),
        "WMaxLim_Ena": 1 if limit_enabled else 0,
        "WMaxLimPct_SF": SCALE_FACTORS["WMaxLimPct_SF"],
        "OutPFSet_SF": SCALE_FACTORS["OutPFSet_SF"],
        "VArPct_SF": SCALE_FACTORS["VArPct_SF"],
    }


def build_register_image(s: Snapshot, sim_time: datetime, speed: int) -> list[int]:
    """The complete SunSpec map from 40000 to the end marker."""
    regs = list(SUNS_MARKER)
    regs += _fill_model(COMMON, IDENTITY)
    regs += _fill_model(INVERTER_3PH, inverter_values(s))
    regs += _fill_model(CONTROLS, control_values(s.power_limit_pct, s.power_limit_enabled))
    regs += _fill_model(SIM_CLOCK, {"SimTime": int(sim_time.replace(tzinfo=timezone.utc).timestamp()), "Speed": speed, "Pad": 0})
    fault_code = {v: k for k, v in FAULT_CODES.items()}.get(s.fault, 0)
    regs += _fill_model(SIM_FAULT, {"TestFault": fault_code, "Pad": 0})
    regs += [END_MODEL_ID, 0]
    assert len(regs) == TOTAL_REGISTERS
    return regs


def control_address(point_name: str, model: Model = CONTROLS) -> int:
    """Absolute Modbus address of a point (default: model 123)."""
    return MODEL_START[model.id] + 2 + model.point(point_name).offset
