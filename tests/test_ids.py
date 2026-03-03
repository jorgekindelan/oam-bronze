from __future__ import annotations

from oam.core.ids import stable_id, uuid7


def test_stable_id_deterministic():
    a = stable_id("disc", ["NL", "AFM", "123", "u1", "u2"])
    b = stable_id("disc", ["NL", "AFM", "123", "u1", "u2"])
    assert a == b


def test_uuid7_monotonic_int_order():
    ids = [uuid7().int for _ in range(20)]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)
