"""Hazards, fruit and the throwable stone of UGH.EXE (integer-only port).

Sources (F = file offset; code pointers in the type structs are CS offsets,
F = CS + 0x1DB0).  The main loop (F=0x2B8E) runs, after the craft physics:
passengers + stones (F=0x3236), hazards (F=0x4113), fruit (F=0x492F).

Hazard types (sprites verified by eye):
    0x7630 pterodactyl (234-257)   0x766C triceratops-like dinosaur (547-598)
    0x76A8 boulder (275-284)       0x76E4 tree (230-232), source of fruit
    0x78DC "stone" (rock with eyes, sprites 0x220-0x222), a kind-4 passenger
Units: x/y/vx fixed point 1/32 px; craft state is engine.physics.Player.
"""
from __future__ import annotations

# ---------------------------------------------------------------- helpers
def f75(v: int) -> int:                         # CS:0x5AFD
    return v - (v >> 2)


def craft_overlap(hx, hy, w0, h0, w1, h1, craft) -> bool:
    """CS:0x40A1 hazard box vs craft origin (type fields +0,+2,+4,+6)."""
    return (craft.y <= hy + (h0 << 5)
            and craft.y >= hy + ((h0 - 2 * h1) << 5) - 0x280
            and craft.x <= hx + ((w0 + w1) << 5) - 0xA0
            and craft.x >= hx + ((w0 - w1) << 5) - 0x340)


def stone_hits(hx, hy, stones):
    """CS:0x3F46 - first flying (falling) stone whose point is near (hx, hy)."""
    for s in stones:
        if s.state != 'FLYING' or s.vy < 0:
            continue
        if (hy <= s.y + 0x100 and hy >= s.y - 0x280
                and hx <= s.x + 0x180 and hx >= s.x - 0x340):
            return s
    return None


