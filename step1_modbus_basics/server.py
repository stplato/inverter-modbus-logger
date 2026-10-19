"""Step 1 - A minimal Modbus TCP server (the "field device").

Run:   python step1_modbus_basics/server.py
Stop:  Ctrl + C

It holds two tables of 16-bit registers, like a real inverter:

  Holding registers (FC03 read, FC06/FC16 write)  -> settings / setpoints
      0: active power limit [%]           = 100
      1: power factor setpoint [x1000]    = 1000  (means 1.000)
      2..9: spare                         = 0

  Input registers (FC04 read only)                -> measurements
      0: AC power [W]                      = 4520
      1: AC voltage [V x10]               = 2304  (means 230.4 V)
      2: grid frequency [Hz x100]         = 5001  (means 50.01 Hz)
      3: heatsink temperature [degC]      = 41
      4..9: spare                         = 0
"""

import logging

from pymodbus.server import StartTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice

HOST = "127.0.0.1"   # only this PC can connect (safe for learning)
PORT = 5020          # real devices use 502; 5020 avoids needing admin rights
DEVICE_ID = 1        # the "unit id" / "slave id" of this device

holding_registers = [100, 1000, 0, 0, 0, 0, 0, 0, 0, 0]
input_registers = [4520, 2304, 5001, 41, 0, 0, 0, 0, 0, 0]


def show_traffic(sending: bool, packet: bytes) -> bytes:
    """Print every raw Modbus TCP frame that goes in or out of the server."""
    direction = "server -> client" if sending else "client -> server"
    print(f"  [{direction}] {packet.hex(' ')}")
    return packet


def build_device() -> SimDevice:
    # A real Modbus device has 4 separate tables. pymodbus wants all 4 defined:
    #   (coils, discrete inputs, holding registers, input registers)
    coils = [SimData(address=0, count=16, values=False, datatype=DataType.BITS)]
    discrete_inputs = [SimData(address=0, count=16, values=False, datatype=DataType.BITS)]
    holding = [SimData(address=0, values=holding_registers, datatype=DataType.REGISTERS)]
    inputs = [SimData(address=0, values=input_registers, datatype=DataType.REGISTERS)]
    return SimDevice(id=DEVICE_ID, simdata=(coils, discrete_inputs, holding, inputs))


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)  # hide pymodbus' internal chatter
    print(f"Modbus TCP server listening on {HOST}:{PORT}, device id {DEVICE_ID}")
    print("Waiting for a client... (Ctrl + C to stop)\n")
    StartTcpServer(build_device(), address=(HOST, PORT), trace_packet=show_traffic)
