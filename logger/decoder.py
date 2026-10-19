"""Step 5 - Read and decode SunSpec models from any SunSpec device.

This is what a real SCADA driver does when it meets a new inverter:
  1. DISCOVER  - check for "SunS" at 40000, then walk the chain of models
  2. READ      - read a whole model in ONE Modbus request (fast, consistent)
  3. DECODE    - turn raw registers into engineering values using scale factors
"""

from __future__ import annotations

from pymodbus.client import ModbusTcpClient

from common.sunspec import (END_MODEL_ID, MODELS, SUNS_MARKER, SUNSPEC_BASE, Model,
                            decode_point, scale)

MAX_REGS_PER_READ = 125     # Modbus limit for FC03


class ModbusReadError(Exception):
    """Device answered with an exception code, or did not answer at all."""


def read_holding(client: ModbusTcpClient, address: int, count: int, device_id: int) -> list[int]:
    rr = client.read_holding_registers(address, count=count, device_id=device_id)
    if rr.isError():
        raise ModbusReadError(f"FC03 at {address} x{count} failed: {rr}")
    return rr.registers


def discover(client: ModbusTcpClient, device_id: int = 1) -> dict[int, tuple[int, int]]:
    """Return {model_id: (start_address, length)} for every model the device has."""
    if read_holding(client, SUNSPEC_BASE, 2, device_id) != SUNS_MARKER:
        raise ModbusReadError(f"No 'SunS' marker at {SUNSPEC_BASE}: not a SunSpec device?")
    found: dict[int, tuple[int, int]] = {}
    addr = SUNSPEC_BASE + 2
    for _ in range(50):                                   # safety limit
        model_id, length = read_holding(client, addr, 2, device_id)
        if model_id == END_MODEL_ID:
            return found
        found[model_id] = (addr, length)
        addr += 2 + length
    raise ModbusReadError("SunSpec chain has no end marker")


def read_model(client: ModbusTcpClient, start: int, length: int, device_id: int = 1) -> list[int]:
    """Read [ID, L, data...] of one model (split into several reads if > 125 registers)."""
    regs: list[int] = []
    total = 2 + length
    while len(regs) < total:
        n = min(MAX_REGS_PER_READ, total - len(regs))
        regs += read_holding(client, start + len(regs), n, device_id)
    return regs


def decode_model(model: Model, regs: list[int]) -> dict[str, object]:
    """Raw registers (including the ID+L header) -> {point_name: value in real units}.

    Values the device marks as 'not implemented' come back as None.
    """
    if regs[0] != model.id:
        raise ValueError(f"expected model {model.id}, got {regs[0]}")
    data = regs[2:]
    raw = {p.name: decode_point(p, data[p.offset:p.offset + p.size]) for p in model.points}
    out: dict[str, object] = {}
    for p in model.points:
        value = raw[p.name]
        if p.type == "sunssf":
            continue                                   # used for scaling, not reported
        if p.sf and value is not None:
            sf = raw.get(p.sf)
            value = None if sf is None else scale(value, sf)
        out[p.name] = value
    return out


def model_by_id(model_id: int) -> Model | None:
    return MODELS.get(model_id)
