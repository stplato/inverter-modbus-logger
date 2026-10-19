"""Step 5 - Send a curtailment command, like a grid operator / park controller does.

    python -m logger.set_limit 60      -> limit the inverter to 60 % of 100 kW
    python -m logger.set_limit off     -> remove the limit
    python -m logger.set_limit 150     -> try an invalid value (see the exception)

It writes SunSpec model 123: WMaxLimPct (the %) and WMaxLim_Ena (enable).
"""

from __future__ import annotations

import argparse
import logging

from pymodbus.client import ModbusTcpClient

from common.sunspec import CONTROLS
from logger.decoder import decode_model, discover, read_model


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("limit", help="percent 0-100, or 'off'")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5020)
    ap.add_argument("--device-id", type=int, default=1)
    args = ap.parse_args()
    logging.basicConfig(level=logging.CRITICAL)

    client = ModbusTcpClient(args.host, port=args.port, timeout=2)
    if not client.connect():
        print("Cannot connect - is the simulator running?")
        return
    start, length = discover(client, args.device_id)[CONTROLS.id]
    base = start + 2                                     # skip the ID and L registers
    addr_limit = base + CONTROLS.point("WMaxLimPct").offset
    addr_enable = base + CONTROLS.point("WMaxLim_Ena").offset

    if args.limit.lower() == "off":
        rr = client.write_register(addr_enable, 0, device_id=args.device_id)
        print(f"FC06 write WMaxLim_Ena=0 at {addr_enable} ->", "OK" if not rr.isError() else rr)
    else:
        pct = int(args.limit)
        rr = client.write_register(addr_limit, pct & 0xFFFF, device_id=args.device_id)
        print(f"FC06 write WMaxLimPct={pct} at {addr_limit} ->", "OK" if not rr.isError() else rr)
        if rr.isError():
            client.close()
            return
        rr = client.write_register(addr_enable, 1, device_id=args.device_id)
        print(f"FC06 write WMaxLim_Ena=1 at {addr_enable} ->", "OK" if not rr.isError() else rr)

    ctl = decode_model(CONTROLS, read_model(client, start, length, args.device_id))
    print(f"Read back: WMaxLimPct = {ctl['WMaxLimPct']} %, WMaxLim_Ena = {ctl['WMaxLim_Ena']}")
    client.close()


if __name__ == "__main__":
    main()
