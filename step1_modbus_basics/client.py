"""Step 1 - A minimal Modbus TCP client (the "SCADA poller").

Start server.py first in another PowerShell window, then run:
    python step1_modbus_basics/client.py
"""

from pymodbus.client import ModbusTcpClient

HOST = "127.0.0.1"
PORT = 5020
DEVICE_ID = 1


def main() -> None:
    client = ModbusTcpClient(HOST, port=PORT, timeout=3)
    if not client.connect():
        print(f"Could not connect to {HOST}:{PORT}. Is server.py running?")
        return
    print(f"Connected to {HOST}:{PORT}\n")

    # --- 1. FC04: read input registers (measurements) ----------------------
    rr = client.read_input_registers(0, count=4, device_id=DEVICE_ID)
    if rr.isError():
        print("FC04 failed:", rr)
        return
    power_w, volt_raw, freq_raw, temp_c = rr.registers
    print("FC04  Read Input Registers 0-3 (raw):", rr.registers)
    print(f"      AC power    = {power_w} W")
    print(f"      AC voltage  = {volt_raw / 10} V      (raw {volt_raw} / 10)")
    print(f"      Frequency   = {freq_raw / 100} Hz    (raw {freq_raw} / 100)")
    print(f"      Temperature = {temp_c} degC\n")

    # --- 2. FC03: read holding registers (settings) ------------------------
    rr = client.read_holding_registers(0, count=2, device_id=DEVICE_ID)
    print("FC03  Read Holding Registers 0-1:", rr.registers)
    print(f"      Power limit = {rr.registers[0]} %,  PF setpoint = {rr.registers[1] / 1000}\n")

    # --- 3. FC06: write ONE holding register (curtail the inverter to 60 %) -
    wr = client.write_register(0, 60, device_id=DEVICE_ID)
    print("FC06  Write Single Register 0 = 60 ->", "OK" if not wr.isError() else wr)

    # --- 4. FC16: write SEVERAL holding registers at once ------------------
    wr = client.write_registers(1, [950, 7, 8], device_id=DEVICE_ID)
    print("FC16  Write Multiple Registers 1-3 = [950, 7, 8] ->", "OK" if not wr.isError() else wr)

    rr = client.read_holding_registers(0, count=4, device_id=DEVICE_ID)
    print("FC03  Read back Holding Registers 0-3:", rr.registers, "\n")

    # --- 5. What a real device does with a bad request ---------------------
    rr = client.read_input_registers(500, count=1, device_id=DEVICE_ID)
    print("FC04  Read Input Register 500 (does not exist) ->", rr)

    client.close()
    print("\nDone, connection closed.")


if __name__ == "__main__":
    main()
