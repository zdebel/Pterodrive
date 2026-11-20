"""Integer-only port of the per-player physics of UGH.EXE (routine CS:0x2E45).

Every number below was read from the 16-bit code; DS:/CS: addresses in the
comments refer to the original.  No floating point is used anywhere.

Coordinates: X/Y are 1/32-pixel fixed point (pixel = value >> 5, arithmetic).
Velocities are in 1/2048 px per tick (position += v >> 6 in 1/32 px units).
One call to Player.step() == one 70.09 Hz tick (see engine/timing.py).

Per-player state (word arrays indexed by 2*player, base addresses):
    DS:27D0 x          DS:27D4 y          DS:27D8 x_px      DS:27DC y_px
    DS:2810 vx         DS:2814 vy         DS:27F8 pad       (-1 = flying,
                                                              >=0 = pad index)
    DS:27F4 effort     (pedal effort this tick, drives rotor animation)
    DS:2818 impact     (largest bounce speed this tick, crash test)
    DS:27F0 anim_acc   DS:27E8 anim_frame
Shared: DS:2622 energy (start 0x5A3B, max 0x5A3B), DS:2903 water/floor line
(pixels), DS:28F5 wind mode, DS:2638 difficulty, DS:262E crash thresholds.
"""
from __future__ import annotations

ENERGY_MAX = 0x5A3B
VMAX = 0x1800                       # clamp for vx and vy, symmetric
X_MIN, X_MAX = -0x200, 0x2600       # 1/32 px  (-16 px .. 304 px)
Y_MIN, Y_MAX = -0x260, 0x17E0       # 0xFDA0 .. 0x17E0  (-19 px .. 191 px)
GRAVITY = 0x1B
THRUST_Y = 0x46
THRUST_X = 0x3F
WATER_SINK_DECEL = 0xC1             # vy > 0 in the lower zone
WATER_RISE_ACCEL = 0x15             # vy <= 0 in the lower zone
CRASH_THRESHOLD = (3100, 2300, 1380)    # DS:262E, indexed by difficulty 0..2
EFFORT_MAX = 0x5A
ANIM_PERIOD = 0xC8
ANIM_FRAMES = ((218, 224), (224, 230))  # DS:0CFA.. per player, [min, max)

# probe points (dx, dy) relative to (x_px + 5, y_px); CS:0x3207
PROBE = ((0, 0), (12, 0), (20, 0), (0, 19), (12, 19), (20, 19),
         (0, 6), (20, 6), (0, 12), (20, 12))


def s16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def clamp_v(v: int) -> int:
    v = s16(v)
    if v < 0:
        return -VMAX if v < -VMAX else v
    return VMAX if v > VMAX else v


class CollisionWorld:
    """Solid test = bit 7 of the background-page pixel, exactly like the game.

    CS:0x3207 ORs ten pixel bytes and every caller branches on the SIGN flag
    (`call 3207 / jns`), so only colour indices >= 128 are solid.  The game
    duplicates palette entries 0-127 into 128-255 (CS:0x641A), which is why
    solid and non-solid rock look identical on screen.
    `solid` is a list of rows (>= 192 rows of 320 bools).  Build it with
    from_tiles(): the game blits the 20x16 tile grid (16x12 sprites, colour 0
    transparent) onto a cleared VGA page and probes that page.  Anything the
    game later draws into that page (level decorations) is not modelled.
    """

    def __init__(self, solid, pads=(), water_line=0xC0, wind=0, difficulty=1):
        self.solid = solid
        self.height = len(solid)
        self.width = len(solid[0])
        self.pads = list(pads)      # dicts: x0, x1 (w0,w1), y (3/4 scaled)
        self.water_line = water_line
        self.wind = wind
        self.difficulty = difficulty
        self.energy = ENERGY_MAX    # DS:2622
        # level-fade control block shared by the whole main loop
        self.fade_level = 0         # DS:27A8
        self.fade_step = 2          # DS:27AA  (+2 fade in, -2 fade out)
        self.complete = False       # DS:27CF < 0  (level finished by delivery)

    def trigger_end(self):
        """cmp [27AA],0 / jl skip / [27AA]=-2 / [27A8]-=2  (crash, no energy,
        Esc, pterodactyl hit)."""
        if self.fade_step >= 0:
            self.fade_step = -2
            self.fade_level -= 2

    def complete_level(self):
        """CS:0x3265..0x3277: unconditional, also sets DS:27CF negative."""
        self.fade_step = -2
        self.fade_level -= 2
        self.complete = True

    @property
    def end_level(self):
        return self.fade_step < 0

    @classmethod
    def from_tiles(cls, tiles, sprite, **kw):
        """tiles: 16 rows x 20 ids;  sprite(id) -> (w, h, rows of pixels)."""
        solid = [[False] * 320 for _ in range(192)]
        for r, row in enumerate(tiles):
            for c, tid in enumerate(row):
                w, h, px = sprite(tid)
                for y in range(h):
                    for x in range(w):
                        if px[y][x] & 0x80:
                            solid[r * 12 + y][c * 16 + x] = True
        return cls(solid, **kw)

    def _px(self, x, y):
        if 0 <= y < self.height and 0 <= x < self.width:
            return self.solid[y][x]
        return False                # off-page reads are not modelled

    def hit(self, x_px: int, y_px: int) -> bool:
        """CS:0x3207 - OR of ten pixels, tested by sign (bit 7) by the callers."""
        bx, by = x_px + 5, y_px
        return any(self._px(bx + dx, by + dy) for dx, dy in PROBE)

    def pad_at(self, bottom_y: int, x_px: int):
        """CS:0x3194 - pad whose y == craft bottom and x+16 inside [x0, x1]."""
        for i, p in enumerate(self.pads):
            if p['y'] == bottom_y and p['x0'] <= x_px + 0x10 <= p['x1']:
                return i
        return -1


