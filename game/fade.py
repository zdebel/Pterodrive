"""Screen fades of UGH.EXE (F=0x6BE6, 0x6BC9, 0x6BD8, 0x6C22, 0x6C4B).

The working palette (CS:4681, 768 six-bit bytes) is the game palette with colours
0-127 duplicated into 128-255.  A fade step with factor cx (0..0x100) computes
dst = (src * cx) >> 8 for every byte (CS:4981) and loads DAC entries 0..216
(range word 0xD800) - entries 217..255 keep whatever the DAC held (black after
the blanking call 0x6C4B).  The level loop calls the step once per iteration
while DS:27A8 <= 0x100, with cx = DS:27A8 *before* it is advanced; the title
sequence uses the two ramps below.
"""
from __future__ import annotations

FADED_ENTRIES = 0xD9                 # DAC entries 0..0xD8


def fade_bytes(src768: bytes, cx: int) -> bytes:
    """CS:0x6BE6: one factor applied to all 768 bytes (mul cx; keep AH)."""
    return bytes(((v * cx) & 0xFFFF) >> 8 for v in src768)


def apply_fade(dac, src768: bytes, cx: int):
    """Load the faded palette into `dac` (list of 256 [r,g,b]) like F=0x6BFA..0x6C45."""
    dst = fade_bytes(src768, cx)
    for i in range(FADED_ENTRIES):
        dac[i] = [dst[3 * i], dst[3 * i + 1], dst[3 * i + 2]]
    return dac


def fade_in_steps():                 # F=0x6BC9: cx = 0, 4, ... 0x100
    return list(range(0, 0x101, 4))


def fade_out_steps():                # F=0x6BD8: cx = 0x100, 0xFC, ... 0
    return list(range(0x100, -1, -4))


def blank_dac():                     # F=0x6C4B
    return [[0, 0, 0] for _ in range(256)]


def dup_palette(base384: bytes) -> bytes:
    """CS:0x641A: colours 0-127 copied to 0-127 and 128-255."""
    return bytes(base384[:384]) * 2
