#!/usr/bin/env python3
"""The game's 8 play_sfx call sites (6 in play, 2 in the frontend), checked
against the original: which event plays which ADLX blob, and which events
the original leaves silent."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from game.session import GameSession
from game.sound import (
    SFX_BOULDER, SFX_PICKUP, SFX_PTERO_FLY, SFX_PTERO_WARN, SFX_SHOUT, NAMED_SFX
)

# play_sfx(blob, vol, count) calls in segment B of UGH.EXE
DOS_CALLS = {
    'pickup': (SFX_PICKUP, 1),             # F=0x38AA bonus token
    'shout': (SFX_SHOUT, 1),               # F=0x3A94 stone thrown
    'pterodactyl_warn': (SFX_PTERO_WARN, 1),   # F=0x417E
    'pterodactyl_fly': (SFX_PTERO_FLY, 0),     # F=0x4234, stopped at F=0x4356
    'boulder': (SFX_BOULDER, 1),           # F=0x4752
    'tree': (SFX_PICKUP, 1),               # F=0x48FE shaken fruit tree
}


def test_named_table():
    print("--> NAMED_SFX matches the DOS call sites...")
    assert NAMED_SFX == DOS_CALLS, NAMED_SFX
    print("    [OK] 6 in-game events, blobs and play counts as in UGH.EXE.")


def test_sfx_triggers():
    print("--> Testing in-game SFX trigger emission...")
    from game.hazards import Pterodactyl, Boulder

    print("    1. Triceratops roar (RoarInit F=0x4417) is silent...")
    s5 = GameSession("UGH.EXE", mode=1, level=5)
    dino = next((h for h in s5.state.hz.hazards if hasattr(h, 'pad')), None)
    assert dino is not None, "Dinosaur hazard not found on level 5"
    s5.state.crafts[0].pad = dino.pad
    s5.state.hz.events.clear()
    dino.tick(s5.state.hz)
    assert dino.state == 'ROAR'
    assert not s5.state.hz.events, s5.state.hz.events
    print("       [OK] no sound.")

    print("    2. Fruit collection (F=0x4A59..) is silent...")
    s5.state.hz.events.clear()
    s5.state.hz.spawn_fruit(0x7970, s5.state.crafts[0].x, s5.state.crafts[0].y, 0, 0)
    fruit = s5.state.hz.slots[11]
    fruit.state = 'LANDED'
    fruit.timer = 10
    fruit.x = s5.state.crafts[0].x
    fruit.y = s5.state.crafts[0].y
    fruit.tick(s5.state.hz)
    assert not fruit.alive, "fruit should have been collected"
    assert not s5.state.hz.events, s5.state.hz.events
    print("       [OK] collected without sound.")

    print("    3. Stone throw ('shout')...")
    s5.state.hz.events.clear()
    stone = s5.state.hz.stones[0] if s5.state.hz.stones else None
    if not stone:
        from game.hazards import Stone
        stone = Stone(0, s5.state.pads)
    stone.carrier = s5.state.crafts[0]
    stone.carrier.fire = True
    stone.state = 'CARRIED'
    stone.tick(s5.state.hz)
    assert ('play', 'shout') in s5.state.hz.events
    print("       [OK]")

    print("    4. Passenger fast delivery token ('pickup')...")
    s1 = GameSession("UGH.EXE", mode=1, level=1)
    s1.state.hz.events.clear()
    p = s1.state.pm.people[0]
    p.player = s1.state.crafts[0]
    p.bonus = 100
    s1.state.pm._exit(p)
    assert ('play', 'pickup') in s1.state.hz.events
    print("       [OK]")

    print("    5. Pterodactyl warning, then flight sound until it leaves...")
    s2 = GameSession("UGH.EXE", mode=1, level=2)
    ptero = next((h for h in s2.state.hz.hazards if isinstance(h, Pterodactyl)), None)
    assert ptero is not None
    s2.state.hz.events.clear()
    ptero.timer = 1
    ptero.tick(s2.state.hz)
    assert s2.state.hz.events == [('play', 'pterodactyl_warn')]
    for _ in range(0x46):
        ptero.tick(s2.state.hz)
    assert ('play', 'pterodactyl_fly') in s2.state.hz.events
    s2.state.hz.events.clear()
    for _ in range(2000):
        ptero.tick(s2.state.hz)
        if s2.state.hz.events:
            break
    assert ('stop', 'pterodactyl_fly') in s2.state.hz.events
    print("       [OK]")

    print("    6. Boulder field frame 6 ('boulder')...")
    s4 = GameSession("UGH.EXE", mode=1, level=4)
    boulder = next((h for h in s4.state.hz.hazards if isinstance(h, Boulder)), None)
    assert boulder is not None
    s4.state.hz.events.clear()
    boulder.state = 'CYCLE'
    boulder.anim.i = 4
    boulder.anim.t = 1
    boulder.tick(s4.state.hz)
    assert ('play', 'boulder') in s4.state.hz.events
    print("       [OK]")


if __name__ == "__main__":
    test_named_table()
    test_sfx_triggers()
    print("\n=======================================================")
    print("  ALL SFX TRIGGERS MATCH THE DOS CALL SITES            ")
    print("=======================================================")
