"""Step 7 - Inject a TEST fault into the running simulator (simulation only).

    python -m simulator.inject fan_failure   cooling fan dies -> heatsink heats up -> power derating
    python -m simulator.inject grid_trip     grid frequency jumps to 51.6 Hz for 2 min -> disconnect
    python -m simulator.inject none          clear the fan failure

It writes vendor register "TestFault" (model 64002). Real inverters have no such
register - in the field, faults come from the real world, not from a command.
"""

from __future__ import annotations

import argparse
import logging

from pymodbus.client import ModbusTcpClient

from common.sunspec import FAULT_CODES, SIM_FAULT
from logger.decoder import discover


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("fault", choices=list(FAULT_CODES.values()))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5020)
    args = ap.parse_args()
    logging.basicConfig(level=logging.CRITICAL)

    client = ModbusTcpClient(args.host, port=args.port, timeout=2)
    if not client.connect():
        print("Cannot connect - is the simulator running?")
        return
    models = discover(client)
    if SIM_FAULT.id not in models:
        print("This device has no fault-injection model (64002).")
        return
    address = models[SIM_FAULT.id][0] + 2 + SIM_FAULT.point("TestFault").offset
    code = {v: k for k, v in FAULT_CODES.items()}[args.fault]
    rr = client.write_register(address, code)
    print(f"FC06 write TestFault={code} ({args.fault}) at {address} ->", "OK" if not rr.isError() else rr)
    client.close()


if __name__ == "__main__":
    main()
