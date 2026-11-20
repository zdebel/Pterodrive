"""One level of the game, stepped exactly like the DOS main loop.

Loop iteration (F=0x2A2D..0x2D54), only simulation-relevant parts:
  1. DS:27A8 < 0 ?  -> the level is over (loop exits)
  2. DS:27A8 <= 0x100 ?  DS:27A8 += DS:27AA        (screen fade)
  3. HUD function F=0x5D05: score capped at 999999; energy < 0 -> energy = -1 and
     fade-out (DS:27AA = -2, DS:27A8 -= 2) if DS:27AA >= 0
  4. weather particles (graphics only)
  5. water F=0x4ACC
  6. input, Esc (-> same fade-out)
  7. craft physics F=0x2E45 per player, ONLY if DS:27A8 > 0xC0 or DS:27AA <= 0
     (so the craft is frozen for the first 97 iterations of a level)
  8. passengers + stones F=0x3236 (one list, level order)
  9. hazards F=0x4113      10. fruit F=0x492F
 11. craft rotor animation F=0x5F3D (graphics only; effort comes from step 7)
Level start (F=0x5B16): arrays cleared, energy = 0x5A3B, DS:27A8 = 0,
DS:27AA = 2, level loader, then ONE dispatch of hazards (F=0x4113) and of
passengers/stones (F=0x3236) before the loop starts.

Water (F=0x4ACC): DS:0x28FE is the level in 1/32 px; every SECOND iteration the
signed descriptor rate (+23) is added (floored at 0); DS:0x2903 = level >> 5.
Only levels 3, 8, 23, 43 of the 1-player game have a non-zero rate.
"""
from __future__ import annotations

from engine.physics import CollisionWorld, Player
from game.hazards import HazardManager, Stone, hazards_from_level
from game.fade import apply_fade, blank_dac
from game.hud import HudRenderer
from game.weather import Particles, Rng
from game.passenger import Passenger, manager_from_level

SCORE_CAP = 999999                                  # CS:0x5D2E


class WaterLevel:
    def __init__(self, level_fixed: int, rate: int):
        self.level = level_fixed                    # DS:28FE
        self.rate = rate                            # DS:2900 (signed)
        self.phase = 0                              # DS:27CE
        self.redraw = 0                             # DS:27A2 (byte)
        self.changed_from = None                    # row to tint (F=0x4B0F)

    @property
    def line(self) -> int:                          # DS:2903 (pixels)
        return self.level >> 5

    def tick(self):
        self.changed_from = None
        if self.redraw:                             # F=0x4ACC: the call after a
            self.redraw = 0                         # line change does nothing
            return
        old = self.line
        self.phase ^= 1
        if self.phase == 0:                         # add on every 2nd call
            self.level = max(self.level + self.rate, 0)
        if self.line != old:                        # strip redraw: DEC [27A2]
            self.redraw = (self.redraw - 1) & 0xFF
            self.changed_from = old


