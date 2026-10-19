"""Tests for the SunSpec encoding/decoding. Run: python -m pytest"""

from datetime import datetime

from common.sunspec import (INVERTER_3PH, decode_point, from_int16, from_string, scale,
                            to_int16, to_string, to_uint32, from_uint32)
from logger.decoder import decode_model
from simulator.pv_model import Inverter
from simulator.sunspec_map import MODEL_START, SUNSPEC_BASE, build_register_image


def test_int16_twos_complement():
    assert to_int16(0xFFFF) == -1
    assert to_int16(0x8000) == -32768
    assert from_int16(-1) == 0xFFFF
    assert to_int16(from_int16(-2)) == -2


def test_uint32_uses_two_registers_high_word_first():
    assert from_uint32(152_340_000) == [0x0914, 0x8620]   # 2324 x 65536 + 34336
    assert to_uint32(0x0914, 0x8620) == 152_340_000


def test_scale_factor():
    assert scale(2304, -1) == 230.4
    assert scale(9987, 1) == 99870
    assert scale(5001, -2) == 50.01


def test_string_roundtrip():
    regs = from_string("SunS", 4)
    assert regs == [0x5375, 0x6E53, 0, 0]
    assert to_string(regs) == "SunS"


def test_not_implemented_is_none():
    p = INVERTER_3PH.point("TmpTrns")         # int16
    assert decode_point(p, [0x8000]) is None


def test_encode_then_decode_gives_back_the_measurements():
    inv = Inverter(seed=1)
    snap = inv.step(datetime(2026, 4, 15, 13, 0))
    image = build_register_image(snap, snap.time_local, 60)
    start = MODEL_START[103] - SUNSPEC_BASE
    decoded = decode_model(INVERTER_3PH, image[start:start + 52])
    assert abs(decoded["W"] - snap.ac_w) <= 5            # W_SF = 1 -> 10 W resolution
    assert abs(decoded["PhVphA"] - snap.v_phase[0]) <= 0.05
    assert decoded["St"] == snap.state
    assert decoded["TmpTrns"] is None                    # not implemented by our inverter
