"""Game session: level progression, lives, score carry-over, telemetry.

End-of-level handling of the DOS main routine (F=0x2D57..0x2D97):
  * DS:27CF < 0 (a delivery finished the level)  -> next level, or finished
  * otherwise (crash / no energy / Esc)          -> DS:263C (lives, starts at 3)
        is decremented; 0 means game over, else the SAME level restarts
  * every (re)start refills energy, keeps the score, resets the multiplier.
The fade between screens is part of the loop itself (see game/state.py).
Esc sets the lives byte to 0 before triggering the fade-out (F=0x2B51).
"""
from __future__ import annotations

from engine.physics import CollisionWorld, pads_from_level
from extractor import Game, load_image, load_sprites
from game.fade import dup_palette
from game.passenger import load_types
from game.state import GameState

START_LIVES = 3
CSV_HEADER = ('tick,player_x_sub,player_y_sub,vx,vy,energy,water_line_sub,'
              'active_passengers,active_hazards,score')
_IDLE_PASSENGER = ('INIT', 'WAIT_SPAWN', 'DEAD_IDLE', 'DEAD_REVIVE')
_IDLE_HAZARD = ('INIT', 'WAIT', 'WARN2', 'DEAD')


class GameSession:
    def __init__(self, exe: str, mode: int = 1, level: int = 1,
                 lives=START_LIVES, difficulty: int = 1):
        self.exe = exe
        self.mode = mode
        self.difficulty = difficulty
        self.levels = Game(exe).decode_all()[mode]
        self.types = load_types(exe)
        img = load_image(exe)
        self.sprite = load_sprites(img)
        self.palette = dup_palette(img[0x2BC6 * 16:0x2BC6 * 16 + 384])
        self.level_no = level
        self.lives = lives
        self.total_ticks = 0                # main-loop iterations executed
        self.over = False                   # game over / game finished
        self.won = False
        self.state = None
        self.score_carry = 0
        self.rng = None                     # carried between levels (game/weather.py)
        self.water_off = 0                  # DS:2907
        self.multiplier = 1                 # DS:263D: reset only on game start / crash restart
        self._start()

    def _start(self):
        lv = self.levels[self.level_no - 1]
        world = CollisionWorld.from_tiles(
            lv['tiles'], self.sprite, pads=pads_from_level(lv),
            water_line=lv['floor_y'], wind=lv['weather'])
        self.level = lv
        self.state = GameState(lv, world, self.types, n_players=self.mode,
                               difficulty=self.difficulty,
                               score=self.score_carry, lives=self.lives,
                               sprite=self.sprite, rng=self.rng,
                               water_off=self.water_off, palette=self.palette)
        self.state.hz.multiplier = self.multiplier
        self.state.hud.reset(self.mode, self.lives, self.multiplier, self.score_carry,
                             self.state.water.line)
        self.rng = self.state.rng

    # ------------------------------------------------------------ queries
    @property
    def score(self):
        return self.state.score

    @property
    def energy(self):
        return self.state.world.energy

    @property
    def outcome(self):                      # for the renderer / UI
        w = self.state.world
        if w.fade_step < 0:
            return 'complete' if w.complete else 'crash'
        return None

    def telemetry_row(self):
        s, c = self.state, self.state.crafts[0]
        ap = sum(1 for p in s.pm.people if p.state not in _IDLE_PASSENGER)
        ah = sum(1 for h in s.hz.hazards if h.state not in _IDLE_HAZARD)
        return (self.total_ticks, c.x, c.y, c.vx, c.vy, s.world.energy,
                s.water.level, ap, ah, s.score)

    # --------------------------------------------------------------- tick
    def tick(self, inputs, esc=False):
        """One main-loop iteration (or the level change when the loop exits)."""
        if self.over:
            return
        if esc:
            self.lives = 0                  # F=0x2B51: DS:263C := 0
            self.state.hz.lives = 0
        if self.state.tick(inputs, esc=esc):
            self.total_ticks += 1
            return
        self._level_over()

    def abort(self):                        # kept for callers: Esc
        self.lives = 0
        self.state.world.trigger_end()

    def _level_over(self):
        self.score_carry = self.state.score
        self.lives = self.state.lives               # fruit may have added lives
        self.multiplier = self.state.hz.multiplier  # survives a completed level
        self.water_off = self.state.water_off
        if self.state.world.complete:
            if self.level_no >= len(self.levels):
                self.over = self.won = True
                return
            self.level_no += 1
        else:
            self.lives -= 1
            if self.lives <= 0:
                self.over = True
                return
            self.multiplier = 1                     # F=0x2D7A: only on a crash restart
        self._start()