class Player:
    def __init__(self, index: int = 0, x_px: int = 0, y_px: int = 0, *, x=None, y=None):
        self.index = index
        self.x = (x_px << 5) if x is None else x         # 1/32 px, exact
        self.y = (y_px << 5) if y is None else y
        # DS:27D8 / DS:27DC are separate variables in the original: they are
        # refreshed only at the end of the horizontal / vertical movement code,
        # so anything that changes x or y elsewhere leaves them stale.
        self.x_px = self.x >> 5
        self.y_px = self.y >> 5
        self.vx = 0
        self.vy = 0
        self.pad = -1                   # DS:27F8
        self.effort = 0                 # DS:27F4
        self.impact = 0                 # DS:2818
        self.anim_acc = -1              # DS:27F0
        self.anim_frame = ANIM_FRAMES[index][0]
        # set by game/passenger.py (DS:27FC, 2804, 2808, 280C)
        self.occupied = 0               # passenger kind aboard, 0 = empty
        self.fire = False               # DS:279C bit 7, set by the input layer
        self.dest_number = 0            # pad number shown for the destination
        self.fare = 0
        self.fare_min = 0

    # ---------------------------------------------------------------- tick
    def step(self, world: CollisionWorld, up=False, down=False,
             left=False, right=False):
        world.energy -= 1                                   # 2E45
        self.effort = 0
        self.impact = 0
        cx = s16(self.y_px - world.water_line + 0x12)       # 2E53
        zone = -1 if cx < 0 else (0 if cx == 0 else 1)      # ch: dry/line/water

        if self.pad < 0:                                    # 2E6A: flying
            self._horizontal(world, zone, left, right)
        self._vertical(world, zone, up, down)
        thr = CRASH_THRESHOLD[world.difficulty]
        if self.impact >= thr:                              # 31DF
            world.trigger_end()

    # ----------------------------------------------------------- horizontal
    def _horizontal(self, world, zone, left, right):
        vx = self.vx
        if zone < 0 and world.wind:                         # 2E75: wind
            a = ((world.wind - 1) * 2 - 1) << 5
            vx += a
            self.vy = s16(self.vy + abs(a >> 1))
        if left:
            vx -= THRUST_X
            self.effort += THRUST_X
        elif right:
            vx += THRUST_X
            self.effort += THRUST_X
        vx = clamp_v(vx)
        self.vx = vx
        new = self.x + (vx >> 6)
        if new < X_MIN:
            new, self.vx = X_MIN, 0
        elif new > X_MAX:
            new, self.vx = X_MAX, 0

        if (new >> 5) == self.x_px:                         # 2F4F
            self.x = new
            self.x_px = new >> 5                            # 2FD2
            return
        bp = self.x
        px = self.x_px
        y = self.y_px
        if new <= bp:                                       # moving left
            # CS:0x2F63 steps one pixel left once; the loop-back (jg 0x2F71)
            # lands AFTER that step, so later iterations re-probe the same
            # pixel.  Moving right (jl 0x2F8A) steps every iteration.
            px -= 1
            while True:
                if world.hit(px, y):
                    bp = (bp & ~0x1F) + 0x20
                    self._bounce_x()
                    break
                bp -= 0x20
                if not bp > new:
                    bp = new
                    break
        else:                                               # moving right
            while True:
                px += 1
                if world.hit(px, y):
                    bp = bp & ~0x1F
                    self._bounce_x()
                    break
                bp += 0x20
                if not bp < new:
                    bp = new
                    break
        self.x = bp
        self.x_px = bp >> 5                                 # 2FD2

    def _bounce_x(self):                                    # 2FB1
        self.vx = (-self.vx) >> 1
        self.impact = abs(s16(self.vx * 2))

    # ------------------------------------------------------------- vertical
    def _vertical(self, world, zone, up, down):
        vy = self.vy
        if zone > 0:                                        # 2FEB: lower zone
            world.energy -= 2
            if vy > 0:
                vy -= WATER_SINK_DECEL
            else:
                vy -= WATER_RISE_ACCEL
                self.pad = -1
        elif zone < 0:                                      # 3008: dry
            took_down = False
            if self.pad < 0:
                vy += GRAVITY
                world.energy -= 2
                if down:
                    vy += THRUST_Y
                    self.effort += EFFORT_MAX
                    took_down = True
            if not took_down and up:                        # 3030
                world.energy -= 3
                vy -= THRUST_Y
                self.effort += EFFORT_MAX
                self.pad = -1
        else:                                               # zone == 0: 3030
            if up:
                world.energy -= 3
                vy -= THRUST_Y
                self.effort += EFFORT_MAX
                self.pad = -1
        vy = clamp_v(vy)
        new = self.y + (vy >> 6)
        self.vy = vy
        if new < Y_MIN:
            new, self.vy = Y_MIN, 0
        elif new > Y_MAX:
            new, self.vy = Y_MAX, 0
        if zone > 0:                                        # 309F surface snap
            if (new >> 5) - world.water_line + 0x12 <= 0:
                self.vy = 0
                new = (world.water_line << 5) - 0x240

        if (new >> 5) == self.y_px:                         # 30FF
            self.y = new
            self.y_px = new >> 5                            # 31D8
            return
        bp = self.y
        px = self.x_px
        if new <= bp:                                       # moving up
            # CS:0x3113 steps one row up once; the loop-back (jg 0x3116) skips
            # that step, so later iterations re-probe the same row.  Moving
            # down (jl 0x3130) steps every iteration.
            ypx = self.y_px - 1
            while True:
                if world.hit(px, ypx):
                    bp = (bp & ~0x1F) + 0x20
                    self._bounce_y(world, bp, px)
                    break
                bp -= 0x20
                if not bp > new:
                    bp = new
                    break
        else:                                               # moving down
            ypx = self.y_px
            while True:
                ypx += 1
                if world.hit(px, ypx):
                    bp = bp & ~0x1F
                    self._bounce_y(world, bp, px)
                    break
                bp += 0x20
                if not bp < new:
                    bp = new
                    break
        self.y = bp
        self.y_px = bp >> 5                                 # 31D8

    def _bounce_y(self, world, bp, px):                     # 314A
        self.vy = (-self.vy) >> 1
        m = abs(s16(self.vy * 2))
        if m > self.impact:
            self.impact = m
        if self.vy < 0:                                     # was moving down
            if self.impact < CRASH_THRESHOLD[world.difficulty]:
                i = world.pad_at((bp >> 5) + 0x14, px)
                if i >= 0:                                  # touchdown
                    self.pad = i
                    self.vx = self.vy = 0

    # ------------------------------------------------------------ animation
    def animate(self):                                      # CS:0x5F3D (main loop)
        e = min(self.effort, EFFORT_MAX)
        self.anim_acc -= e
        if self.anim_acc < 0:
            self.anim_acc += ANIM_PERIOD
            self.anim_frame += 1
            lo, hi = ANIM_FRAMES[self.index]
            if self.anim_frame >= hi:
                self.anim_frame = lo


