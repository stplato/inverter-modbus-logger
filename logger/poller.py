"""Step 5 + 6C - The data logger: a tiny SCADA poller.

Every --interval seconds it reads the inverter's SunSpec model 103, decodes it,
prints one line, writes one row to PostgreSQL (Step 6C) and one row to a CSV
backup file in data/.
If the inverter stops answering it marks the data COMMS_LOST and keeps
trying to reconnect (1 s, 2 s, 4 s ... up to 30 s between attempts).
If the DATABASE is down, readings are buffered in memory and written later.

Start the database and the simulator first, then from the project folder:
    python -m logger.poller
    python -m logger.poller --interval 2
    python -m logger.poller --no-db          (CSV only, like Step 5)
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pymodbus.client import ModbusTcpClient

from common.sunspec import COMMON, CONTROLS, INVERTER_3PH, SIM_CLOCK, STATE_NAMES, decode_events
from logger.decoder import ModbusReadError, decode_model, discover, read_model
from logger.alarms import AlarmManager
from logger.db import DEFAULT_DSN, Historian

CSV_FIELDS = ["time_device", "time_logger", "quality", "state", "ac_kw", "dc_kw", "dc_v",
              "dc_a", "v_phase_a", "hz", "pf_pct", "energy_kwh", "t_heatsink_c",
              "t_cabinet_c", "limit_pct", "limit_enabled"]


class Poller:
    def __init__(self, host: str, port: int, device_id: int, csv_dir: Path,
                 historian: Historian | None = None):
        self.client = ModbusTcpClient(host, port=port, timeout=2, retries=0)
        self.host, self.port, self.device_id = host, port, device_id   # device_id = Modbus unit id
        self.models: dict[int, tuple[int, int]] = {}
        self.csv_dir = csv_dir
        self.historian = historian
        self.alarms = AlarmManager(historian)                       # Step 7B
        self.identity: dict | None = None     # model 1: manufacturer, model, serial, firmware
        self.db_device_id: int | None = None  # devices.device_id in PostgreSQL
        self.last_dev_time: datetime | None = None   # to estimate time during COMMS_LOST
        self.last_dev_mono = 0.0
        self.speed = 1
        self.last_wh: float | None = None   # last energy counter value we saw
        self.logged_wh = 0.0                # energy measured while this logger ran
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
        self.identity = ident
        names = ", ".join(f"{mid} @ {addr}" for mid, (addr, _) in self.models.items())
        print(f"Connected: {ident['Mn']} {ident['Md']}  SN {ident['SN']}  fw {ident['Vr']}")
        print(f"SunSpec models found: {names}")
        self.register_device()
        print()
        print(f"{'device time':16}  {'state':9} {'P_ac kW':>8} {'P_dc kW':>8} {'V_dc':>6} "
              f"{'Hz':>6} {'T_snk':>6} {'E_logged kWh':>12}  limit  events")

    def register_device(self) -> None:
        """Make sure this inverter has a row in the devices table (matched by serial)."""
        if self.historian is None or self.identity is None or self.db_device_id is not None:
            return
        ident = self.identity
        self.db_device_id = self.historian.register_device(
            name=f"INV-{self.device_id:02d}", manufacturer=ident["Mn"], model=ident["Md"],
            serial=ident["SN"], firmware=ident["Vr"], host=self.host, port=self.port,
            unit_id=self.device_id)
        if self.db_device_id is not None:
            self.alarms.device_id = self.db_device_id
            print(f"Database: {ident['SN']} is device_id {self.db_device_id}")

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
            self.speed = clock.get("Speed") or 1
        else:
            t_dev = datetime.now()
        if self.last_dev_time is not None and t_dev < self.last_dev_time - timedelta(minutes=1):
            print(f"  ! device clock jumped BACKWARDS: {self.last_dev_time:%H:%M} -> {t_dev:%H:%M} "
                  "(device restarted / clock reset?) - data and alarm times may overlap")
        self.last_dev_time, self.last_dev_mono = t_dev, time.monotonic()

        # Energy = sum of counter INCREASES. If the counter goes backwards the device
        # was reset or replaced: a real SCADA must not book negative energy.
        if self.last_wh is not None:
            delta = inv["WH"] - self.last_wh
            if delta < 0:
                print(f"  ! energy counter went BACKWARDS by {-delta / 1000:.1f} kWh "
                      "(device reset/replaced?) - delta ignored")
            else:
                self.logged_wh += delta
        self.last_wh = inv["WH"]
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
            "limit_enabled": bool(ctl.get("WMaxLim_Ena")),
            "events": decode_events(inv["Evt1"]),           # Step 7: inverter's own event flags
            "_e_logged": self.logged_wh / 1000,
        }

    def comms_lost_row(self) -> dict:
        """A COMMS_LOST row. The device time is unknown, so estimate it from the last
        good reading: last device time + real seconds passed x clock speed."""
        row = {"time_logger": datetime.now().isoformat(timespec="seconds"), "quality": "COMMS_LOST"}
        if self.last_dev_time is not None:
            elapsed = (time.monotonic() - self.last_dev_mono) * self.speed
            row["time_device"] = (self.last_dev_time + timedelta(seconds=elapsed)).isoformat(
                timespec="seconds")
        return row

    def poll(self) -> None:
        if not self.client.connected and not self.connect():
            self.save(self.comms_lost_row())
            return
        try:
            row = self.read_once()
        except (ModbusReadError, Exception) as exc:      # timeout, socket closed, ...
            print(f"  ! read failed ({type(exc).__name__}): marking COMMS_LOST")
            self.client.close()
            self.save(self.comms_lost_row())
            return
        limit = f"{row['limit_pct']:.0f}%" if row["limit_enabled"] else "off"
        print(f"{row['time_device'][:16].replace('T', ' ')}  {row['state']:9} "
              f"{row['ac_kw']:8.1f} {row['dc_kw']:8.1f} {row['dc_v']:6.1f} "
              f"{row['hz']:6.2f} {row['t_heatsink_c']:6.1f} {row['_e_logged']:12.1f}  {limit:>5}"
              f"  {','.join(row['events'])}")
        self.save(row)

    # --- storage: PostgreSQL (main) + CSV (local backup) ----------------------
    def save(self, row: dict) -> None:
        if self.historian is not None:
            self.register_device()                    # in case the DB was down at connect
            if self.db_device_id is not None and row.get("time_device"):
                self.historian.store(self.db_device_id,
                                     datetime.fromisoformat(row["time_device"]), row)
        if row.get("time_device"):                    # Step 7B: alarms on every poll
            self.alarms.evaluate(row, datetime.fromisoformat(row["time_device"]))
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
    # Defaults can come from environment variables (used by docker-compose.yml)
    ap.add_argument("--host", default=os.environ.get("INVERTER_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("INVERTER_PORT", 5020)))
    ap.add_argument("--device-id", type=int, default=1)
    ap.add_argument("--interval", type=float, default=float(os.environ.get("POLL_INTERVAL", 5.0)),
                    help="seconds between polls")
    ap.add_argument("--csv-dir", default="data")
    ap.add_argument("--db", default=DEFAULT_DSN, help="PostgreSQL connection string")
    ap.add_argument("--no-db", action="store_true", help="CSV only (no database)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.CRITICAL)      # we report errors ourselves
    historian = None if args.no_db else Historian(args.db)
    poller = Poller(args.host, args.port, args.device_id, Path(args.csv_dir), historian)
    target = "CSV only" if historian is None else "PostgreSQL + CSV backup"
    print(f"Logger polling {args.host}:{args.port} every {args.interval:g} s "
          f"-> {target}  (Ctrl + C to stop)")
    try:
        while True:
            started = time.monotonic()
            poller.poll()
            time.sleep(max(0.0, args.interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        poller.client.close()
        if historian is not None:
            historian.close()
        print("\nLogger stopped.")


if __name__ == "__main__":
    main()
