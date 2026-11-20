"""Joystick input of UGH.EXE: calibration F=0x6E76, port read F=0x6F9C, poll F=0x2DC0.

The original measures each axis by timing a one-shot RC circuit on game port
0x201: a write starts all four axis timers (bits 0..3 read as 1 while the
timer runs), buttons are bits 4..7 (active low).  Everything is integer code.

State words (DGROUP): 3315 xA, 3317 xB, 3319 yA, 331B yB   measured counts
  3331/3333 x low thresholds (A/B), 3335/3337 x high, 3339/333B y low, 333D/333F y high
  331D buttons A (bits 4,5), 331F buttons B (6,7)   3341 enabled-axis mask
  3343 timeout count  263F/2641 which stick drives player 1 / 2 (negative = keyboard)

PygameStick adapts a host joystick to a Port201 (axis -1..1 -> timer length);
that mapping is the only part that is not from the original.
"""
from __future__ import annotations


class Port201:
    """Game port model.  T[k] = number of time units axis k's timer runs
    (None = no stick: bit stays 1).  `pressed` = bitmask of buttons 4..7 held."""

    def __init__(self, T=(None, None, None, None), pressed=0):
        self.T = list(T)
        self.pressed = pressed
        self.t = 0

    def out(self):                       # OUT 0x201: fire the one-shots
        self.t = 0

    def wait(self, n):                   # time passing without port reads
        self.t += n

    def read(self):                      # IN AL,0x201 (each read takes one unit)
        v = 0xF0 & ~self.pressed
        for k in range(4):
            if self.T[k] is None or self.t < self.T[k]:
                v |= 1 << k
        self.t += 1
        return v


class JoyState:
    def __init__(self):
        self.xA = self.xB = self.yA = self.yB = 0          # 3315,3317,3319,331B
        self.lo_x = [0, 0]; self.hi_x = [0, 0]             # 3331/3333, 3335/3337
        self.lo_y = [0, 0]; self.hi_y = [0, 0]             # 3339/333B, 333D/333F
        self.btn = [0, 0]                                  # 331D, 331F
        self.cfg = 0                                       # 3341
        self.maxcnt = 0xFFFF                               # 3343
        self.sumA = [0, 0]; self.sumB = [0, 0]             # 3329/332D, 332B/332F
        self.presentA = self.presentB = 0                  # 3311, 3313
        self.p1 = self.p2 = 0xFFFF                         # 263F / 2641 initial: keyboard


def read_sticks(port: Port201, v: JoyState):
    """F=0x6F9C. Returns buttons A|B (the routine's final AX)."""
    port.out()
    bx = bp = si = di = 0
    cx = v.maxcnt
    v.btn[0] = (~port.read()) & 0x30
    v.btn[1] = (~port.read()) & 0xC0
    ah = v.cfg & 0xFF
    if ah:
        ah = (~ah) & 0xFF
        while True:
            al = port.read()
            r = ((al | ah) ^ 0xFF) & 0xFF
            if r:                                           # an enabled axis finished
                if r & 1:
                    bx = cx; ah |= 1
                elif r & 2:
                    bp = cx; ah |= 2
                elif r & 4:
                    si = cx; ah |= 4
                else:
                    di = cx; ah |= 8
                if ah == 0xFF:
                    break
                continue                                    # jne 6FCF: no loop decrement
            cx = (cx - 1) & 0xFFFF                          # loop 6FCF
            if cx == 0:
                break
    m = v.maxcnt
    v.yB = (m - di) & 0xFFFF
    v.xB = (m - si) & 0xFFFF
    v.xA = (m - bx) & 0xFFFF
    v.yA = (m - bp) & 0xFFFF
    return v.btn[0] | v.btn[1]


