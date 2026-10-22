"""Step 7B - alarm rules. Run: python -m pytest"""

from datetime import datetime, timedelta

from logger.alarms import AlarmManager, ThresholdRule

# The 10 heatsink temperatures from the flapping question (fan failure, T hovers ~70 C)
TEMPS = [70.4, 70.9, 70.6, 69.8, 70.7, 69.9, 71.1, 70.6, 69.7, 70.8]


def count_raises(rule: ThresholdRule, temps=TEMPS) -> int:
    raises, active = 0, False
    for t in temps:
        now_active = rule.evaluate({"t_heatsink_c": t}, active)
        raises += now_active and not active
        active = now_active
    return raises


def test_simple_rule_flaps():
    # Rule A: on above 70, off at 70 -> no delay, no hysteresis
    rule = ThresholdRule("T", "t_heatsink_c", raise_above=70, clear_below=70, on_delay=1,
                         severity="WARNING", message="")
    assert count_raises(rule) == 4


def test_delay_and_hysteresis_give_one_alarm():
    # Rule C: on after 3 polls above 70, off below 65
    rule = ThresholdRule("T", "t_heatsink_c", raise_above=70, clear_below=65, on_delay=3,
                         severity="WARNING", message="")
    assert count_raises(rule) == 1


def _row(**kw):
    base = {"quality": "GOOD", "events": [], "t_heatsink_c": 40.0}
    base.update(kw)
    return base


def test_comms_lost_needs_three_failed_polls_and_clears_on_first_good():
    m = AlarmManager(historian=None, device_id=1)
    t = datetime(2026, 4, 15, 12, 0)
    for i in range(2):
        m.evaluate({"quality": "COMMS_LOST"}, t + timedelta(minutes=i))
    assert "COMMS_LOST" not in m.active          # 2 failures: not yet
    m.evaluate({"quality": "COMMS_LOST"}, t + timedelta(minutes=2))
    assert "COMMS_LOST" in m.active              # 3rd failure: raised
    m.evaluate(_row(), t + timedelta(minutes=3))
    assert "COMMS_LOST" not in m.active          # first good poll: cleared


def test_device_events_follow_the_inverter():
    m = AlarmManager(historian=None, device_id=1)
    t = datetime(2026, 4, 15, 12, 0)
    m.evaluate(_row(events=["GRID_DISCONNECT", "OVER_FREQUENCY"]), t)
    assert m.active == {"GRID_DISCONNECT", "OVER_FREQUENCY"}
    m.evaluate(_row(events=["GRID_DISCONNECT"]), t + timedelta(minutes=1))
    assert m.active == {"GRID_DISCONNECT"}
    m.evaluate(_row(), t + timedelta(minutes=2))
    assert m.active == set()


def test_comms_lost_does_not_clear_other_alarms():
    m = AlarmManager(historian=None, device_id=1)
    t = datetime(2026, 4, 15, 12, 0)
    m.evaluate(_row(events=["GRID_DISCONNECT"]), t)
    m.evaluate({"quality": "COMMS_LOST"}, t + timedelta(minutes=1))
    assert "GRID_DISCONNECT" in m.active         # unknown != cleared


def test_clock_jump_never_gives_negative_alarm_duration():
    m = AlarmManager(historian=None, device_id=1)
    t = datetime(2026, 4, 15, 14, 0)
    for i in range(3):
        m.evaluate({"quality": "COMMS_LOST"}, t + timedelta(minutes=i))
    assert m._started["COMMS_LOST"] == t + timedelta(minutes=2)
    m.evaluate(_row(), datetime(2026, 4, 15, 11, 0))      # device restarted with an older clock
    assert "COMMS_LOST" not in m.active and "COMMS_LOST" not in m._started
