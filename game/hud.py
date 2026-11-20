"""The 8-line status strip of UGH.EXE, ported from the original code.

Where it lives: HUD sprites are blitted with y >= 0xC0, which CS:0x5FAD sends to
the video page at CS:4604 (offset 0), row = y - 0xC0.  The CRTC split shows
those 8 rows under the 192-line playfield, i.e. screen lines 192..199.

Everything below is a port of:
    F=0x5C29  panel (two sprites)            F=0x5D05  per-iteration update
    F=0x5C5A  one digit sprite               F=0x5CAF/5C6D/5C7F  number drawing
    F=0x5E92..0x5F1C  energy bar written straight to video memory
The update is INCREMENTAL, exactly as in the original: a field is redrawn only
when it differs from the value drawn last time, only the digits that changed
are redrawn, and some fields end the pass early (`jmp 0x5E6A`) so at most one of
them is refreshed per main-loop iteration.  Sprites are drawn with colour 0
transparent over what is already there (the panel), so the strip is stateful.

Layout (pixels, x then y; y is the screen line):
    panel          sprites 0xAE + 0xAF (1 player) / 0xB0 + 0xB1 (2 players)
                   at (0, 193) and (255, 193)            255x7 + 65x7
    digits         sprite 0xCC + n, 6x5, drawn at y = 194, advance 6 px
    lives          2 digits          x = 0x21
    score          6 digits          x = 0x76 (1P)  0x72 (2P)
    people to go   2 digits          x = 0xB1 (1P)  0xA9 (2P)
    carried icon   sprite 0xB2 + DS:27FC       at (0xE0|0xD7, 193)   16x7
    dest. tablet   sprite 0xB7 + DS:2804       at (0xD0|0xC7, 193)   16x7
    player 2       sprites 0xBF + DS:27FE and 0xC4 + DS:2806 at (0x102, 193)/(0x112, 193)
    fare           4 digits          x = 0xF4 (1P)  0xE8 (2P)
    multiplier     2 digits          x = 0x122 (1P) 0x131 (2P)
    energy bar     3 rows (lines 195-197), starts at x = 64, colour 0x32,
                   length = energy // 700 pixels (max 33)
"""
from __future__ import annotations

ROWS = 8
WIDTH = 384                 # video page stride is 0x60 bytes = 384 pixels
BAR_COLOR = 0x32
BAR_X0 = 64                 # DS:A7 = A000:0130 -> row 3, byte 16

_POS = {   # DS:27B0..27C0
    1: dict(lives=0x21, score=0x76, left=0xB1, icon_dest=0xD0, icon=0xE0,
            fare=0xF4, mult=0x122),
    2: dict(lives=0x21, score=0x72, left=0xA9, icon_dest=0xC7, icon=0xD7,
            fare=0xE8, mult=0x131, icon2=0x102, icon2_dest=0x112),
}


def _wrap100(v):                                   # CS:5B33..5B41
    v = (v + 0x0B) & 0xFF
    return v - 0x64 if v >= 0x64 else v


