"""The native OPL2 core (extractor/_opl2c.c) must render exactly what the
numpy/Python path renders.  The streams are random register programs that
exercise every envelope rate class, feedback, FM/AM, vibrato, tremolo and all
four waveforms, with key-on/off in the middle of blocks."""
import random

import pytest

from extractor import opl2

SLOT_REGS = [0, 1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 13, 16, 17, 18, 19, 20, 21]


def program(seed, n=40000):
    rnd = random.Random(seed)
    ev = [(0, 0x01, 0x20), (0, 0x08, rnd.choice((0, 0x40)))]
    for off in SLOT_REGS:
        ev += [(0, 0x20 + off, rnd.randrange(256)),
               (0, 0x40 + off, rnd.randrange(256)),
               (0, 0x60 + off, rnd.choice((0xF0, 0x00, rnd.randrange(256)))),
               (0, 0x80 + off, rnd.randrange(256)),
               (0, 0xE0 + off, rnd.randrange(4))]
    for c in range(9):
        ev += [(0, 0xC0 + c, rnd.randrange(16)),
               (0, 0xA0 + c, rnd.randrange(256)),
               (0, 0xB0 + c, 0x20 | rnd.randrange(32))]
    if rnd.random() < 0.5:
        ev.append((0, 0xBD, rnd.choice((0x80, 0x40, 0xC0))))
    for _ in range(120):
        off = rnd.randrange(1, n)
        c = rnd.randrange(9)
        reg = rnd.choice((0xB0 + c, 0xB0 + c, 0xA0 + c, 0xC0 + c,
                          0x40 + rnd.choice(SLOT_REGS), 0x60 + rnd.choice(SLOT_REGS),
                          0x80 + rnd.choice(SLOT_REGS), 0xE0 + rnd.choice(SLOT_REGS)))
        ev.append((off, reg, rnd.randrange(256)))
    ev.sort(key=lambda e: e[0])
    return n, ev


def render(seed, native):
    saved = opl2._NATIVE
    if not native:
        opl2._NATIVE = None
    try:
        n, ev = program(seed)
        return opl2.OPL2().render_block(n, ev)
    finally:
        opl2._NATIVE = saved


@pytest.mark.parametrize('seed', range(8))
def test_native_matches_python(seed):
    if opl2._NATIVE is None:
        pytest.skip('native core not built (python -m extractor.build_native)')
    assert render(seed, True) == render(seed, False)


def test_python_path_renders_sound():
    assert any(render(0, False))
