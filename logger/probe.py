"""Walkthrough 3 - A tiny "Modbus Poll" tool: send ONE request and show the answer.

Commissioning engineers use tools like this (Modbus Poll, QModMaster) to test a
device by hand before the SCADA is configured.

    python -m logger.probe read 40070 2        FC03: read 2 holding registers from 40070
    python -m logger.probe write 40084 5000    FC06: write 5000 into register 40084
    python -m logger.probe read 40070 2 --device-id 5
"""

from __future__ import annotations

import argparse
import logging

from pymodbus.client import ModbusTcpClient

EXCEPTION_NAMES = {1: "ILLEGAL FUNCTION", 2: "ILLEGAL DATA ADDRESS", 3: "ILLEGAL DATA VALUE",
                   4: "DEVICE FAILURE", 11: "GATEWAY TARGET FAILED TO RESPOND"}


def show(sending: bool, packet: bytes) -> bytes:
    print(f"  {'SENT    ' if sending else 'RECEIVED'}  {packet.hex(' ')}")
    return packet


def main() -> None:
    ap = argparse.ArgumentParser(description="Send one Modbus request")
    ap.add_argument("action", choices=["read", "write"])
    ap.add_argument("address", type=int)
    ap.add_argument("value", type=int, help="read: how many registers / write: the value")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5020)
    ap.add_argument("--device-id", type=int, default=1)
    args = ap.parse_args()
    logging.basicConfig(level=logging.CRITICAL)

    client = ModbusTcpClient(args.host, port=args.port, timeout=2, retries=0, trace_packet=show)
    if not client.connect():
        print("Cannot connect - is the simulator running?")
        return

    if args.action == "read":
        print(f"FC03 read {args.value} register(s) from {args.address}, device {args.device_id}")
        rr = client.read_holding_registers(args.address, count=args.value, device_id=args.device_id)
    else:
        print(f"FC06 write {args.value} to register {args.address}, device {args.device_id}")
        rr = client.write_register(args.address, args.value & 0xFFFF, device_id=args.device_id)

    if rr.isError():
        code = getattr(rr, "exception_code", None)
        print(f"-> EXCEPTION {code}: {EXCEPTION_NAMES.get(code, '?')}")
    elif args.action == "read":
        print("-> registers:", rr.registers)
    else:
        print("-> OK, device accepted the write")
    client.close()


if __name__ == "__main__":
    main()