def pads_from_level(level_json: dict):
    """decode_levels.py output -> CollisionWorld.pads (x0/x1 = descriptor w0/w1)."""
    return [dict(x0=p['raw']['w0'], x1=p['raw']['w1'], y=p['y']) for p in level_json['pads']]


if __name__ == '__main__':          # deterministic demo on real level data
    import os, sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from extractor import Game, load_image, load_sprites
    exe = os.path.join(os.path.dirname(__file__), '..', 'UGH.EXE')
    lv = Game(exe).decode_all()[1][0]
    sp = load_sprites(load_image(exe))
    w = CollisionWorld.from_tiles(lv['tiles'], sp, pads=pads_from_level(lv),
                                  water_line=lv['floor_y'], wind=lv['weather'])
    p = Player(0, int(lv['start1']['x']), int(lv['start1']['y']))
    trace = []
    for t in range(400):
        p.step(w, up=(t % 3 == 0))
        trace.append((p.x, p.y, p.vx, p.vy, p.pad))
    print('start px', lv['start1'], 'pads', w.pads)
    print('after 400 ticks', trace[-1], 'energy', w.energy, 'end', w.end_level)
    print('sha-like checksum', sum(a * 31 + b * 17 + c * 7 + d for a, b, c, d, _ in trace) & 0xFFFFFFFF)
