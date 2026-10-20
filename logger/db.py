"""Step 6C - Store readings in PostgreSQL (the SCADA "historian").

Two SCADA ideas live here:
  1. REGISTER the device once (devices table) and then store only its device_id.
  2. STORE-AND-FORWARD: if the database is down, keep readings in memory and
     write them later, so a database restart doesn't create a hole in the data.
"""

from __future__ import annotations

import logging
import os
import time
from collections import deque
from datetime import datetime

import psycopg

logging.getLogger("psycopg").setLevel(logging.CRITICAL)   # we report DB problems ourselves

DEFAULT_DSN = os.environ.get(
    "PV_DB_DSN", "host=127.0.0.1 port=5433 dbname=scada user=solar password=solar_dev")

READING_COLUMNS = ["time", "device_id", "quality", "state", "ac_kw", "dc_kw", "dc_v", "dc_a",
                   "v_phase_a", "hz", "pf_pct", "t_heatsink_c", "t_cabinet_c", "energy_kwh",
                   "limit_pct", "limit_enabled"]

INSERT_READING = (
    f"INSERT INTO readings ({', '.join(READING_COLUMNS)}) "
    f"VALUES ({', '.join(['%s'] * len(READING_COLUMNS))}) "
    "ON CONFLICT (device_id, time) DO NOTHING"          # same reading twice -> ignore
)

UPSERT_DEVICE = """
INSERT INTO devices (name, manufacturer, model, serial_number, firmware, host, port, unit_id)
VALUES (%(name)s, %(manufacturer)s, %(model)s, %(serial)s, %(firmware)s, %(host)s, %(port)s, %(unit_id)s)
ON CONFLICT (serial_number) DO UPDATE
    SET firmware = EXCLUDED.firmware, host = EXCLUDED.host,
        port = EXCLUDED.port, unit_id = EXCLUDED.unit_id
RETURNING device_id
"""


class Historian:
    def __init__(self, dsn: str = DEFAULT_DSN, buffer_size: int = 10_000):
        self.dsn = dsn
        self.conn: psycopg.Connection | None = None
        self.buffer: deque[tuple] = deque(maxlen=buffer_size)   # oldest dropped if full
        self.warned = False
        # Backoff: when the DB is down, don't retry on every poll - each attempt can block
        # for connect_timeout seconds and would slow down polling of the inverters.
        self.backoff = 5.0
        self.next_attempt = 0.0

    # --- connection -------------------------------------------------------
    def _connect(self) -> bool:
        if self.conn is not None and not self.conn.closed:
            return True
        if time.monotonic() < self.next_attempt:
            return False                                  # still waiting: don't block polling
        try:
            self.conn = psycopg.connect(self.dsn, autocommit=True, connect_timeout=3)
            # Device times are Athens local time -> let PostgreSQL attach the right offset
            self.conn.execute("SET TIME ZONE 'Europe/Athens'")
            if self.warned:
                print("  + database back online")
            self.warned = False
            self.backoff = 5.0
            return True
        except psycopg.OperationalError as exc:
            if not self.warned:
                print(f"  ! database unavailable ({str(exc).splitlines()[0]}) - buffering readings")
                self.warned = True
            self.conn = None
            self.next_attempt = time.monotonic() + self.backoff
            self.backoff = min(self.backoff * 2, 30.0)
            return False

    # --- devices ------------------------------------------------------------
    def register_device(self, name: str, manufacturer: str, model: str, serial: str,
                        firmware: str, host: str, port: int, unit_id: int) -> int | None:
        """Insert the device if new (matched by serial number) and return its device_id."""
        if not self._connect():
            return None
        try:
            row = self.conn.execute(UPSERT_DEVICE, {
                "name": name, "manufacturer": manufacturer, "model": model, "serial": serial,
                "firmware": firmware, "host": host, "port": port, "unit_id": unit_id,
            }).fetchone()
            return row[0]
        except psycopg.OperationalError:
            self.conn = None
            return None

    # --- readings -------------------------------------------------------------
    def store(self, device_id: int, time: datetime, row: dict) -> None:
        """Queue one reading, then try to write everything that is waiting."""
        values = [time, device_id] + [row.get(c) for c in READING_COLUMNS[2:]]
        self.buffer.append(tuple(values))
        self.flush()

    def flush(self) -> None:
        if not self.buffer or not self._connect():
            return
        try:
            with self.conn.cursor() as cur:
                cur.executemany(INSERT_READING, list(self.buffer))
            if len(self.buffer) > 1:
                print(f"  + wrote {len(self.buffer)} buffered readings to the database")
            self.buffer.clear()
        except psycopg.OperationalError:
            self.conn = None                      # connection died: keep the buffer, retry later

    def close(self) -> None:
        if self.conn is not None:
            self.flush()
            self.conn.close()