def digits_n(value: int, n: int):
    """CS:0x5CF3 (2 digits) / 5CDF (4) / 5CBF (6): digit lists, no terminator."""
    if n == 2:
        return [(value & 0xFF) // 10, (value & 0xFF) % 10]
    if n == 4:
        return [value // 1000, value % 1000 // 100, value % 100 // 10, value % 10]
    v = value
    return [v // 100000, v % 100000 // 10000, v % 10000 // 1000,
            v % 1000 // 100, v % 100 // 10, v % 10]


class HudRenderer:
    def __init__(self, sprite):
        """sprite(id) -> (w, h, rows)  (see render_levels.load_sprites)."""
        self._sprite = sprite
        self._cache = {}
        self.pixels = bytearray(ROWS * WIDTH)

    # ------------------------------------------------------------ sprites
    def _spr(self, sid):
        s = self._cache.get(sid)
        if s is None:
            w, h, rows = self._sprite(sid)
            s = self._cache[sid] = (w, h, [bytes(r) for r in rows])
        return s

    def blit(self, sid, x, y, water_line):
        """CS:0x5FAD for y >= 0xC0: colour 0 transparent, rows below the water
        line get +0x40 (never the case for the strip unless the water is at the
        very top of the screen)."""
        w, h, rows = self._spr(sid)
        row0 = y - 0xC0
        bp0 = water_line - row0
        for j in range(h):
            r = row0 + j
            if not 0 <= r < ROWS:
                continue
            tint = (bp0 - j) < 0
            line = rows[j]
            for i in range(w):
                v = line[i]
                if v:
                    xx = x + i
                    if 0 <= xx < WIDTH:
                        self.pixels[r * WIDTH + xx] = (v | 0x40) if tint else v

    # -------------------------------------------------------- level start
    def reset(self, mode, lives, mult, score, water_line):
        """CS:0x5B16 HUD variables + CS:0x5C29 panel."""
        self.mode = mode
        self.p = _POS[mode]
        self.pixels = bytearray(ROWS * WIDTH)
        self.v_lives = _wrap100(lives)                       # DS:27CB
        self.v_mult = _wrap100(mult)                         # DS:27CA
        self.v_left = 0x63                                   # DS:27CC
        self.v_fare = 0x270F                                 # DS:27C6
        s = score + 0x1B207                                  # DS:27C2/27C4
        if s > 0xF4240:                                      # jb/ja/cmp 0x4240 chain
            s -= 0xF4240
        self.v_score = s
        self.v_icon = [-1, -1]                               # DS:2800 / 2802
        self.v_bar = 0                                       # DS:27A6
        a, b = (0xAE, 0xAF) if mode == 1 else (0xB0, 0xB1)
        self.blit(a, 0, 0xC1, water_line)
        self.blit(b, 0xFF, 0xC1, water_line)

    # ------------------------------------------------------- number drawing
    def _number(self, new, old, x, water_line):
        for n, o in zip(new, old):                           # CS:0x5C8D
            if n != o:
                self.blit(0xCC + n, x, 0xC2, water_line)
            x += 6

    # -------------------------------------------------------------- update
    def update(self, s):
        """One call of CS:0x5D05.  `s` provides: lives, score, left (DS:28F1),
        mode, crafts (occupied, dest_number, fare), multiplier, energy,
        water_line, hud_flag (DS:27CD, writable)."""
        wl = s.water_line
        p = self.p
        # lives
        if s.lives != self.v_lives:
            old, self.v_lives = self.v_lives, s.lives
            self._number(digits_n(s.lives, 2), digits_n(old, 2), p['lives'], wl)
        # score (heavy item: ends the pass)
        if s.score != self.v_score:
            old, self.v_score = self.v_score, s.score
            self._number(digits_n(s.score, 6), digits_n(old, 6), p['score'], wl)
            return self._bar(s)
        # people still to deliver
        if s.left != self.v_left:
            old, self.v_left = self.v_left, s.left
            self._number(digits_n(s.left, 2), digits_n(old, 2), p['left'], wl)
        # player 1 icons
        c = s.crafts
        if c[0].occupied != self.v_icon[0]:
            self.v_icon[0] = c[0].occupied
            self.blit(0xB2 + c[0].occupied, p['icon'], 0xC1, wl)
            self.blit(0xB7 + c[0].dest_number, p['icon_dest'], 0xC1, wl)
            return self._bar(s)
        # player 2 icons
        if self.mode == 2 and c[1].occupied != self.v_icon[1]:
            self.v_icon[1] = c[1].occupied
            self.blit(0xBF + c[1].occupied, p['icon2'], 0xC1, wl)
            self.blit(0xC4 + c[1].dest_number, p['icon2_dest'], 0xC1, wl)
            return self._bar(s)
        # fare of the passenger on board
        fare = None
        if c[0].occupied != 0:
            fare = c[0].fare
        elif self.mode == 2 and c[1].occupied != 0:
            fare = c[1].fare
        elif s.hud_flag != 0:
            fare = 0
        if fare is not None:
            if fare != self.v_fare:
                old, self.v_fare = self.v_fare, fare
                self._number(digits_n(fare, 4), digits_n(old, 4), p['fare'], wl)
            s.hud_flag = 0
        # multiplier
        if s.multiplier != self.v_mult:
            old, self.v_mult = self.v_mult, s.multiplier
            self._number(digits_n(s.multiplier, 2), digits_n(old, 2), p['mult'], wl)
        return self._bar(s)

    # ---------------------------------------------------------- energy bar
    def _bar(self, s):
        """CS:0x5E6A..0x5F1C."""
        if s.energy < 0:
            return
        ax = s.energy // 700                                 # div word [DS:C5]
        old = self.v_bar
        if ax > old:                                         # grow (5ED4)
            last = ax - 1                                    # 5ED8: dec ax
            first_byte, last_byte = old >> 2, last >> 2
            for byte in range(first_byte, last_byte):        # full bytes: mask 0xF
                for pl in range(4):
                    self._bar_px(byte * 4 + pl, BAR_COLOR)
            for pl in range((last & 3) + 1):                 # tail byte: planes 0..bx
                self._bar_px(last_byte * 4 + pl, BAR_COLOR)
        elif ax < old:                                       # shrink (5EA8): one pixel
            self._bar_px(ax, 0)
        self.v_bar = ax

    def _bar_px(self, i, color):
        for row in (3, 4, 5):
            self.pixels[row * WIDTH + BAR_X0 + i] = color

    # -------------------------------------------------------------- output
    def render(self, surface, state=None):
        """Copy the strip to screen lines 192..199 of `surface` (a bytearray of
        320x200 palette indices, or a pygame Surface).  With `state` the
        per-iteration update is run first."""
        if state is not None:
            self.update(state)
        if isinstance(surface, (bytearray, memoryview)):
            for r in range(ROWS):
                surface[(192 + r) * 320:(193 + r) * 320] = self.pixels[r * WIDTH:r * WIDTH + 320]
        else:
            for r in range(ROWS):
                for x in range(320):
                    surface.set_at((x, 192 + r), self.pixels[r * WIDTH + x])
        return surface
