"""Deterministic fixed-timestep clock modelled on UGH.EXE.

What the DOS code does (reverse engineered):
  * The game loop (CS:0x2A2D..0x2D54 of UGH.EXE) runs ONE logic update and ONE
    render per iteration and waits for the VGA vertical retrace once per
    iteration (CS:0x6273 polling port 0x3DA bit 3).  The video mode is a
    chained-off mode 13h derivative that never writes the Misc Output
    register, so it keeps mode 13h's 400-line timing:
        25.175 MHz / (800 clocks * 449 lines) = 70.0863 Hz.
    So: logic rate == frame rate == ~70.09 Hz, 1 tick per frame, no catch-up
    (a slow PC simply runs the game slower).
  * INT 08h is also hooked (CS segment 0x1A32) and PIT channel 0 is
    reprogrammed (mode 0x36).  That timer only drives the sound driver
    (registered periods 6211 PIT ticks ~192.1 Hz and 119275 ticks ~10.0 Hz);
    the ISR does not chain to the BIOS tick and PIT is reset to divisor 0
    (18.2 Hz) on exit.  Game logic does not depend on it.
"""
from __future__ import annotations

PIT_HZ = 1193181.818
VGA_DOT_CLOCK = 25_175_000.0
VGA_TOTAL_CLOCKS_PER_LINE = 800
VGA_TOTAL_LINES = 449
TICK_HZ = VGA_DOT_CLOCK / (VGA_TOTAL_CLOCKS_PER_LINE * VGA_TOTAL_LINES)   # 70.0866
TICK_DT = 1.0 / TICK_HZ

# periods registered with the INT 08h multiplexer (sound driver only)
SOUND_TIMER_TICKS = (6211, 119275)
SOUND_TIMER_HZ = tuple(PIT_HZ / t for t in SOUND_TIMER_TICKS)   # 192.1, 10.0


class FixedClock:
    """Accumulates real time and releases whole logic ticks.

    The original has no catch-up, but a modern host needs a bound so a stall
    does not trigger hundreds of ticks: `max_ticks_per_advance` caps the burst
    (set it to 1 to reproduce the DOS "slow machine = slow game" behaviour).
    All state is integer nanoseconds, so identical dt sequences give identical
    tick sequences.
    """

    def __init__(self, tick_hz: float = TICK_HZ, max_ticks_per_advance: int = 5):
        self.tick_ns = round(1e9 / tick_hz)
        self.max_ticks = max_ticks_per_advance
        self.acc_ns = 0
        self.tick = 0                      # total logic ticks released

    def advance(self, dt_seconds: float) -> int:
        """Add elapsed real time; return how many logic ticks to run now."""
        return self.advance_ns(round(dt_seconds * 1e9))

    def advance_ns(self, dt_ns: int) -> int:
        self.acc_ns += max(0, dt_ns)
        n = min(self.acc_ns // self.tick_ns, self.max_ticks)
        if self.acc_ns // self.tick_ns > self.max_ticks:
            self.acc_ns = 0                # drop the backlog (stall)
        else:
            self.acc_ns -= n * self.tick_ns
        self.tick += n
        return n

    @property
    def alpha(self) -> float:
        """Fraction of the next tick already elapsed (for render interpolation)."""
        return self.acc_ns / self.tick_ns

    def reset(self):
        self.acc_ns = 0
        self.tick = 0


class DosClock(FixedClock):
    """1 logic tick + 1 render per VGA frame, no catch-up (original behaviour)."""

    def __init__(self):
        super().__init__(TICK_HZ, max_ticks_per_advance=1)
