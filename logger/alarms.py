"""Step 7B - Alarm manager: turn readings and device events into alarms.

Every poll, each rule decides "should this alarm be active now?". The manager only
acts on CHANGES: inactive -> active = RAISE (new row in `alarms`),
active -> inactive = CLEAR (fill in `cleared_at`).

Three kinds of rules, like a real SCADA:
  1. Device events  - the inverter's own SunSpec Evt1 bits (it already decided)
  2. Thresholds     - the SCADA's own rules on measurements, with
                      ON-DELAY  (condition must persist N polls) and
                      HYSTERESIS (clear at a lower value than the raise value)
  3. Communication  - no answer from the device for N polls in a row
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from logger.db import Historian

# Severity of the device's own events (SunSpec Evt1 names)
EVENT_SEVERITY = {
    "GRID_DISCONNECT": "CRITICAL", "OVER_FREQUENCY": "CRITICAL", "UNDER_FREQUENCY": "CRITICAL",
    "OVER_TEMP": "CRITICAL", "GROUND_FAULT": "CRITICAL", "AC_OVER_VOLT": "WARNING",
    "AC_UNDER_VOLT": "WARNING", "CABINET_OPEN": "WARNING", "MANUAL_SHUTDOWN": "INFO",
}


@dataclass
class ThresholdRule:
    """Raise when `field > raise_above` for `on_delay` polls in a row; clear when `< clear_below`."""
    code: str
    field: str
    raise_above: float
    clear_below: float          # hysteresis: lower than raise_above
    on_delay: int               # polls the condition must persist
    severity: str
    message: str
    _count: int = 0             # consecutive polls above the threshold

    def evaluate(self, row: dict, active: bool) -> bool:
        value = row.get(self.field)
        if value is None:                       # no data (e.g. COMMS_LOST): keep current state
            return active
        if value > self.raise_above:
            self._count += 1
        else:
            self._count = 0
        if not active:
            return self._count >= self.on_delay
        return value >= self.clear_below        # stay active until clearly below


@dataclass
class AlarmManager:
    historian: Historian | None
    device_id: int | None = None
    comms_delay: int = 3                        # COMMS_LOST alarm after 3 failed polls
    active: set[str] = field(default_factory=set)
    rules: list[ThresholdRule] = field(default_factory=lambda: [
        ThresholdRule("HIGH_TEMP", "t_heatsink_c", raise_above=70.0, clear_below=65.0,
                      on_delay=3, severity="WARNING",
                      message="Heatsink above 70 C - check cooling fan (inverter derating)"),
    ])
    _comms_fail: int = 0
    _synced: bool = False
    _started: dict = field(default_factory=dict)   # code -> raise time (to catch clock jumps)

    def _sync(self) -> None:
        """After a logger restart, pick up alarms that are still open in the database."""
        if self._synced or self.historian is None or self.device_id is None:
            return
        self.active |= self.historian.active_alarms(self.device_id)
        if self.active:
            print(f"  (alarms still open from before: {', '.join(sorted(self.active))})")
        self._synced = True

    def evaluate(self, row: dict, at: datetime) -> None:
        """Decide, for every alarm code, whether it should be active after this poll."""
        self._sync()
        wanted: dict[str, tuple[bool, str, str]] = {}    # code -> (active?, severity, message)

        # 3. Communication
        if row.get("quality") == "COMMS_LOST":
            self._comms_fail += 1
        else:
            self._comms_fail = 0
        wanted["COMMS_LOST"] = (self._comms_fail >= self.comms_delay, "CRITICAL",
                                f"No answer from the inverter for {self.comms_delay}+ polls")
        if row.get("quality") == "COMMS_LOST":
            # Device data unknown: keep event/threshold alarms as they are (don't clear blindly)
            for code in self.active - {"COMMS_LOST"}:
                wanted[code] = (True, "", "")
        else:
            # 1. Device events: active exactly while the inverter reports them
            events = set(row.get("events") or [])
            for code in events | (self.active & set(EVENT_SEVERITY)):
                wanted[code] = (code in events, EVENT_SEVERITY.get(code, "WARNING"),
                                f"Inverter reports {code}")
            # 2. Thresholds with on-delay + hysteresis
            for rule in self.rules:
                wanted[rule.code] = (rule.evaluate(row, rule.code in self.active),
                                     rule.severity, rule.message)

        for code, (should_be_active, severity, message) in wanted.items():
            if should_be_active and code not in self.active:
                self.active.add(code)
                self._started[code] = at
                print(f"  [ALARM RAISED ]  {code:16} {severity:8} {message}")
                if self.historian is not None and self.device_id is not None:
                    self.historian.raise_alarm(self.device_id, code, severity, message, at)
            elif not should_be_active and code in self.active:
                self.active.discard(code)
                started = self._started.pop(code, None)
                if started is not None and at < started:
                    # The device clock went backwards: never store a negative alarm duration
                    print(f"  ! {code}: clear time {at:%H:%M} is before raise time {started:%H:%M} "
                          "(clock jump) - using the raise time")
                    at = started
                print(f"  [ALARM CLEARED]  {code}")
                if self.historian is not None and self.device_id is not None:
                    self.historian.clear_alarm(self.device_id, code, at)
