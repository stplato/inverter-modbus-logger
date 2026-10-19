"""Step 3 - SunSpec register map (shared by the simulator and the logger).

SunSpec is an open standard for how solar inverters lay out their Modbus registers,
so one SCADA driver can read SMA, Fronius, SolarEdge, Huawei, ... the same way.

Layout starting at holding register 40000 (FC03):

    40000-40001  "SunS"  marker that says "a SunSpec map starts here"
    then a chain of MODELS, each one:  [ID] [L = length] [L data registers]
        model 1     Common   (manufacturer, model, serial number)
        model 103   Three-phase inverter measurements
        model 123   Immediate controls (power limit = curtailment)
        model 64001 Vendor model: simulator clock (NOT standard, only for this project)
    0xFFFF 0     end of the chain

Every scaled value comes with a Scale Factor (SF) register:
    real value = raw x 10^SF           e.g. W = 9987, W_SF = 1   -> 99 870 W
"""

from __future__ import annotations

from dataclasses import dataclass

SUNSPEC_BASE = 40000
SUNS_MARKER = [0x5375, 0x6E53]        # ASCII "SunS"
END_MODEL_ID = 0xFFFF

# "Not implemented" values: a device fills unused registers with these
NOT_IMPL = {"uint16": 0xFFFF, "int16": 0x8000, "acc32": 0x0, "uint32": 0xFFFFFFFF,
            "sunssf": 0x8000, "enum16": 0xFFFF, "bitfield32": 0xFFFFFFFF, "string": 0}


@dataclass(frozen=True)
class Point:
    """One named value inside a model."""
    name: str
    offset: int            # registers after the model's ID+L header
    type: str              # uint16 | int16 | acc32 | uint32 | sunssf | enum16 | bitfield32 | string
    size: int = 1          # number of registers
    sf: str | None = None  # name of the scale-factor point that applies
    units: str = ""
    writable: bool = False


@dataclass(frozen=True)
class Model:
    id: int
    length: int
    name: str
    points: tuple[Point, ...]

    def point(self, name: str) -> Point:
        return next(p for p in self.points if p.name == name)


# --- Model 1: Common -------------------------------------------------------
COMMON = Model(1, 66, "Common", (
    Point("Mn", 0, "string", 16),          # manufacturer
    Point("Md", 16, "string", 16),         # model
    Point("Opt", 32, "string", 8),
    Point("Vr", 40, "string", 8),          # firmware version
    Point("SN", 48, "string", 16),         # serial number
    Point("DA", 64, "uint16"),             # Modbus device address
    Point("Pad", 65, "uint16"),
))

# --- Model 103: Three-phase inverter -----------------------------------------
INVERTER_3PH = Model(103, 50, "Inverter (Three Phase)", (
    Point("A", 0, "uint16", sf="A_SF", units="A"),
    Point("AphA", 1, "uint16", sf="A_SF", units="A"),
    Point("AphB", 2, "uint16", sf="A_SF", units="A"),
    Point("AphC", 3, "uint16", sf="A_SF", units="A"),
    Point("A_SF", 4, "sunssf"),
    Point("PPVphAB", 5, "uint16", sf="V_SF", units="V"),
    Point("PPVphBC", 6, "uint16", sf="V_SF", units="V"),
    Point("PPVphCA", 7, "uint16", sf="V_SF", units="V"),
    Point("PhVphA", 8, "uint16", sf="V_SF", units="V"),
    Point("PhVphB", 9, "uint16", sf="V_SF", units="V"),
    Point("PhVphC", 10, "uint16", sf="V_SF", units="V"),
    Point("V_SF", 11, "sunssf"),
    Point("W", 12, "int16", sf="W_SF", units="W"),
    Point("W_SF", 13, "sunssf"),
    Point("Hz", 14, "uint16", sf="Hz_SF", units="Hz"),
    Point("Hz_SF", 15, "sunssf"),
    Point("VA", 16, "int16", sf="VA_SF", units="VA"),
    Point("VA_SF", 17, "sunssf"),
    Point("VAr", 18, "int16", sf="VAr_SF", units="var"),
    Point("VAr_SF", 19, "sunssf"),
    Point("PF", 20, "int16", sf="PF_SF", units="%"),
    Point("PF_SF", 21, "sunssf"),
    Point("WH", 22, "acc32", 2, sf="WH_SF", units="Wh"),
    Point("WH_SF", 24, "sunssf"),
    Point("DCA", 25, "uint16", sf="DCA_SF", units="A"),
    Point("DCA_SF", 26, "sunssf"),
    Point("DCV", 27, "uint16", sf="DCV_SF", units="V"),
    Point("DCV_SF", 28, "sunssf"),
    Point("DCW", 29, "int16", sf="DCW_SF", units="W"),
    Point("DCW_SF", 30, "sunssf"),
    Point("TmpCab", 31, "int16", sf="Tmp_SF", units="C"),
    Point("TmpSnk", 32, "int16", sf="Tmp_SF", units="C"),
    Point("TmpTrns", 33, "int16", sf="Tmp_SF", units="C"),
    Point("TmpOt", 34, "int16", sf="Tmp_SF", units="C"),
    Point("Tmp_SF", 35, "sunssf"),
    Point("St", 36, "enum16"),             # operating state (see STATE_NAMES)
    Point("StVnd", 37, "enum16"),
    Point("Evt1", 38, "bitfield32", 2),    # event / alarm flags
    Point("Evt2", 40, "bitfield32", 2),
    Point("EvtVnd1", 42, "bitfield32", 2),
    Point("EvtVnd2", 44, "bitfield32", 2),
    Point("EvtVnd3", 46, "bitfield32", 2),
    Point("EvtVnd4", 48, "bitfield32", 2),
))