def calibrate(port: Port201, v: JoyState):
    """F=0x6E76: detect sticks, measure the centre, derive the thresholds."""
    v.presentA = v.presentB = 0
    v.cfg = 0
    v.maxcnt = 0xFFFF
    v.sumA = [0, 0]; v.sumB = [0, 0]
    port.out()
    port.wait(0x1600)                                       # 6EA4 delay loop
    al = port.read()
    if al & 3 == 0:                                         # stick A present
        v.presentA = 0xFFFF
        v.p1 = 0
        v.cfg |= 3
        read_sticks(port, v)
        v.sumA = [(v.xA << 1) & 0xFFFF, (v.yA << 1) & 0xFFFF]
        lo = v.xA - (v.xA >> 1)
        v.lo_x[0], v.hi_x[0] = lo, lo + 2 * (v.xA >> 1)
        lo = v.yA - (v.yA >> 1)
        v.lo_y[0], v.hi_y[0] = lo, lo + 2 * (v.yA >> 1)
    if al & 0xC == 0:                                       # stick B present
        v.presentB = 0xFFFF
        v.p2 = 1
        v.cfg |= 0xC
        port.wait(0x1600)                                   # 6F24 delay loop
        read_sticks(port, v)
        v.sumB = [(v.xB << 1) & 0xFFFF, (v.yB << 1) & 0xFFFF]
        lo = v.xB - (v.xB >> 1)
        v.lo_x[1], v.hi_x[1] = lo, lo + 2 * (v.xB >> 1)
        lo = v.yB - (v.yB >> 1)
        v.lo_y[1], v.hi_y[1] = lo, lo + 2 * (v.yB >> 1)
    v.maxcnt = max(v.sumA + v.sumB)                         # 6F70..6F97 (unsigned max)


def poll(port: Port201, v: JoyState, mode: int):
    """F=0x2D9B..0x2E3B: returns per-player dicts for players driven by a stick
    (None = keyboard player)."""
    if v.p1 < 0x8000:                                       # test [263F] / jns
        read_sticks(port, v)
    elif v.p2 >= 0x8000 and mode == 2:
        pass                                                # 2DA6: js -> skip
    elif v.p2 < 0x8000 and mode == 2:
        read_sticks(port, v)
    out = []
    for p in range(mode):
        idx = v.p1 if p == 0 else v.p2
        if idx >= 0x8000:
            out.append(None)
            continue
        i = idx & 1
        x = (v.xA, v.xB)[i]
        y = (v.yA, v.yB)[i]
        sx = lambda w: w - 0x10000 if w & 0x8000 else w
        left = right = up = down = False
        if sx(x) <= sx(v.lo_x[i]):
            left = True
        elif sx(x) >= sx(v.hi_x[i]):
            right = True
        if sx(y) <= sx(v.lo_y[i]):
            up = True
        elif sx(y) >= sx(v.hi_y[i]):
            down = True
        out.append(dict(left=left, right=right, up=up, down=down, fire=bool(v.btn[i])))
    return out


class PygameStick:
    """Host joystick -> Port201 (axis -1..1 maps to a timer of CENTER*(1+axis))."""
    CENTER = 0x180

    def __init__(self, js_a=None, js_b=None):
        self.js = [js_a, js_b]

    def port(self):
        T = [None] * 4
        pressed = 0
        for n, js in enumerate(self.js):
            if js is None:
                continue
            num_axes = js.get_numaxes()
            ax = js.get_axis(0) if num_axes > 0 else 0.0
            ay = js.get_axis(1) if num_axes > 1 else 0.0
            if js.get_numhats() > 0:
                hx, hy = js.get_hat(0)
                if abs(ax) < 0.2 and hx != 0:
                    ax = float(hx)
                if abs(ay) < 0.2 and hy != 0:
                    ay = -float(hy)
            T[2 * n] = max(0, int(self.CENTER * (1 + ax)))
            T[2 * n + 1] = max(0, int(self.CENTER * (1 + ay)))
            num_buttons = js.get_numbuttons()
            if any(js.get_button(b) for b in range(min(num_buttons, 16))):
                pressed |= 0x10 << (2 * n)
        return Port201(T, pressed)
