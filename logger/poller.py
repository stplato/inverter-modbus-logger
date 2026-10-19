"""Step 5 - The data logger: a tiny SCADA poller.

Every --interval seconds it reads the inverter's SunSpec model 103, decodes it,
prints one line and appends one row to a CSV file in data/.
If the inverter stops answering it marks the data COMMS_LOST and keeps
trying to reconnect (1 s, 2 s, 4 s ... up to 30 s between attempts).

Start the simulator first, then from the project folder:
    python -m logger.poller
    python -m logger.poller --interval 2
"""

from __future__ import annotations

import argparse
import csv
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from pymodbus.client import ModbusTcpClient

from common.sunspec import COMMON, CONTROLS, INVERTER_3PH, SIM_CLOCK, STATE_NAMES
from logger.decoder import ModbusReadError, decode_model, discover, read_model

CSV_FIELDS = ["time_device", "time_logger", "quality", "state", "ac_kw", "dc_kw", "dc_v",
              "dc_a", "v_phase_a", "hz", "pf_pct", "energy_kwh", "t_heatsink_c",
              "t_cabinet_c", "limit_pct", "limit_enabled"]


class Poller:
    def __init__(self, host: str, port: int, device_id: int, csv_dir: Path):
        self.client = ModbusTcpClient(host, port=port, timeout=2, retries=0)
        self.host, self.port, self.device_id = host, port, device_id
        self.models: dict[int, tuple[int, int]] = {}
        self.csv_dir = csv_dir
        self.first_wh: float | None = None
        self.backoff = 1.0
        self.next_attempt = 0.0

    # --- connection management -------------------------------------------
    def connect(self) -> bool:
        if time.monotonic() < self.next_attempt:
            return False
        if self.client.connect():
            try:
                self.models = discover(self.client, self.device_id)
                self.print_identity()
                self.backoff = 1.0
                return True
            except ModbusReadError as exc:
                print(f"  discovery failed: {exc}")
        self.client.close()
        self.next_attempt = time.monotonic() + self.backoff
        print(f"  cannot reach {self.host}:{self.port}, retry in {self.backoff:.0f} s")
        self.backoff = min(self.backoff * 2, 30)
        return False

    def print_identity(self) -> None:
        start, length = self.models[COMMON.id]
        ident = decode_model(COMMON, read_model(self.client, start, length, self.device_id))
        names = ", ".join(f"{mid} @ {addr}" for mid, (addr, _) in self.models.items())
        print(f"Connected: {ident['Mn']} {ident['Md']}  SN {ident['SN']}  fw {ident['Vr']}")
        print(f"SunSpec models found: {names}\n")
        print(f"{'device time':16}  {'state':9} {'P_ac kW':>8} {'P_dc kW':>8} {'V_dc':>6} "
              f"{'Hz':>6} {'T_snk':>6} {'E_logged kWh':>12}  limit")

    # --- one poll ---------------------------------------------------------
    def read_once(self) -> dict:
        def get(model):
            start, length = self.models[model.id]
            return decode_model(model, read_model(self.client, start, length, self.device_id))

        inv = get(INVERTER_3PH)
        ctl = get(CONTROLS) if CONTROLS.id in self.models else {}
        clock = get(SIM_CLOCK) if SIM_CLOCK.id in self.models else {}

        if clock.get("SimTime"):
            t_dev = datetime.fromtimestamp(clock["SimTime"], tz=timezone.utc).replace(tzinfo=None)
        else:
            t_dev = datetime.now()

        if self.first_wh is None:
            self.first_wh = inv["WH"]
        return {
            "time_device": t_dev.isoformat(timespec="seconds"),
            "time_logger": datetime.now().isoformat(timespec="seconds"),
            "quality": "GOOD",
            "state": STATE_NAMES.get(inv["St"], str(inv["St"])),
            "ac_kw": inv["W"] / 1000,
            "dc_kw": inv["DCW"] / 1000,
            "dc_v": inv["DCV"],
            "dc_a": inv["DCA"],
            "v_phase_a": inv["PhVphA"],
            "hz": inv["Hz"],
            "pf_pct": inv["PF"],
            "energy_kwh": inv["WH"] / 1000,
            "t_heatsink_c": inv["TmpSnk"],
            "t_cabinet_c": inv["TmpCab"],
            "limit_pct": ctl.get("WMaxLimPct"),
            "limit_enabled": ctl.get("WMaxLim_Ena"),
            "_e_today": (inv["WH"] - self.first_wh) / 1000,
        }

    def poll(self) -> None:
        if not self.client.connected and not self.connect():
            self.save({"time_logger": datetime.now().isoformat(timespec="seconds"),
                       "quality": "COMMS_LOST"})
            return
        try:
            row = self.read_once()
        except (ModbusReadError, Exception) as exc:      # timeout, socket closed, ...
            print(f"  ! read failed ({type(exc).__name__}): marking COMMS_LOST")
            self.client.close()
            self.save({"time_logger": datetime.now().isoformat(timespec="seconds"),
                       "quality": "COMMS_LOST"})
            return
        limit = f"{row['limit_pct']:.0f}%" if row["limit_enabled"] else "off"
        print(f"{row['time_device'][:16].replace('T', ' ')}  {row['state']:9} "
              f"{row['ac_kw']:8.1f} {row['dc_kw']:8.1f} {row['dc_v']:6.1f} "
              f"{row['hz']:6.2f} {row['t_heatsink_c']:6.1f} {row['_e_today']:12.1f}  {limit}")
        self.save(row)

    # --- storage (CSV for now; PostgreSQL in Step 6) ------------------------
    def save(self, row: dict) -> None:
        self.csv_dir.mkdir(parents=True, exist_ok=True)
        path = self.csv_dir / f"readings_{datetime.now():%Y%m%d}.csv"
        new_file = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
            if new_file:
                w.writeheader()
            w.writerow(row)


def main() -> None:
    ap = argparse.ArgumentParser(description="SunSpec Modbus data logger")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5020)
    ap.add_argument("--device-id", type=int, default=1)
    ap.add_argument("--interval", type=float, default=5.0, help="seconds between polls")
    ap.add_argument("--csv-dir", default="data")
    args = ap.parse_args()

    logging.basicConfig(level=logging.CRITICAL)      # we report errors ourselves
    poller = Poller(args.host, args.port, args.device_id, Path(args.csv_dir))
    print(f"Logger polling {args.host}:{args.port} every {args.interval:g} s "
          f"-> {args.csv_dir}/  (Ctrl + C to stop)")
    try:
        while True:
            started = time.monotonic()
            poller.poll()
            time.sleep(max(0.0, args.interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        poller.client.close()
        print("\nLogger stopped.")


if __name__ == "__main__":
    main()
