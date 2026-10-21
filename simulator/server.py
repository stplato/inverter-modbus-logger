"""Step 4 - The simulated inverter as a live Modbus TCP server.

Every real second the simulated clock moves forward by --speed seconds, the PV
model is recalculated, and the SunSpec registers are refreshed. A SCADA (our
logger) can read them with FC03 and write the power limit with FC06/FC16.

Run from the project folder:
    python -m simulator.server                       (today, from 06:00, 60x speed)
    python -m simulator.server --start "2026-04-15 10:00" --speed 120
    python -m simulator.server --weather cloudy --trace
Stop with Ctrl + C.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timedelta

from pymodbus.constants import ExcCodes
from pymodbus.server import ModbusTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice

from common.sunspec import CONTROLS, FAULT_CODES, SIM_FAULT, SUNSPEC_BASE, STATE_NAMES, decode_events, to_int16
from simulator.pv_model import Inverter
from simulator.sunspec_map import (MODEL_START, TOTAL_REGISTERS, build_register_image,
                                   control_address)

DEVICE_ID = 1


class InverterDevice:
    """Glue between the PV model (physics) and the Modbus register table."""

    def __init__(self, start: datetime, speed: int, weather: str, seed: int | None):
        self.sim_time = start
        self.speed = speed
        self.inverter = Inverter(weather=weather, seed=seed)
        self.snapshot = self.inverter.step(self.sim_time)
        self.image = build_register_image(self.snapshot, self.sim_time, self.speed)
        self.connected = True

        # addresses of the 3 writable control registers
        self.addr_conn = control_address("Conn")
        self.addr_limit = control_address("WMaxLimPct")
        self.addr_enable = control_address("WMaxLim_Ena")
        self.addr_fault = control_address("TestFault", SIM_FAULT)      # Step 7 (test only)
        self.writable = {self.addr_conn, self.addr_limit, self.addr_enable, self.addr_fault}

    # --- the clock ------------------------------------------------------
    async def run_clock(self) -> None:
        tick = 0
        while True:
            await asyncio.sleep(1)
            self.sim_time += timedelta(seconds=self.speed)
            self.snapshot = self.inverter.step(self.sim_time)
            self.image = build_register_image(self.snapshot, self.sim_time, self.speed)
            tick += 1
            if tick % 10 == 1 or self.snapshot.events or self.snapshot.fault != "none":
                self.print_status()

    def print_status(self) -> None:
        s = self.snapshot
        limit = f"{s.power_limit_pct:.0f}%" if s.power_limit_enabled else "off"
        events = ",".join(decode_events(s.events)) or "-"
        fault = f"   [TEST FAULT: {s.fault}]" if s.fault != "none" else ""
        print(f"[{self.sim_time:%Y-%m-%d %H:%M}]  POA {s.poa:5.0f} W/m2   "
              f"P_ac {s.ac_w / 1000:6.1f} kW   T_snk {s.t_heatsink:4.1f} C   Hz {s.hz:5.2f}   "
              f"limit {limit:>4}   {STATE_NAMES[s.state]:9} events {events}{fault}")

    # --- called by pymodbus on every request ------------------------------
    async def on_request(self, function_code, start_address, address, count,
                         registers, set_values):
        """pymodbus 'action': runs before every read or write."""
        if set_values is None:
            # READ: copy the latest measurements into the live register table
            registers[0:len(self.image)] = self.image
            return None

        # WRITE: only the control registers of model 123 may be written
        targets = range(address, address + len(set_values))
        if any(a not in self.writable for a in targets):
            print(f"  ! rejected write to {address}..{address + len(set_values) - 1} (read-only)")
            return ExcCodes.ILLEGAL_ADDRESS
        for a, v in zip(targets, set_values):
            if a == self.addr_limit:
                if not 0 <= v <= 100:
                    print(f"  ! rejected WMaxLimPct = {to_int16(v)} (must be 0..100)")
                    return ExcCodes.ILLEGAL_VALUE
                self.inverter.power_limit_pct = float(v)
            elif a == self.addr_enable:
                self.inverter.power_limit_enabled = bool(v)
            elif a == self.addr_conn:
                self.connected = bool(v)
            elif a == self.addr_fault:
                if v not in FAULT_CODES:
                    return ExcCodes.ILLEGAL_VALUE
                self.inverter.inject(FAULT_CODES[v], self.sim_time)
                print(f"  >>> TEST FAULT injected: {FAULT_CODES[v]}")
                self.snapshot.fault = self.inverter.fault
                self.image = build_register_image(self.snapshot, self.sim_time, self.speed)
                return None
        # refresh the register image now, so a read-back shows the new setpoint
        self.snapshot.power_limit_pct = self.inverter.power_limit_pct
        self.snapshot.power_limit_enabled = self.inverter.power_limit_enabled
        self.snapshot.fault = self.inverter.fault
        self.image = build_register_image(self.snapshot, self.sim_time, self.speed)
        print(f"  > SCADA wrote {list(set_values)} at {address}: power limit "
              f"{self.inverter.power_limit_pct:.0f}% "
              f"{'ENABLED' if self.inverter.power_limit_enabled else 'disabled'}")
        return None


def show_traffic(sending: bool, packet: bytes) -> bytes:
    print(f"    [{'out' if sending else ' in'}] {packet.hex(' ')}")
    return packet


def build_simdevice(device: InverterDevice) -> SimDevice:
    # SunSpec devices keep everything in HOLDING registers (FC03).
    # The other 3 tables exist but are empty (1 dummy entry each).
    coils = [SimData(address=0, values=False, datatype=DataType.BITS)]
    discrete = [SimData(address=0, values=False, datatype=DataType.BITS)]
    holding = [SimData(address=SUNSPEC_BASE, values=device.image, datatype=DataType.REGISTERS)]
    inputs = [SimData(address=0, values=0, datatype=DataType.REGISTERS)]
    return SimDevice(id=DEVICE_ID, simdata=(coils, discrete, holding, inputs),
                     action=device.on_request)


async def main() -> None:
    ap = argparse.ArgumentParser(description="Simulated 100 kW SunSpec PV inverter")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5020)
    ap.add_argument("--start", help='simulated start time, e.g. "2026-07-15 06:00"')
    ap.add_argument("--speed", type=int, default=60, help="simulated seconds per real second")
    ap.add_argument("--weather", choices=["clear", "cloudy"], default="clear")
    ap.add_argument("--seed", type=int, default=None, help="random seed (repeatable runs)")
    ap.add_argument("--trace", action="store_true", help="print raw Modbus frames")
    args = ap.parse_args()

    start = (datetime.fromisoformat(args.start) if args.start
             else datetime.now().replace(hour=6, minute=0, second=0, microsecond=0))
    device = InverterDevice(start, args.speed, args.weather, args.seed)

    print("Simulated SunSpec inverter")
    print(f"  Modbus TCP {args.host}:{args.port}, device id {DEVICE_ID}")
    print(f"  SunSpec map at {SUNSPEC_BASE}..{SUNSPEC_BASE + TOTAL_REGISTERS - 1}  "
          f"(model 1 @ {MODEL_START[1]}, 103 @ {MODEL_START[103]}, "
          f"123 @ {MODEL_START[CONTROLS.id]}, 64001 @ {MODEL_START[64001]}, 64002 @ {MODEL_START[64002]})")
    print(f"  Clock: {start:%Y-%m-%d %H:%M}, {args.speed}x speed "
          f"(1 real minute = {args.speed} simulated minutes)")
    print("  Ctrl + C to stop\n")

    server = ModbusTcpServer(build_simdevice(device), address=(args.host, args.port),
                             trace_packet=show_traffic if args.trace else None)
    await asyncio.gather(server.serve_forever(), device.run_clock())


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer stopped.")