class GameState:
    def __init__(self, level: dict, world: CollisionWorld, types,
                 n_players=1, difficulty=1, score=0, lives=3, sprite=None,
                 rng=None, water_off=0, palette=None):
        self.level = level
        self.world = world
        self.n = n_players
        sf = [level['start1_fixed'], level['start2_fixed']]
        self.crafts = [Player(i, x=sf[i][0], y=sf[i][1]) for i in range(n_players)]
        self.water = WaterLevel(level['water_level_fixed'], level['water_rate'])
        world.water_line = self.water.line
        world.difficulty = difficulty
        world.energy = 0x5A3B
        world.fade_level, world.fade_step, world.complete = 0, 2, False
        self.palette = palette                      # CS:4681 (768 bytes, 6-bit)
        self.dac = blank_dac()                      # F=0x6C4B at level start
        self.score = score
        self.hud_flag = 0xFF                        # DS:27CD (loader: 0xFF)
        self.finished = False
        self.tick_no = 0

        self.pm = manager_from_level(level, types, self.crafts, difficulty)
        self.pm.add_score = self.add_score
        self.pm.on_complete = world.complete_level
        self.pm.spawn_fruit = lambda *a: self.hz.spawn_fruit(*a)
        self.pm.mult_ref = lambda: self.hz.multiplier
        pads = []
        for q, base in zip(level['pads'], self.pm.pads):
            d = dict(base)
            d['x'] = q['x']
            pads.append(d)
        self.pads = pads
        hz, stones = hazards_from_level(level, pads)
        self.hz = HazardManager(hz, stones, pads, self.crafts, n_players)
        self.hz.add_score = self.add_score
        self.hz.end_cb = world.trigger_end
        self.hz.difficulty = difficulty
        self.hz.lives = lives
        self.pm.sound = self.hz.sound
        self.pm.on_exit = lambda: setattr(self, 'hud_flag', 1)
        # B list = passengers and stones in level-record order (F=0x3236)
        pi, si, self.b_objects = iter(self.pm.people), iter(stones), []
        for e in level['people']:
            if 'trip' in e:
                self.b_objects.append(next(pi))
            elif e['type'] == '0x78DC':
                self.b_objects.append(next(si))
        # background page (F=0x5F22 + 0x621F): tiles, rows below the water get +0x40
        self.bg = None
        if sprite:
            self.bg = self._build_bg(sprite)
        self.wave_c, self.wave_i = 0, 0             # DS:27A3 / 27A4 (F=0x4B69)
        self.rng = rng or Rng()
        self.water_off = water_off                  # DS:2907 (carried between levels)
        self.particles = (Particles(level['weather'], self.rng, self.water.line, water_off)
                          if level['weather'] else None)
        # status strip: HUD variables (F=0x5B16) + panel (F=0x5C29)
        self.hud = HudRenderer(sprite) if sprite else None
        if self.hud:
            self.hud.reset(n_players, lives, self.hz.multiplier, score, self.water.line)
        # level start (F=0x5C20, 0x5C23): one dispatch before the loop
        self._sync_hz()
        self.hz.tick_hazards()
        self._dispatch_b()
        self.pm.refresh()
        self._sync_back()

    def _build_bg(self, sprite):
        bg = bytearray(192 * 320)
        wl = self.water.line
        for r, row in enumerate(self.level['tiles']):
            for c, tid in enumerate(row):
                w, h, px = sprite(tid)
                for j in range(h):
                    y = r * 12 + j
                    tint = y > wl
                    for i in range(w):
                        v = px[j][i]
                        if v:
                            bg[y * 320 + c * 16 + i] = (v | 0x40) if tint else v
        return bg

    # ------------------------------------------------------------ plumbing
    # read-only views used by game.hud.HudRenderer.update (F=0x5D05)
    lives = property(lambda s: s.hz.lives)
    left = property(lambda s: s.pm.remaining)
    mode = property(lambda s: s.n)
    multiplier = property(lambda s: s.hz.multiplier)
    energy = property(lambda s: s.world.energy)
    water_line = property(lambda s: s.water.line)

    def add_score(self, n):
        self.score = (self.score + n) & 0xFFFFFFFF

    def _sync_hz(self):
        self.hz.energy = self.world.energy
        self.hz.water_fixed = self.water.level

    def _sync_back(self):
        self.world.energy = self.hz.energy

    def _dispatch_b(self):
        for o in self.b_objects:
            if isinstance(o, Passenger):
                self.pm.tick_one(o)
            else:
                o.tick(self.hz)

    # ---------------------------------------------------------------- loop
    def tick(self, inputs, esc=False) -> bool:
        """One main-loop iteration.  False = the loop exited (level over)."""
        w = self.world
        if w.fade_level < 0:                        # F=0x2A31
            self.finished = True
            return False
        if w.fade_level <= 0x100:                   # F=0x2A40
            if self.palette is not None:            # F=0x6BE6 with cx = DS:27A8
                apply_fade(self.dac, self.palette, w.fade_level & 0xFFFF)
            w.fade_level += w.fade_step
        if self.score > SCORE_CAP:                  # F=0x5D2E
            self.score = SCORE_CAP
        if w.energy < 0:                            # F=0x5E6A
            w.energy = -1
            w.trigger_end()
        if self.hud:                                # F=0x5D05 (draw part)
            self.hud.update(self)
        self.water.tick()                           # F=0x4ACC
        r = self.water.changed_from
        if self.bg is not None and r is not None and 0 <= r < 192:
            for x in range(320):                    # F=0x4B0F: OR 0x40 into the old line
                self.bg[r * 320 + x] |= 0x40
        w.water_line = self.water.line
        self.pm.water_line = self.water.line
        if esc:                                     # F=0x2B4A
            w.trigger_end()
        if w.fade_level > 0xC0 or w.fade_step <= 0:  # F=0x2B6B
            for c, inp in zip(self.crafts, inputs):
                c.fire = bool(inp.get('fire'))
                c.step(w, up=inp.get('up', False), down=inp.get('down', False),
                       left=inp.get('left', False), right=inp.get('right', False))
        else:
            for c, inp in zip(self.crafts, inputs):
                c.fire = bool(inp.get('fire'))
        self._sync_hz()
        self._dispatch_b()
        self.hz.tick_hazards()
        self.hz.tick_fruit()
        self._sync_back()
        for c in self.crafts:
            c.animate()
        self.pm.refresh()
        if self.particles:                          # F=0x2D48 -> 0x5A28
            self.particles.update(self.water.line, self.water_off)
        self.water_off = ((self.water.line & 0xFF) * 0x60) & 0xFFFF   # F=0x4B8A..4B91
        self.wave_c = (self.wave_c - 1) & 0xFF      # F=0x4B69: pattern phase
        if self.wave_c & 0x80:
            self.wave_c = 6
            self.wave_i -= 1
            if self.wave_i < 0:
                self.wave_i = 2
        self.tick_no += 1
        return True
