"""Passenger state machine of UGH.EXE (handlers at CS:0x324C-0x38D9).

Integer-only port.  Names: `x`,`y` are 1/32 px fixed point (DS:2AED/2B0D),
`px`,`py` the pixel copies (DS:2B2D/2B6D, refreshed once per tick after every
handler has run, as the main loop does).  A passenger's current state is a
function pointer (DS:2A0D) that is read from a table in its *type struct*; the
slots are listed in STATE_SLOTS.

    INIT --trip ends--> DEAD_IDLE (and DS:28F1 -= 1; 0 => level complete)
      |
      v trip step: src, dest, delay*1.5
    WAIT_SPAWN --timer 0 and src pad free--> SPAWN (one tick)
      v
    ARRIVE (arrival animation) --> IDLE (one tick) --> WALK_IDLE
    WALK_IDLE: walk to pad wait-x; craft landed on src pad & empty --> HAIL
    HAIL (one tick; bubble sprite 0x10C+dest, patience 0x8C) --> CALLING
    CALLING: craft still there & empty: animate, patience-- ; 0 --> APPROACH_START
             craft left / not empty --> LOST_CRAFT
    LOST_CRAFT (bubble 0x112, patience 0x8C) --> LOST_WAIT
    LOST_WAIT: patience 0: craft there & empty --> HAIL else --> IDLE
    APPROACH_START --> APPROACH: walk to craft (px+w-16 == craft x_px) --> BOARD
    BOARD (one tick): craft.occupied = kind, fare = fare0, bonus timer 200,
                      pad slot freed --> RIDING
    RIDING: fare-- down to fare_min, bonus timer--; craft landed on dest --> EXIT
    EXIT (one tick): score += fare * multiplier --> WALK_OUT
    WALK_OUT: walk to dest pad exit-x --> ARRIVED (one tick) --> FINISH_ANIM
    FINISH_ANIM: end of animation: trip += 2 words --> INIT (src = old dest)
Any of WALK_IDLE/CALLING/LOST_WAIT/APPROACH: the passenger sinking below the
water line, or a *flying* craft overlapping its hit box (CS:0x4026), swaps its
type for the 'dead' (swimming) variant at type+0x48 and runs that variant's own
machine (F=0x3B58..0x3F45: sink, wait at the surface 700 ticks, can be
rescued by a craft hovering at the water line, or drifts away).
Type 0x78DC (kind 4, the throwable stone) has no trip chain; it lives in
game/hazards.py (class Stone) and is dispatched in the same B-list order.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

# state slots inside the type struct (byte offsets) -> state names
STATE_SLOTS = {
    0x08: 'INIT', 0x0A: 'WAIT_SPAWN', 0x0C: 'SPAWN', 0x0E: 'ARRIVE',
    0x10: 'IDLE', 0x12: 'WALK_IDLE', 0x14: 'HAIL', 0x16: 'CALLING',
    0x18: 'LOST_CRAFT', 0x1A: 'LOST_WAIT', 0x1C: 'APPROACH_START',
    0x1E: 'APPROACH', 0x20: 'BOARD', 0x22: 'RIDING', 0x24: 'EXIT',
    0x26: 'WALK_OUT', 0x28: 'ARRIVED', 0x2A: 'FINISH_ANIM', 0x2C: 'DEAD_IDLE',
}
# the same type-struct slots name different handlers in the 'dead' (swimming)
# variants of the types (0x776A, 0x77FE, 0x7892); shared slots keep their name
DEAD_SLOTS = {
    0x08: 'D_INIT', 0x0A: 'D_SINK', 0x0C: 'D_OUT_INIT', 0x0E: 'D_OUT',
    0x10: 'D_WAIT_INIT', 0x12: 'D_WAIT', 0x14: 'HAIL', 0x16: 'D_CALLING',
    0x18: 'LOST_CRAFT', 0x1A: 'D_LOST_WAIT', 0x1C: 'APPROACH_START',
    0x1E: 'D_APPROACH', 0x20: 'D_BOARD', 0x22: 'DEAD_IDLE', 0x24: 'DEAD_IDLE',
    0x26: 'DEAD_IDLE', 0x28: 'DEAD_IDLE', 0x2A: 'DEAD_IDLE', 0x2C: 'DEAD_IDLE',
}
NORMAL_SLOTS = dict(STATE_SLOTS)
DEAD_PTRS = (0x776A, 0x77FE, 0x7892)
BUBBLE_BASE = 0x10C             # speech bubble with the destination pad number
BUBBLE_LOST = 0x112
BUBBLE_MAX = 0x111
CALL_PATIENCE = 0x8C            # DS:2A2D
BONUS_TICKS = 0xC8              # DS:2C8D
REVIVE_TICKS_DEFAULT = 0x2BC
MULTIPLIER_CAP = (3, 9, 99)     # DS:2628


@dataclass
class PassengerType:
    ptr: int
    w: int
    h: int
    hit_x: int                  # +4
    hit_y: int                  # +6
    step: int                   # +0x3E ticks per animation step (= walk speed)
    fare0: int                  # +0x40
    fare_min: int               # +0x42
    revive: int                 # +0x44
    kind: int                   # +0x46
    dead_ptr: int               # +0x48
    anim: dict = field(default_factory=dict)   # idle/call/walk_l/walk_r/arrive/finish

    @property
    def is_dead(self):
        return self.ptr in DEAD_PTRS

    @classmethod
    def from_ds(cls, ds: bytes, ptr: int):
        w = lambda o: struct.unpack_from('<H', ds, ptr + o)[0]

        def lst(o):
            a, out = w(o), []
            while struct.unpack_from('<H', ds, a)[0] != 0xFFFF:
                out.append(struct.unpack_from('<H', ds, a)[0])
                a += 2
            return out
        return cls(ptr, w(0), w(2), w(4), w(6), w(0x3E), w(0x40), w(0x42),
                   w(0x44), w(0x46), w(0x48),
                   dict(idle=lst(0x2E), call=lst(0x30), walk_l=lst(0x32),
                        walk_r=lst(0x34), arrive=lst(0x36), finish=lst(0x38)))


def load_types(exe_path: str):
    """{ptr: PassengerType} for the three trip types and their dead variants."""
    from extractor import load_image
    img = load_image(exe_path)
    ds = img[0x5C09 * 16:0x5C09 * 16 + 0x7E2E]
    return {p: PassengerType.from_ds(ds, p)
            for p in (0x7720, 0x77B4, 0x7848, 0x776A, 0x77FE, 0x7892)}


class Passenger:
    def __init__(self, t: PassengerType, trip: list, trip_index: int = 0):
        self.type = t
        self.trip = trip                # words: p0 d0 p1 d1 ... pn dn 0xFFFF
        self.ti = trip_index            # DS:29ED (word index of current src)
        self.state = 'INIT'
        self.src = self.dest = 0        # DS:2A4D / 2A6D
        self.timer = 0                  # DS:2ACD
        self.x = self.y = 0
        self.px = self.py = 0
        self.anim_i = 0                 # DS:2BAD (byte index, steps of 2)
        self.anim_t = 0                 # DS:2BCD
        self.sprite = -1                # DS:2BED
        self.bubble = -1                # DS:2C2D
        self.aux = 0                    # DS:2A2D (patience / walk flag / craft)
        self.bonus = 0                  # DS:2C8D
        self.player = None
        self.revive_t = 0
        self.squashed = False
        self.vy = 0                     # DS:2C6D


class PassengerManager:
    def __init__(self, types, pads, passengers, players, water_line,
                 difficulty=1, multiplier=1):
        """pads: dicts w/ id, y, x0, x1, exit_x (descriptor w3), wait_x (w4)."""
        self.types = types
        self.pads = pads
        self.people = passengers
        self.players = players
        self.water_line = water_line
        self.difficulty = difficulty
        self._multiplier = multiplier       # DS:263D (see property)
        self.score = 0                      # DS:261E/2620 (32 bit)
        self.remaining = sum(1 for p in passengers)   # DS:28F1 (people w/ trips)
        self.level_done = False             # DS:28F1 reached 0
        self.on_complete = None             # callback: CS:0x3265 fade-out + DS:27CF
        self.spawn_fruit = lambda *a: None  # set by GameState (fruit slots)
        self.on_exit = None                 # DS:27CD := 1 (CS:0x38D5)
        self.mult_ref = None                # callable -> DS:263D
        self.slot = [None] * len(pads)      # DS:2999 pad occupancy

    @property
    def multiplier(self):
        return self.mult_ref() if self.mult_ref else self._multiplier

    # ---------------------------------------------------------------- tick
    def add_score(self, n):
        self.score = (self.score + n) & 0xFFFFFFFF

    def tick_one(self, p):
        getattr(self, '_' + p.state.lower())(p)

    def refresh(self):
        for p in self.people:               # main loop F=0x2B99: only if visible
            if p.sprite != -1:
                p.px, p.py = p.x >> 5, p.y >> 5

    def tick(self):
        for p in self.people:
            self.tick_one(p)
        self.refresh()

    # -------------------------------------------------------------- helpers
    def _goto(self, p, state):
        p.state = state

    def _reset_anim(self, p):
        p.anim_i, p.anim_t = -2, 1

    def _step(self, p):
        """dec DS:2BCD; True on the tick the animation index advances."""
        p.anim_t -= 1
        if p.anim_t != 0:
            return False
        p.anim_t = p.type.step
        p.anim_i += 2
        return True

    def _frame(self, p, name, wrap=True):
        lst = p.type.anim[name]
        i = p.anim_i >> 1
        if i >= len(lst):
            if not wrap:
                return None
            p.anim_i = 0
            i = 0
        p.sprite = lst[i]
        return lst[i]

    def _craft_on(self, p, need_empty):
        for i, c in enumerate(self.players):
            if c.pad == p.src and (not need_empty or c.occupied == 0):
                return i, c
        return None, None

    def _overlap(self, p):
        """CS:0x4026 -> first craft whose box overlaps; None if none."""
        t = p.type
        for c in self.players:
            if ((t.h << 5) + p.y >= c.y
                    and ((t.h - 2 * t.hit_y) << 5) + p.y - 0x280 <= c.y
                    and ((t.w + t.hit_x) << 5) + p.x - 0xA0 >= c.x
                    and ((t.w - t.hit_x) << 5) + p.x - 0x340 <= c.x):
                return c
        return None

    def _sunk(self, p):
        return (p.type.h >> 1) + p.py - self.water_line >= 0

    def _free_slot(self, p):
        if self.slot[p.src] is p:
            self.slot[p.src] = None
        p.bubble = -1

    def _goto(self, p, off):
        """DS:2A0D := type[off]  (the slot names depend on the type variant)."""
        p.state = (DEAD_SLOTS if p.type.is_dead else NORMAL_SLOTS)[off]

    def _to_dead(self, p):
        """mov si,[si+0x48] / mov [29AD],si / jmp [si+8]   (CS:0x33BD etc.)"""
        p.type = self.types[p.type.dead_ptr]
        self._d_init(p)

    def _sunk_check(self, p):
        """F=0x3398 pattern: below the water line -> start sinking."""
        if (p.type.h >> 1) + p.py - self.water_line >= 0:
            p.y = (self.water_line + p.type.h) << 5
            self._free_slot(p)
            self._to_dead(p)
            return True
        return False

    def _squash_check(self, p):
        """CS:0x4026 then `cmp ax,0xFFFF`: a flying craft overlaps the passenger."""
        c = self._overlap(p)
        if c is not None and c.pad == -1:
            self._to_dead(p)
            return True
        return False

    def _danger(self, p):
        return self._sunk_check(p) or self._squash_check(p)

    # -------------------------------------------------------- state handlers
    def _init(self, p):                                     # 324C
        d = p.trip[p.ti + 2]
        if d == 0xFFFF:
            if self.remaining > 0:
                self.remaining -= 1
                if self.remaining == 0:
                    self.level_done = True
                    if self.on_complete:
                        self.on_complete()
            p.sprite = -1
            p.state = 'DEAD_IDLE'
            return
        p.dest = d
        p.src = p.trip[p.ti]
        delay = p.trip[p.ti + 1]
        p.timer = delay + (delay >> 1)
        p.state = 'WAIT_SPAWN'

    def _wait_spawn(self, p):                               # 32B9
        p.sprite = -1
        if p.timer != 0:
            p.timer -= 1
            if p.timer != 0:
                return
        if self.slot[p.src] is None:
            self._spawn(p)

    def _spawn(self, p):                                    # 32EB
        pad = self.pads[p.src]
        self.slot[p.src] = p
        p.py = pad['y'] - p.type.h                  # DS:2B6D / 2B2D are set here too
        p.px = pad['exit_x'] - p.type.w
        p.y = p.py << 5
        p.x = p.px << 5
        self._reset_anim(p)
        p.state = 'ARRIVE'

    def _arrive(self, p):                                   # 3332
        p.anim_t -= 1
        if p.anim_t != 0:
            return
        p.anim_t = p.type.step
        p.anim_i += 2
        lst = p.type.anim['arrive']
        if (p.anim_i >> 1) >= len(lst):
            self._idle(p)
        else:
            p.sprite = lst[p.anim_i >> 1]

    def _idle(self, p):                                     # 3364
        self._reset_anim(p)
        p.aux = -1
        p.bubble = -1
        p.state = 'WALK_IDLE'

    def _walk(self, p, target, step_tick, left, right):
        """Shared walking step: returns -1 left, +1 right, 0 arrived."""
        here = p.px + p.type.w
        if here == target:
            return 0
        lst = p.type.anim['walk_l' if here > target else 'walk_r']
        if (p.anim_i >> 1) >= len(lst):
            p.anim_i = 0
        p.sprite = lst[p.anim_i >> 1]
        if step_tick:
            p.x += -0x20 if here > target else 0x20
        return -1 if here > target else 1

    def _walk_idle(self, p):                                # 3387
        if self._sunk_check(p):
            return
        pad = self.pads[p.src]
        if self._step(p):
            here = p.px + p.type.w
            if here == pad['wait_x']:
                if p.aux != 1:
                    p.anim_i = 0
                lst = p.type.anim['idle']
                if (p.anim_i >> 1) >= len(lst):
                    p.anim_i = 0
                p.sprite = lst[p.anim_i >> 1]
                p.aux = 1
            else:
                p.aux = 0
                lst = p.type.anim['walk_l' if here > pad['wait_x'] else 'walk_r']
                if (p.anim_i >> 1) >= len(lst):
                    p.anim_i = 0
                p.sprite = lst[p.anim_i >> 1]
                p.x += -0x20 if here > pad['wait_x'] else 0x20
        if self._squash_check(p):
            return
        i, c = self._craft_on(p, need_empty=True)
        if c is not None:
            self._hail(p)

    def _hail(self, p):                                     # 34A6
        self._reset_anim(p)
        p.bubble = min(BUBBLE_BASE + p.dest, BUBBLE_MAX)
        p.aux = CALL_PATIENCE
        self._goto(p, 0x16)

    def _call_anim(self, p):
        if self._step(p):
            self._frame(p, 'call')

    def _calling(self, p):                                  # 34DA
        if self._danger(p):
            return
        i, c = self._craft_on(p, need_empty=False)
        if c is None or c.occupied != 0:
            self._lost(p)
            return
        self._call_anim(p)
        p.aux -= 1
        if p.aux == 0:
            self._approach_start(p)

    def _approach_start(self, p):                           # 3678
        self._reset_anim(p)
        p.bubble = -1
        self._goto(p, 0x1E)

    def _lost(self, p):                                     # 3596
        self._reset_anim(p)
        p.bubble = BUBBLE_LOST
        p.aux = CALL_PATIENCE
        self._goto(p, 0x1A)

    def _lost_wait(self, p):                                # 35BA
        if self._danger(p):
            return
        self._call_anim(p)
        p.aux -= 1
        if p.aux == 0:
            i, c = self._craft_on(p, need_empty=True)
            if c is not None:
                self._hail(p)
            else:
                self._idle(p)

    def _approach(self, p):                                 # 3696
        if self._danger(p):
            return
        i, c = self._craft_on(p, need_empty=False)
        if c is None:
            self._lost(p)
            return
        step_tick = self._step(p)
        here = p.px + p.type.w - 0x10
        if here == c.x_px:
            self._board(p, i, c)
            return
        lst = p.type.anim['walk_l' if here > c.x_px else 'walk_r']
        if (p.anim_i >> 1) >= len(lst):
            p.anim_i = 0
        p.sprite = lst[p.anim_i >> 1]
        if step_tick:
            p.x += -0x20 if here > c.x_px else 0x20

    def _board(self, p, i, c):                              # 37AB
        c.fare, c.fare_min = p.type.fare0, p.type.fare_min
        c.occupied = p.type.kind
        c.dest_number = self.pads[p.dest]['id']
        p.player = c
        p.sprite = -1
        p.bonus = BONUS_TICKS
        self.slot[p.src] = None
        p.state = 'RIDING'

    def _riding(self, p):                                   # 37F2
        c = p.player
        if c.fare > c.fare_min:
            c.fare -= 1
        if p.bonus > 0:
            p.bonus -= 1
        if c.pad == p.dest:
            self._exit(p)

    def _exit(self, p):                                     # 382E
        c = p.player
        c.occupied = 0
        c.dest_number = 0
        p.x = ((0x10 - p.type.w) << 5) + c.x
        self.add_score(c.fare * self.multiplier)
        if p.bonus != 0 and self.multiplier < MULTIPLIER_CAP[self.difficulty]:
            # CS:0x3867..0x38A8: spawn a 0x7A38 token (fruit slot) at the craft
            self.spawn_fruit(0x7A38, c.x + 0x200, c.y + 0x140, 0, 0)
            if hasattr(self, 'sound') and self.sound:
                self.sound('pickup')
        pad = self.pads[p.dest]
        p.y = (pad['y'] - p.type.h) << 5
        self._reset_anim(p)
        if self.on_exit:
            self.on_exit()
        p.state = 'WALK_OUT'

    def _walk_out(self, p):                                 # 38D9
        pad = self.pads[p.dest]
        if not self._step(p):
            return
        here = p.px + p.type.w
        if here == pad['exit_x']:
            self._arrived(p)
            return
        lst = p.type.anim['walk_l' if here > pad['exit_x'] else 'walk_r']
        if (p.anim_i >> 1) >= len(lst):
            p.anim_i = 0
        p.sprite = lst[p.anim_i >> 1]
        p.x += -0x20 if here > pad['exit_x'] else 0x20
        if (p.x >> 5) + p.type.w == pad['exit_x']:
            self._arrived(p)

    def _arrived(self, p):                                  # 396E
        self._reset_anim(p)
        p.state = 'FINISH_ANIM'

    def _finish_anim(self, p):                              # 3986
        if not self._step(p):
            return
        lst = p.type.anim['finish']
        i = p.anim_i >> 1
        if i < len(lst):
            p.sprite = lst[i]
        if i + 1 >= len(lst):
            p.ti += 2                                       # DS:29ED += 4 bytes
            self._init(p)                                   # jmp [si+8]: same tick

    def _dead_idle(self, p):                                # 39BE: ret
        pass

    # ------------------------------------------------ 'dead' (swimming) variant
    def _anim_loop(self, p, name):
        """The 3B9E/3D0C idiom: look the entry up every tick, wrap at 0xFFFF."""
        lst = p.type.anim[name]
        if (p.anim_i >> 1) >= len(lst):
            p.anim_i = 0
        p.sprite = lst[p.anim_i >> 1]

    def _dstep(self, p):
        p.anim_t -= 1
        if p.anim_t == 0:
            p.anim_t = p.type.step
            p.anim_i += 2

    def _keep_surface(self, p):
        if self.water_line != p.py:
            p.y = (self.water_line - p.type.h) << 5

    def _d_init(self, p, g=None):                           # 3B58
        p.state = 'D_SINK'
        self._free_slot(p)
        p.bubble = -1
        p.anim_i, p.anim_t, p.vy = -2, 1, 0

    def _d_sink(self, p):                                   # 3B85
        self._dstep(p)
        self._anim_loop(p, 'idle')
        cx = p.py - p.type.h - self.water_line
        dry = cx <= 0
        vy = p.vy
        if dry:
            vy += 0x27
        elif vy > 0:
            vy -= 0x175
        else:
            vy -= 0x5C
        vy = max(-0x1800, min(0x1800, vy))
        p.vy = vy
        p.y += vy >> 6
        if vy < 0 and ((p.y >> 5) - p.type.h) <= self.water_line:
            p.y = (self.water_line - p.type.h) << 5
            self._d_wait_init(p)

    def _d_out_init(self, p):                               # 3C4C
        p.state = 'D_OUT'
        p.bubble = -1
        p.anim_i, p.anim_t, p.vy = -2, 1, 0

    def _d_out(self, p):                                    # 3C70
        self._dstep(p)
        self._anim_loop(p, 'idle')
        p.vy += 0x27
        yn = p.y + (p.vy >> 6)
        if (yn & 0xFFFF) >= 0x1800:
            p.state = 'DEAD_IDLE'
            p.sprite = -1
        else:
            p.y = yn

    def _d_wait_init(self, p):                              # 3CD4
        p.state = 'D_WAIT'
        p.anim_i, p.anim_t = -2, 1
        p.timer = p.type.revive

    def _d_wait(self, p):                                   # 3CF3
        self._dstep(p)
        self._anim_loop(p, 'call')
        self._keep_surface(p)
        if p.type.ptr != 0x77FE:
            for c in self.players:
                if c.vy == 0 and c.occupied == 0 and \
                        c.y_px - self.water_line + 0x12 == 0:
                    self._hail(p)
                    return
        p.timer -= 1
        if p.timer != 0:
            return
        self._d_out_init(p)

    def _d_calling(self, p):                                # 3D92
        self._keep_surface(p)
        ok = any(c.y_px - self.water_line + 0x12 == 0 for c in self.players)
        if not ok:
            self._lost(p)
            return
        self._dstep(p)
        self._anim_loop(p, 'call')
        p.aux -= 1
        if p.aux == 0:
            self._approach_start(p)

    def _d_lost_wait(self, p):                              # 3E18
        self._keep_surface(p)
        self._dstep(p)
        self._anim_loop(p, 'call')
        p.aux -= 1
        if p.aux == 0:
            self._d_out_init(p)

    def _d_approach(self, p):                               # 3E71
        self._keep_surface(p)
        c = None
        for cr in self.players:
            if cr.occupied == 0 and cr.y_px - self.water_line + 0x12 == 0:
                c = cr
                break
        if c is None:
            self._lost(p)
            return
        step_tick = self._dstep_flag(p)
        here = p.px + p.type.w - 0x10
        if here == c.x_px:
            self._d_board(p, c)
            return
        lst = p.type.anim['walk_l' if here > c.x_px else 'walk_r']
        if (p.anim_i >> 1) >= len(lst):
            p.anim_i = 0
        p.sprite = lst[p.anim_i >> 1]
        if step_tick:
            p.x += -0x20 if here > c.x_px else 0x20

    def _dstep_flag(self, p):
        p.anim_t -= 1
        if p.anim_t != 0:
            return False
        p.anim_t = p.type.step
        p.anim_i += 2
        return True

    def _d_board(self, p, c):                               # 3790 -> 37BD
        c.fare, c.fare_min = p.type.fare0, p.type.fare_min
        p.type = self.types[p.type.dead_ptr]                # back to the live type
        c.occupied = p.type.kind
        c.dest_number = self.pads[p.dest]['id']
        p.player = c
        p.sprite = -1
        p.bonus = BONUS_TICKS
        self.slot[p.src] = None
        p.state = 'RIDING'

    def _dead_idle(self, p):                                # ret
        pass


def manager_from_level(level: dict, types: dict, players, difficulty=1):
    """Build a PassengerManager from a decode_levels.py level dict."""
    pads = [dict(id=q['id'], y=q['y'], x0=q['raw']['w0'], x1=q['raw']['w1'],
                 exit_x=q['stand_x'], wait_x=q['raw']['w4']) for q in level['pads']]
    people = []
    for e in level['people']:
        if 'trip' not in e:
            continue                                        # type 0x78DC
        people.append(Passenger(types[int(e['type'], 16)], e['trip']['raw']))
    m = PassengerManager(types, pads, people, players,
                         water_line=level['floor_y'], difficulty=difficulty)
    m.remaining = level['required_people']      # DS:28F1 = descriptor +8
    return m


if __name__ == '__main__':
    import os, sys
    root = os.path.join(os.path.dirname(__file__), '..')
    sys.path.insert(0, root)
    from extractor import Game
    from engine.physics import Player
    exe = os.path.join(root, 'UGH.EXE')
    types = load_types(exe)
    lv = Game(exe).decode_all()[1][0]
    craft = Player(0, 0, 0)
    m = manager_from_level(lv, types, [craft])
    print('people', [(p.type.kind, p.trip) for p in m.people])
    log = []
    for t in range(6000):
        m.tick()
        p = m.people[0]
        if not log or log[-1][1] != p.state:
            log.append((t, p.state))
        # scripted craft: park on the pad the passenger starts from, then
        # "teleport" to its destination pad while it rides
        if p.state in ('WALK_IDLE', 'CALLING', 'APPROACH', 'LOST_WAIT', 'HAIL'):
            craft.pad = p.src
            craft.x = (m.pads[p.src]['wait_x'] - 30) << 5
        elif p.state == 'RIDING':
            craft.pad = p.dest
        else:
            craft.pad = -1
            craft.x = 0
        craft.y = 100 << 5
        if m.level_done:
            break
    print(log[:22])
    print('score', m.score, 'done', m.level_done, 'tick', t)
