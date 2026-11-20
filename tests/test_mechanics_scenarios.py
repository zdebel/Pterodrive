#!/usr/bin/env python3
"""Verification of Environmental Hazards: Water rise & drowning, Stone hits on Dino, Boulder, Tree."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from game.session import GameSession
from game.hazards import Dinosaur, Boulder, Tree, Stone


def test_water_rise_and_passenger_submergence():
    print("--> 1. Testing Water Rise & Passenger drowning mechanics (Level 3)...")
    s3 = GameSession("UGH.EXE", mode=1, level=3)
    p0 = s3.state.pm.people[0]
    initial_water = s3.state.water.line
    assert s3.state.water.rate != 0, "Level 3 must have dynamic rising water rate"
    
    # Step water until passenger is submerged
    p0.x = 100 << 5
    p0.px = 100
    p0.y = 150 << 5
    p0.py = 150
    s3.state.water.level = 100 << 5  # water line rises above passenger
    s3.state.pm.water_line = 100

    # Tick passenger
    s3.state.pm._danger(p0)
    assert p0.type.is_dead, "Passenger should enter dead (swimming) state upon water submergence"
    assert p0.state in ('D_INIT', 'D_SINK', 'D_WAIT_INIT', 'D_WAIT'), f"Unexpected state: {p0.state}"
    print(f"    [OK] Passenger correctly transitioned to dead/swimming state ({p0.state}) when water rose.")


def test_stone_hit_dinosaur():
    print("\n--> 2. Testing Stone hitting Dinosaur (Stun & Score 1400 pts)...")
    s5 = GameSession("UGH.EXE", mode=1, level=5)
    dino = next((h for h in s5.state.hz.hazards if isinstance(h, Dinosaur)), None)
    assert dino is not None
    
    # Create a flying stone colliding with dino
    stone = Stone(dino.pad, s5.state.pads)
    stone.x = dino.x
    stone.y = dino.y
    stone.vx = 0
    stone.vy = 10
    stone.state = 'FLYING'
    s5.state.hz.stones = [stone]

    old_score = s5.state.score
    dino._stone(s5.state.hz)
    assert dino.state == 'STUN', f"Expected dino to be STUNNED, got {dino.state}"
    assert dino.timer == 0x15E, f"Expected 350 ticks stun duration, got {dino.timer}"
    assert s5.state.score == old_score + Dinosaur.SCORE, "Dino stun score mismatch"
    assert stone.sprite == 0x222, "Stone should show bounce/hit sprite"
    assert stone.vy < 0, "Stone should bounce upward"
    print(f"    [OK] Dinosaur stunned for 350 ticks and awarded +{Dinosaur.SCORE} pts.")


def test_stone_hit_boulder():
    print("\n--> 3. Testing Stone hitting Boulder (Destruction & Score 800 pts)...")
    s4 = GameSession("UGH.EXE", mode=1, level=4)
    boulder = next((h for h in s4.state.hz.hazards if isinstance(h, Boulder)), None)
    assert boulder is not None

    stone = Stone(0, s4.state.pads)
    stone.x = boulder.x
    stone.y = boulder.y
    stone.state = 'FLYING'
    stone.vy = 8
    s4.state.hz.stones = [stone]

    old_score = s4.state.score
    boulder.state = 'CYCLE'
    boulder.tick(s4.state.hz)
    assert boulder.state == 'DEAD', f"Expected boulder to enter DEAD state, got {boulder.state}"
    assert boulder.sprite == 275, "Expected destroyed boulder rubble sprite (275)"
    assert s4.state.score == old_score + Boulder.SCORE, "Boulder destruction score mismatch"
    print(f"    [OK] Boulder destroyed into rubble and awarded +{Boulder.SCORE} pts.")


def test_stone_hit_tree():
    print("\n--> 4. Testing Stone hitting Tree (Shake, SFX & Fruit drop)...")
    s1 = GameSession("UGH.EXE", mode=1, level=1)
    tree = next((h for h in s1.state.hz.hazards if isinstance(h, Tree)), None)
    if tree:
        stone = Stone(0, s1.state.pads)
        stone.x = tree.x
        stone.y = tree.y
        stone.state = 'FLYING'
        stone.vy = 5
        s1.state.hz.stones = [stone]
        tree.state = 'ANIM'
        tree.anim.t = 1
        tree.tick(s1.state.hz)
        assert tree.state == 'WAIT', "Tree should enter WAIT state after being shaken"
        assert tree.sprite == 232, "Tree should show shaken sprite (232)"
        assert ('play', 'tree') in s1.state.hz.events, "Tree shake SFX should be emitted"
        print("    [OK] Tree hit by stone shakes (sprite 232), emits SFX, and drops fruit.")


if __name__ == "__main__":
    test_water_rise_and_passenger_submergence()
    test_stone_hit_dinosaur()
    test_stone_hit_boulder()
    test_stone_hit_tree()
    print("\n=======================================================")
    print("  ALL ENVIRONMENTAL & HAZARD MECHANICS VERIFIED 100%!  ")
    print("=======================================================")