# --- Model 123: Immediate controls (only the points we use are writable) -----
CONTROLS = Model(123, 24, "Immediate Controls", (
    Point("Conn_WinTms", 0, "uint16", units="s"),
    Point("Conn_RvrtTms", 1, "uint16", units="s"),
    Point("Conn", 2, "enum16", writable=True),                         # 0 disconnect, 1 connect
    Point("WMaxLimPct", 3, "uint16", sf="WMaxLimPct_SF", units="%", writable=True),
    Point("WMaxLimPct_WinTms", 4, "uint16", units="s"),
    Point("WMaxLimPct_RvrtTms", 5, "uint16", units="s"),
    Point("WMaxLimPct_RmpTms", 6, "uint16", units="s"),
    Point("WMaxLim_Ena", 7, "enum16", writable=True),                  # 0 disabled, 1 enabled
    Point("OutPFSet", 8, "int16", sf="OutPFSet_SF"),
    Point("OutPFSet_WinTms", 9, "uint16"),
    Point("OutPFSet_RvrtTms", 10, "uint16"),
    Point("OutPFSet_RmpTms", 11, "uint16"),
    Point("OutPFSet_Ena", 12, "enum16"),
    Point("VArWMaxPct", 13, "int16", sf="VArPct_SF"),
    Point("VArMaxPct", 14, "int16", sf="VArPct_SF"),
    Point("VArAvalPct", 15, "int16", sf="VArPct_SF"),
    Point("VArPct_WinTms", 16, "uint16"),
    Point("VArPct_RvrtTms", 17, "uint16"),
    Point("VArPct_RmpTms", 18, "uint16"),
    Point("VArPct_Mod", 19, "enum16"),
    Point("VArPct_Ena", 20, "enum16"),
    Point("WMaxLimPct_SF", 21, "sunssf"),
    Point("OutPFSet_SF", 22, "sunssf"),
    Point("VArPct_SF", 23, "sunssf"),
))

# --- Model 64001: vendor model for THIS simulator (not part of SunSpec) ------
# Real inverters often add vendor models (IDs 64000+). Ours tells the logger the
# simulated clock, because the simulator runs a whole day in a few minutes.
SIM_CLOCK = Model(64001, 4, "Simulator Clock (vendor)", (
    Point("SimTime", 0, "uint32", 2, units="s"),   # unix time of the simulated local clock
    Point("Speed", 2, "uint16", units="x"),        # simulated seconds per real second
    Point("Pad", 3, "uint16"),
))

MODELS = {m.id: m for m in (COMMON, INVERTER_3PH, CONTROLS, SIM_CLOCK)}

STATE_NAMES = {1: "OFF", 2: "SLEEPING", 3: "STARTING", 4: "MPPT", 5: "THROTTLED",
               6: "SHUTTING_DOWN", 7: "FAULT", 8: "STANDBY"}


# ---------------------------------------------------------------------------
# Raw register <-> Python value conversion
# ---------------------------------------------------------------------------


def to_int16(raw: int) -> int:
    """Registers carry 0..65535. Signed values use two's complement: 0xFFFF = -1."""
    return raw - 0x10000 if raw >= 0x8000 else raw


def from_int16(value: int) -> int:
    if not -0x8000 <= value <= 0x7FFF:
        raise ValueError(f"{value} does not fit in int16")
    return value & 0xFFFF


def to_uint32(hi: int, lo: int) -> int:
    """32-bit values span two registers; SunSpec puts the high word first (big-endian)."""
    return (hi << 16) | lo


def from_uint32(value: int) -> list[int]:
    return [(value >> 16) & 0xFFFF, value & 0xFFFF]


def to_string(regs: list[int]) -> str:
    raw = b"".join(r.to_bytes(2, "big") for r in regs)
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")


def from_string(text: str, size: int) -> list[int]:
    raw = text.encode("ascii")[: size * 2].ljust(size * 2, b"\x00")
    return [int.from_bytes(raw[i:i + 2], "big") for i in range(0, len(raw), 2)]


def encode_point(point: Point, value) -> list[int]:
    """Python value (already as a raw integer, or a string) -> list of registers."""
    if point.type == "string":
        return from_string(value, point.size)
    if point.type in ("int16", "sunssf"):
        return [from_int16(int(value))]
    if point.type in ("acc32", "uint32", "bitfield32"):
        return from_uint32(int(value))
    return [int(value) & 0xFFFF]


def decode_point(point: Point, regs: list[int]):
    """List of registers -> raw Python value (None if 'not implemented')."""
    if point.type == "string":
        return to_string(regs)
    raw = to_uint32(regs[0], regs[1]) if point.size == 2 else regs[0]
    if point.type not in ("acc32",) and raw == NOT_IMPL[point.type]:
        return None
    if point.type in ("int16", "sunssf"):
        return to_int16(raw)
    return raw


def scale(value: int, sf: int) -> float:
    """Apply a SunSpec scale factor: value x 10^sf (rounded to remove float noise)."""
    return round(value * 10 ** sf, max(0, -sf))