class _Anim:
    """DS:2D2F index (bytes, step 2), DS:2D39 delay."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.i, self.t = -2, 1

    def step(self, lst, reload, wrap=True):
        """True if the frame advanced. Returns sprite via .sprite."""
        self.t -= 1
        if self.t != 0:
            return False
        self.t = reload
        self.i += 2
        if (self.i >> 1) >= len(lst):
            if not wrap:
                self.sprite = None
                return True
            self.i = 0
        self.sprite = lst[self.i >> 1]
        return True


def _rng(a, b):
    return list(range(a, b + 1))


# ------------------------------------------------------------ pterodactyl
class Pterodactyl:
    """Type 0x7630.  Box params W0,H0,W1,H1 = 16,11,8,8.  Score 2500."""
    W0, H0, W1, H1 = 0x10, 0x0B, 8, 8
    SCORE = 0x9C4
    LEFT, RIGHT = _rng(234, 245), _rng(246, 257)

    def __init__(self, delay_raw: int, vx: int):
        d = delay_raw
        self.delay = d + (d >> 1)               # level word *1.5 (CS:0x5B0A)
        self.vx = vx
        self.target = 1                          # DS:2D57
        self.x = self.y = 0
        self.state = 'INIT'
        self.timer = 0
        self.anim = _Anim()
        self.sprite = -1
        self.lst = self.LEFT

    def tick(self, g):
        getattr(self, '_' + self.state.lower())(g)

    def _init(self, g):                          # F4129
        self.state = 'WAIT'
        self.anim.reset()
        self.sprite = -1
        self.timer = self.delay

    def _wait(self, g):                          # F414F
        self.timer -= 1
        if self.timer == 0:
            self._warn(g)

    def _warn(self, g):                          # F4160
        self.state = 'WARN2'
        self.timer = 0x46
        g.sound('pterodactyl_warn')              # F417E: blob 0x325B

    def _warn2(self, g):                         # F4189
        self.timer -= 1
        if self.timer == 0:
            self._spawn(g)

    def _spawn(self, g):                         # F419A
        p = self.target ^ 1
        if p >= g.n_players:
            p = 0
        self.target = p
        c = g.crafts[p]
        if c.x < 0x1400:                         # craft on the left half
            if self.vx >= 0:
                self.vx = -self.vx
            self.x, self.lst = 0x27E0, self.LEFT
        else:
            if self.vx < 0:
                self.vx = -self.vx
            self.x, self.lst = -0x3E0, self.RIGHT        # 0xFC20 as a signed word
        y = min(c.y + 0x340, g.water_fixed) - 0x340
        self.y = max(y, -0x80)
        self.state = 'FLY'
        g.sound('pterodactyl_fly')               # F4234: blob 0x3260, loops until F4350

    def _leave(self, g):
        self.state = 'INIT'
        self._init(g)

    def _fly(self, g):                           # F4243
        nx = self.x + self.vx
        if nx <= -0x400 or nx >= 0x2800:
            g.sound_stop('pterodactyl_fly')
            self._leave(g)
            return
        self.x = nx
        if not self.anim.step(self.lst, 4):
            return
        self.sprite = self.anim.sprite
        s = stone_hits(self.x, self.y, g.stones)
        if s is not None:
            g.sound_stop('pterodactyl_fly')
            s.vy = -s.vy
            s.sprite = 0x222
            self._hit(g)
            return
        for i, c in enumerate(g.crafts[:g.n_players]):
            if craft_overlap(self.x, self.y, self.W0, self.H0, self.W1, self.H1, c):
                if i == self.target:
                    g.crash()                    # DS:27AA <- -2
                break

    def _hit(self, g):                           # F42DB
        g.add_score(self.SCORE)
        self.state = 'FALL'
        self.timer = 0
        self.sprite = 234 if self.vx < 0 else 246

    def _fall(self, g):                          # F430E
        nx = self.x + self.vx
        if nx <= -0x400 or nx >= 0x2800:
            self._leave(g)
            return
        self.x = nx
        if self.timer < 0x28:
            self.timer += 1
        ny = self.y + self.timer
        if ny >= 0x1800:
            self._leave(g)
            return
        self.y = ny


# --------------------------------------------------------------- dinosaur
class Dinosaur:
    """Type 0x766C.  Box params 16,22,8,8.  Stun score 1400.  Knocks the craft
    back, never crashes it."""
    W0, H0, W1, H1 = 0x10, 0x16, 8, 8
    SCORE = 0x578
    LISTS = {
        'walk': (_rng(559, 564), _rng(565, 570)),
        'charge': (_rng(547, 552), _rng(553, 558)),
        'recover': ([595] * 6, [596] * 6),
        'roar': (_rng(583, 588), _rng(589, 594)),
        'stun': (_rng(571, 576), _rng(577, 582)),
    }

    def __init__(self, pad: int, vx_raw: int, pads):
        self.pad = pad
        p = pads[pad]
        self.x = (p['x'] - 0x10) << 5
        self.y = (p['y'] - 0x16) << 5
        self.vx = -f75(vx_raw)                   # loader CS:0x5990..0x599E
        self.dir = 0                             # DS:2D57 (0 left, 2 right)
        self.timer = 0
        self.anim = _Anim()
        self.sprite = -1
        self.state = 'WALK_INIT'                 # loader: DS:2CF3 = slot +8 (F4361)

    def _list(self, name):
        return self.LISTS[name][self.dir >> 1]

    def tick(self, g):
        getattr(self, '_' + self.state.lower())(g)

    def _craft_on_pad(self, g):
        for j, c in enumerate(g.crafts[:g.n_players]):
            if c.pad == self.pad:
                return j, c
        return None, None

    def _stone(self, g):
        s = stone_hits(self.x, self.y, g.stones)
        if s is not None:
            s.vy = -s.vy
            s.sprite = 0x222
            self._stun_init(g)
            return True
        return False

    def _walk_init(self, g=None):                # F4361
        self.state = 'WALK'
        self.anim.reset()

    def _walk(self, g):                          # F4379
        self.anim.t -= 1
        if self.anim.t == 0:
            self.x += self.vx
            p = self.x >> 5
            pad = g.pads[self.pad]
            if p < pad['x0'] or p + 32 >= pad['x1']:
                self.vx = -self.vx
                self.dir ^= 2
            self.anim.t = 4
            self.anim.i += 2
            lst = self._list('walk')
            if (self.anim.i >> 1) >= len(lst):
                self.anim.i = 0
            self.sprite = lst[self.anim.i >> 1]
        j, c = self._craft_on_pad(g)
        if c is not None:                        # RoarInit F4417 (silent)
            self.state = 'ROAR'
            self.anim.reset()
            self.timer = 0x8C
            return
        self._stone(g)

    def _face(self, c):
        if self.x < c.x:
            self.dir, self.vx = 2, abs(self.vx)
        else:
            self.dir, self.vx = 0, -abs(self.vx)

    def _roar(self, g):                          # F4431
        self.timer -= 1
        if self.timer == 0:
            self.state = 'CHARGE'                # ChargeInit F44DE
            self.anim.reset()
            self.timer = 0
            return
        if self.anim.step(self._list('roar'), 5):
            self.sprite = self.anim.sprite
        if self._stone(g):
            return
        j, c = self._craft_on_pad(g)
        if c is None:
            self._walk_init()
            return
        self._face(c)

    def _charge(self, g):                        # F44F8
        if self.anim.step(self._list('charge'), 4):
            self.sprite = self.anim.sprite
        if self._stone(g):
            return
        j, c = self._craft_on_pad(g)
        if c is None:
            self.state = 'RECOVER'               # RecoverInit F45E0
            self.anim.reset()
            return
        self._face(c)
        self.timer += self.dir - 1
        self.x += self.vx + self.timer
        for k, cr in enumerate(g.crafts[:g.n_players]):
            if craft_overlap(self.x, self.y, self.W0, self.H0, self.W1, self.H1, cr):
                v = self.vx + self.timer
                cr.y -= 0x20
                cr.vx = v << 5
                cr.vy = v << 4
                cr.pad = -1
                self.state = 'RECOVER'           # HitInit F463F
                self.anim.reset()
                break

    def _recover(self, g):                       # F45F4
        if self.anim.step(self._list('recover'), 4, wrap=False):
            if self.anim.sprite is None:
                self._walk_init()
                return
            self.sprite = self.anim.sprite
        self._stone(g)

    def _stun_init(self, g):                     # StunInit F469E
        g.add_score(self.SCORE)
        self.state = 'STUN'
        self.anim.reset()
        self.timer = 0x15E

    def _stun(self, g):                          # F46C4
        self.timer -= 1
        if self.timer == 0:
            self._walk_init()
            return
        if self.anim.step(self._list('stun'), 4):
            self.sprite = self.anim.sprite



# ---------------------------------------------------------------- boulder
class Boulder:
    """Type 0x76A8.  Not a crusher: a periodic pull/push field (+-41 vx per
    tick) that a falling stone destroys for 800 points."""
    SCORE = 0x320
    LIST = _rng(275, 284)

    def __init__(self, x_px: int, y_raw: int):
        self.x = x_px << 5
        self.y = f75(y_raw << 5) - 0x2C0
        self.state = 'INIT'                      # loader: slot +0x10 (F470B)
        self.anim = _Anim()
        self.sprite = -1
        self.timer = 0

    def tick(self, g):
        if self.state == 'INIT':                 # F470B
            self.state = 'CYCLE'
            self.anim.reset()
        elif self.state == 'CYCLE':
            if self.anim.step(self.LIST, 15):
                self.sprite = self.anim.sprite
                if self.anim.i == 6:
                    g.sound('boulder')
            for c in g.crafts[:g.n_players]:
                if (self.y - 256 <= c.y <= self.y + 416
                        and self.x - 3648 <= c.x <= self.x):
                    c.vx += 0x29 if self.anim.i <= 4 else -0x29
            s = stone_hits(self.x, self.y, g.stones)
            if s is not None:
                s.vy = -s.vy
                g.add_score(self.SCORE)
                self.sprite = 275
                self.timer = 0x15E
                self.state = 'DEAD'
        else:
            self.timer -= 1
            if self.timer == 0:
                self.state = 'CYCLE'
                self.anim.reset()


# ------------------------------------------------------------------- tree
class Tree:
    """Type 0x76E4.  Shaken by a falling stone; drops one fruit per shake."""
    LIST = [230] * 13 + [231]

    def __init__(self, pad: int, fruit_types: list, pads):
        p = pads[pad]
        self.x = (p['x'] - 0x10) << 5
        self.y = (p['y'] - 0x18) << 5
        self.fruit = list(fruit_types)           # per-tree list (type ptrs)
        self.k = 0
        self.state = 'INIT'                      # loader: slot +8 (F4837)
        self.anim = _Anim()
        self.sprite = -1
        self.timer = 0

    def tick(self, g):
        if self.state == 'INIT':                 # F4837
            self.state = 'ANIM'
            self.anim.reset()
        elif self.state == 'ANIM':
            if self.anim.step(self.LIST, 6):
                self.sprite = self.anim.sprite
                s = stone_hits(self.x, self.y, g.stones)
                if s is not None:
                    s.vy = (-s.vy) >> 1
                    s.sprite = 0x222
                    self._shaken(g, s)
        elif self.state == 'WAIT':
            self.timer -= 1
            if self.timer == 0:
                self.state = 'INERT' if self.k >= len(self.fruit) else 'ANIM'

    def _shaken(self, g, s):                     # F48BC
        self.state, self.timer, self.sprite = 'WAIT', 0xD2, 232
        if self.k < len(self.fruit):
            t = self.fruit[self.k]
            self.k += 1
            g.spawn_fruit(t, s.x, s.y, s.vx, (-s.vy) >> 2)
            g.sound('tree')                      # F48FE: blob 0x3274


# ------------------------------------------------------------------ fruit
# type ptr -> (launch +0xC, value +0xE, sprite +0x10, pickup kind +0x12)
# kind 0: +value energy (cap 0x5A3B)   kind 1: +value lives (cap 99)
# kind >= 2: multiplier DS:263D += 1 while below DS:2628[difficulty]
MULT_CAP = (3, 9, 99)                           # DS:2628
FRUIT_TYPES = {
    0x7970: (14, 3000, 285, 0), 0x7984: (21, 7000, 286, 0), 0x7998: (28, 5000, 287, 0),
    0x79AC: (35, 5000, 288, 0), 0x79C0: (42, 8000, 289, 0), 0x79D4: (50, 7000, 290, 0),
    0x79E8: (57, 3000, 291, 0), 0x79FC: (64, 5000, 292, 0), 0x7A10: (71, 10000, 293, 0),
    0x7A38: (0x39, 0, 297, 2),                  # bonus token from a fast delivery
}
FRUIT_W, FRUIT_H = 8, 16
ENERGY_MAX = 0x5A3B


class Fruit:
    def __init__(self, t, ox, oy, vx, launch_in):
        self.type = t
        launch, self.energy, self.sprite, self.kind = FRUIT_TYPES[t]
        self.x = ox - (FRUIT_W << 5)
        self.y = oy - (FRUIT_H << 4)
        self.vx = vx
        self.vy = -(launch_in + launch)
        self.state = 'FALL'
        self.timer = 0
        self.alive = True

    def tick(self, g):
        if self.state == 'FALL':                 # F4994
            self.x += self.vx
            if self.x <= -0x200 or self.x >= 0x2800:
                self.alive = False
                return
            self.vy += 3
            if self.vy < 0:
                self.y += self.vy
                return
            yn = self.y + self.vy
            if yn >= 0x1800:
                self.alive = False
                return
            cx = (self.x >> 5) + FRUIT_W
            for p in g.pads:
                # F=0x4A02: `cmp cx,y / jge skip` -> old bottom strictly above
                if ((self.y >> 5) + FRUIT_H < p['y'] <= (yn >> 5) + FRUIT_H
                        and p['x0'] <= cx and cx - 1 <= p['x1']):
                    self.y = (p['y'] - FRUIT_H) << 5
                    self.state, self.timer = 'LANDED', 0x230
                    return
            self.y = yn
        else:                                    # F4A59
            self.timer -= 1
            if self.timer == 0:
                self.alive = False
                return
            for c in g.crafts[:g.n_players]:
                if (self.y + ((FRUIT_H - 26) << 5) <= c.y <= self.y + (FRUIT_H << 5)
                        and self.x + ((FRUIT_W - 3) << 5) - 0x340 <= c.x
                        <= self.x + ((FRUIT_W + 3) << 5) - 160):
                    if self.kind == 0:
                        g.energy = min(g.energy + self.energy, ENERGY_MAX)
                    elif self.kind == 1:
                        g.lives = min(g.lives + self.energy, 99)
                    elif g.multiplier < MULT_CAP[g.difficulty]:
                        g.multiplier += 1
                    self.alive = False
                    return


# ------------------------------------------------------------- stone (0x78DC)
class Stone:
    """Passenger kind 4: sits on a pad, is picked up by overlap while the craft
    is empty, thrown with Fire (level-triggered), flies ballistically."""
    W, H, M4, M6 = 8, 11, 3, 2

    def __init__(self, pad: int, pads):
        p = pads[pad]
        self.x = (p['x'] - 8) << 5
        self.y = (p['y'] - 0x0B) << 5
        self.vx = self.vy = 0
        self.state = 'INIT'                      # B-list object: slot +0x10 (F39BF)
        self.sprite = -1
        self.carrier = None
        self.anim = _Anim()

    def tick(self, g):
        getattr(self, '_' + self.state.lower())(g)

    def _init(self, g):                          # F39BF
        self.state = 'IDLE'
        self.anim.reset()

    def _idle(self, g):                          # F39D7 (box = CS:0x4026)
        self.sprite = 0x220
        for c in g.crafts[:g.n_players]:
            if (self.y + (self.H << 5) >= c.y
                    and (self.H - 2 * self.M6) * 32 + self.y - 0x280 <= c.y
                    and ((self.W + self.M4) << 5) + self.x - 0xA0 >= c.x
                    and ((self.W - self.M4) << 5) + self.x - 0x340 <= c.x):
                if c.occupied == 0:
                    c.occupied = 4               # F39F8
                    c.dest_number = 7
                    self.carrier = c
                    self.sprite = -1
                    self.state = 'CARRIED'
                break

    def _carried(self, g):                       # F3A1B
        if self.carrier.fire:
            self._release(g)

    def _release(self, g=None):                  # F3A31
        c = self.carrier
        c.occupied = 0
        c.dest_number = 0
        self.vx = c.vx >> 5
        self.vy = c.vy >> 5
        self.x = c.x + 0x200 - (self.W << 5)
        self.y = c.y + 0x140 - (self.H << 4)
        self.sprite = 0x221
        self.state = 'FLYING'
        if g is not None:
            g.sound('shout')

    def _flying(self, g):                        # F3A9E (state id 0x1CEE)
        nx = self.x + self.vx
        if nx <= -0x200 or nx >= 0x2800:
            return self._dead()
        self.x = nx
        self.vy += 2
        if self.vy < 0:
            self.y += self.vy
            return
        yn = self.y + self.vy
        if yn >= 0x1800:
            return self._dead()
        cx = (self.x >> 5) + self.W
        for p in g.pads:
            if ((self.y >> 5) + self.H <= p['y'] <= (yn >> 5) + self.H
                    and p['x0'] <= cx <= p['x1']):
                self.y = (p['y'] - self.H) << 5
                self._init(g)                    # jmp [si+0x10] (F39BF)
                return
        self.y = yn

    def _dead(self, g=None):
        self.sprite = -1
        self.state = 'DEAD'


# ---------------------------------------------------------------- manager
class HazardManager:
    """Runs hazards (F=0x4113), then fruit (F=0x492F)."""

    def __init__(self, hazards, stones, pads, crafts, n_players=1):
        self.hazards = hazards
        self.stones = stones
        self.pads = pads
        self.crafts = crafts
        self.n_players = n_players
        self.slots = [None] * 12                 # DS:2D9B... (F=0x492F / 0x4946)
        self.score = 0
        self.energy = ENERGY_MAX
        self.water_fixed = 0xC0 << 5
        self.end_level = False
        self.end_cb = None                      # world.trigger_end (conditional)
        self.multiplier = 1                     # DS:263D
        self.lives = 3                          # DS:263C
        self.difficulty = 1                     # DS:2638
        self.events = []

    # callbacks used by the entities
    def add_score(self, n): self.score = (self.score + n) & 0xFFFFFFFF
    def crash(self):
        self.end_level = True
        if self.end_cb:
            self.end_cb()

    def sound(self, name): self.events.append(('play', name))
    def sound_stop(self, name): self.events.append(('stop', name))
    @property
    def fruit(self):                             # active fruit, high slot first
        return [f for f in reversed(self.slots) if f is not None]

    def spawn_fruit(self, t, ox, oy, vx, launch):
        for k in range(11, -1, -1):              # F=0x4946: scan 0x16 down to 0
            if self.slots[k] is None:
                self.slots[k] = Fruit(t, ox, oy, vx, launch)
                return                           # no free slot: dropped

    def tick_hazards(self):                      # F=0x4113
        for h in self.hazards:
            h.tick(self)

    def tick_fruit(self):                        # F=0x492F: slot 11 .. 0
        for k in range(11, -1, -1):
            f = self.slots[k]
            if f is not None:
                f.tick(self)
                if not f.alive:
                    self.slots[k] = None

    def tick(self):                              # stand-alone use
        for s in self.stones:
            s.tick(self)
        self.tick_hazards()
        self.tick_fruit()


def hazards_from_level(level: dict, pads):
    """decode_levels level dict -> (hazards, stones) (no state is shared)."""
    hz, stones = [], []
    for e in level['hazards']:
        t = e['type']
        if t == '0x7630':
            hz.append(Pterodactyl(e['raw'][1], _s16(e['raw'][2])))
        elif t == '0x766C':
            hz.append(Dinosaur(e['pad'], e['raw'][2], pads))
        elif t == '0x76A8':
            hz.append(Boulder(e['raw'][0], e['raw'][1]))
        elif t == '0x76E4':
            hz.append(Tree(e['pad'], [int(x, 16) for x in e.get('pointer_list', [])
                                      if int(x, 16) in FRUIT_TYPES], pads))
    for pe in level['people']:
        if pe['type'] == '0x78DC':
            stones.append(Stone(pe['pad'], pads))
    return hz, stones


def _s16(v):
    return v - 0x10000 if v & 0x8000 else v
