"""Keyboard input replicating the DOS scancode state machine of UGH.EXE.

Original (INT 09h ISR at CS:0x6317, table at DS:0x281C, state at DS:0x278C):
  * the ISR reads port 0x60 and matches raw set-1 scancodes against a table of
    6-byte entries  [sc0, sc1, 0xFF, slot, value, alive]  with a small
    prefix-matching state machine (index in DS:0x2647), so E0-prefixed
    (extended) keys are two-byte sequences;
  * a completed match writes `value` (0xFF = make, 0x00 = break) into the
    word-array slot DS:0x278C+slot, but only if that player's controller flag
    (DS:0x263F / 0x2641) is negative (keyboard enabled);
  * the game reads these slots every tick as *level* signals (held = true);
    nothing is edge-triggered in the input layer.

Slots (offset from 0x278C, player = (slot >> 1) & 1):
    0x00/0x02 up   0x04/0x06 down   0x08/0x0A left   0x0C/0x0E right
    0x10/0x12 fire/action           0x14 ignored (E0 2A / E0 AA fake shift)
The loop also special-cases the last raw scancode: 0x19 (P) pauses (any key
resumes), 0x01 (Esc) aborts the level.  In the menu F4 (0x3E) toggles
1/2-player mode; F3/F5 (0x3D/0x3F) are other menu keys.

Effect on the ship per tick (CS:0x2E80-0x3030): left/right add -/+0x3F to the
horizontal velocity, up/down change the vertical velocity by -/+0x46 on top of
a constant +0x1B pull; velocities clamp to +/-0x1800 and position moves by
velocity>>6 in 1/32 pixel units.  Fire is only tested as "held".
"""
from __future__ import annotations

UP, DOWN, LEFT, RIGHT, FIRE = 0, 1, 2, 3, 4
_SLOT_NAMES = {0x00: (0, UP), 0x02: (1, UP), 0x04: (0, DOWN), 0x06: (1, DOWN),
               0x08: (0, LEFT), 0x0A: (1, LEFT), 0x0C: (0, RIGHT), 0x0E: (1, RIGHT),
               0x10: (0, FIRE), 0x12: (1, FIRE)}

# DS:0x281C  (sc0, sc1, slot, value)   sc1 = None for single-byte keys
KEY_TABLE = [
    (0xE0, 0x2A, 0x14, 0xFF), (0xE0, 0xAA, 0x14, 0xFF),
    (0xE0, 0x48, 0x00, 0xFF), (0x11, None, 0x02, 0xFF), (0x48, None, 0x02, 0xFF),
    (0xE0, 0xC8, 0x00, 0x00), (0x91, None, 0x02, 0x00), (0xC8, None, 0x02, 0x00),
    (0xE0, 0x50, 0x04, 0xFF), (0x2C, None, 0x06, 0xFF), (0x50, None, 0x06, 0xFF),
    (0xE0, 0xD0, 0x04, 0x00), (0xAC, None, 0x06, 0x00), (0xD0, None, 0x06, 0x00),
    (0xE0, 0x4B, 0x08, 0xFF), (0x1E, None, 0x0A, 0xFF), (0x4B, None, 0x0A, 0xFF),
    (0xE0, 0xCB, 0x08, 0x00), (0x9E, None, 0x0A, 0x00), (0xCB, None, 0x0A, 0x00),
    (0xE0, 0x4D, 0x0C, 0xFF), (0x1F, None, 0x0E, 0xFF), (0x4D, None, 0x0E, 0xFF),
    (0xE0, 0xCD, 0x0C, 0x00), (0x9F, None, 0x0E, 0x00), (0xCD, None, 0x0E, 0x00),
    (0xE0, 0x1D, 0x10, 0xFF), (0x52, None, 0x10, 0xFF), (0x1D, None, 0x12, 0xFF),
    (0xE0, 0x1C, 0x12, 0xFF), (0xE0, 0x9D, 0x10, 0x00), (0xD2, None, 0x10, 0x00),
    (0x9D, None, 0x12, 0x00), (0xE0, 0x9C, 0x12, 0x00),
]


