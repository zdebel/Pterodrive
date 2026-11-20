"""Rain/snow particles (levels with a non-zero wind byte, DS:28F5).

Port of the code at F=0x59B4..0x5A8D (loader warm-up, respawn 0x59E5, update
0x5A28) and the draw routine 0x5AC9.  One particle per scanline 0..water line,
stored as a byte offset in the 96-byte-stride video page plus a plane number
(pixel x = (offset % 96) * 4 + plane).  Particles are drawn as single pixels of
colour 0x5B.  The random generator (F=0x6CB9) has no other user, so its state
only advances on weather levels; DS:2907 (water row offset, written by the water
wave routine) also carries over from the previous level.
"""
from __future__ import annotations

STRIDE = 0x60
COLOR = 0x5B
N = 0xC1                                    # bx = 0 .. 0x180 step 2
RNG_INIT = (0x7F03, 0x8134, 0x48BC, 0x2347)  # CS:4EF7..4EFE in the file


class Rng:
    """CS:0x6CB9: four chained add-with-carry words, result = high word of
    (state * range)."""

    def __init__(self, state=RNG_INIT):
        self.s = list(state)

    def rand(self, rng_range: int) -> int:
        s = self.s
        ax = rng_range & 0xFFFF
        t = ax + s[0]
        ax, c = t & 0xFFFF, t >> 16
        t = s[1] + ax + c
        dx, c = t & 0xFFFF, t >> 16
        s[1] = dx
        t = s[2] + dx + c
        cx, c = t & 0xFFFF, t >> 16
        s[2] = cx
        t = s[3] + cx + c
        dx, c = t & 0xFFFF, t >> 16
        s[3] = dx
        t = ax + dx + c
        ax = t & 0xFFFF
        s[0] = ax
        return ((ax * (rng_range & 0xFFFF)) >> 16) & 0xFFFF


class Particles:
    def __init__(self, wind: int, rng: Rng, water_line: int, water_off: int):
        self.wind = wind                         # DS:28F5 (1 or 2)
        self.rng = rng
        self.pos = [0] * N                       # DS:2E8B
        self.plane = [0] * N                     # DS:318F
        # loader warm-up: respawn all, then 0x241 updates
        for i in range(N - 1, -1, -1):
            self._respawn(i, water_line)
        for _ in range(0x241):
            self.update(water_line, water_off)

    def _respawn(self, i, water_line):           # CS:0x59E5
        r = self.rng.rand(0x140 + water_line)
        if r < 0x140:
            row, x = 0, r
        else:
            row = r - 0x140
            x = 0x13F if self.wind == 1 else 0
        self.pos[i] = ((row & 0xFF) * STRIDE + (x >> 2)) & 0xFFFF
        self.plane[i] = x & 3

    def update(self, water_line, water_off):     # CS:0x5A28
        bp = (self.wind - 1) * 2 - 1
        for i in range(water_line + 1, -1, -1):
            if i >= N:
                continue
            ax = self.pos[i] + 0xC0
            if not i & 1:
                ax += STRIDE
            cx = self.plane[i] + 2 * bp
            if not i & 1:
                cx += bp
            if cx & 0xFC:                        # test cx,0xFC (16-bit)
                ax += bp
            cx &= 3
            ax &= 0xFFFF
            if ax >= water_off:                  # cmp ax,[2907] / jb
                self._respawn(i, water_line)
            else:
                self.pos[i], self.plane[i] = ax, cx

    def pixels(self, water_line):                # CS:0x5AC9
        out = []
        for i in range(min(water_line, N - 1), -1, -1):
            p = self.pos[i]
            out.append(((p % STRIDE) * 4 + self.plane[i], p // STRIDE))
        return out