class DosKeyboard:
    """Feed raw set-1 scancode bytes with `scancode()`; read `held(p, action)`."""

    def __init__(self, keyboard_enabled=(True, True)):
        self.enabled = list(keyboard_enabled)       # DS:0x263F / 0x2641
        self.slots = {s: 0 for s in range(0, 0x16, 2)}   # DS:0x278C ...
        self.last_scancode = 0                      # DS:0x2643 / CS:0x4509
        self._idx = 0                               # DS:0x2647
        self._dead = [False] * len(KEY_TABLE)       # entry byte 5 == 0xFF
        self.pause_event = False                    # P (0x19) make seen
        self.quit_event = False                     # Esc (0x01) make seen
        self.any_make = False                       # any make code seen
        self.on_byte = None                         # optional hook(byte)

    def scancode(self, byte: int):
        """One INT 09h: process one byte read from port 0x60."""
        self.last_scancode = byte
        if self.on_byte:
            self.on_byte(byte)
        if byte < 0x80 and byte != 0xE0:
            self.any_make = True
            if byte == 0x19:
                self.pause_event = True
            elif byte == 0x01:
                self.quit_event = True
        cont = False
        for n, (a, b, slot, val) in enumerate(KEY_TABLE):
            if self._dead[n]:
                continue
            want = a if self._idx == 0 else b
            if want is None or byte != want:
                self._dead[n] = True
                continue
            nxt = b if self._idx == 0 else None
            if nxt is None:                         # sequence complete
                if self.enabled[(slot >> 1) & 1]:
                    self.slots[slot] = val
                self._reset()
                return
            cont = True
        if cont:
            self._idx += 1
        else:
            self._reset()

    def _reset(self):
        self._idx = 0
        self._dead = [False] * len(KEY_TABLE)

    def held(self, player: int, action: int) -> bool:
        slot = {UP: 0x00, DOWN: 0x04, LEFT: 0x08, RIGHT: 0x0C, FIRE: 0x10}[action] + 2 * player
        return self.slots[slot] != 0

    @property
    def pause_pressed(self):  return self.last_scancode == 0x19
    @property
    def quit_pressed(self):   return self.last_scancode == 0x01


# --------------------------------------------------------------- pygame glue
def _pygame_map():
    import pygame as pg
    E0 = 0xE0
    return {                                  # pygame key -> make byte sequence
        pg.K_UP: (E0, 0x48), pg.K_DOWN: (E0, 0x50), pg.K_LEFT: (E0, 0x4B),
        pg.K_RIGHT: (E0, 0x4D), pg.K_RCTRL: (E0, 0x1D), pg.K_KP0: (0x52,),
        pg.K_w: (0x11,), pg.K_z: (0x2C,), pg.K_a: (0x1E,), pg.K_s: (0x1F,),
        pg.K_KP8: (0x48,), pg.K_KP2: (0x50,), pg.K_KP4: (0x4B,), pg.K_KP6: (0x4D,),
        pg.K_LCTRL: (0x1D,), pg.K_KP_ENTER: (E0, 0x1C),
        pg.K_p: (0x19,), pg.K_ESCAPE: (0x01,),
        pg.K_F3: (0x3D,), pg.K_F4: (0x3E,), pg.K_F5: (0x3F,),
    }


class PygameKeyboard(DosKeyboard):
    """Translate pygame KEYDOWN/KEYUP into the scancodes the DOS ISR saw."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self._map = _pygame_map()

    def handle_event(self, event) -> bool:
        import pygame as pg
        if event.type not in (pg.KEYDOWN, pg.KEYUP):
            return False
        seq = self._map.get(event.key)
        if not seq:
            return False
        if event.type == pg.KEYUP:                      # break = make | 0x80
            seq = seq[:-1] + (seq[-1] | 0x80,)
        for b in seq:
            self.scancode(b)
        return True


# ------------------------------------------------- deterministic record/replay
import json


class InputRecorder:
    """Records raw scancode bytes with the logic tick they arrived before.

    File format: JSON array of [tick, scancode_byte].  The byte includes the
    0xE0 prefix and the 0x80 break bit, i.e. exactly what port 0x60 delivered
    to the original INT 09h handler.  tick = number of logic ticks already
    simulated when the byte arrived.
    """

    def __init__(self, kb: DosKeyboard):
        self.events = []
        self.tick = 0
        kb.on_byte = lambda b: self.events.append([self.tick, b])

    def save(self, path):
        with open(path, 'w') as f:
            json.dump(self.events, f)


class InputPlayer:
    """Feeds recorded bytes straight into a DosKeyboard (no OS events)."""

    def __init__(self, path_or_events):
        if isinstance(path_or_events, str):
            with open(path_or_events) as f:
                path_or_events = json.load(f)
        self.events = [(int(t), int(b)) for t, b in path_or_events]
        self.i = 0

    @property
    def last_tick(self):
        return self.events[-1][0] if self.events else 0

    @property
    def done(self):
        return self.i >= len(self.events)

    def feed(self, kb: DosKeyboard, tick: int):
        while self.i < len(self.events) and self.events[self.i][0] <= tick:
            kb.scancode(self.events[self.i][1])
            self.i += 1


if __name__ == '__main__':                             # self-test, no pygame
    kb = DosKeyboard()
    for b in (0xE0, 0x4B): kb.scancode(b)              # left arrow down
    assert kb.held(0, LEFT) and not kb.held(1, LEFT)
    for b in (0x1F,): kb.scancode(b)                   # S -> player 2 right
    assert kb.held(1, RIGHT)
    for b in (0xE0, 0xCB): kb.scancode(b)              # left arrow up
    assert not kb.held(0, LEFT)
    for b in (0xE0, 0x2A, 0xE0, 0x48): kb.scancode(b)  # fake shift + up
    assert kb.held(0, UP)
    rec = DosKeyboard(); r = InputRecorder(rec)
    r.tick = 3
    for b in (0xE0, 0x4B): rec.scancode(b)
    pl = InputPlayer(r.events); kb2 = DosKeyboard()
    pl.feed(kb2, 2); assert not kb2.held(0, LEFT)
    pl.feed(kb2, 3); assert kb2.held(0, LEFT)
    print('input state machine OK')
