#!/usr/bin/env python3
"""Verification of the Outro / Ending sequence, level 69/81 victory, and music."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from game.session import GameSession
from game.frontend import FrontendModel, FrontendAssets
from game.sound import TRACK_TITLE, TRACK_INGAME, TRACK_ENDING, SFX_SPLASH


def test_level_counts():
    print("--> 1. Verifying final level counts matching DOS DGROUP (0x2624)...")
    assets = FrontendAssets("UGH.EXE")
    import struct
    mode1_max, mode2_max = struct.unpack_from("<HH", assets.ds, 0x2624)
    assert mode1_max == 69, f"Mode 1 max level mismatch: {mode1_max} != 69"
    assert mode2_max == 81, f"Mode 2 max level mismatch: {mode2_max} != 81"
    print(f"    Mode 1 (1-player): {mode1_max} levels")
    print(f"    Mode 2 (2-player): {mode2_max} levels")


def test_session_victory():
    print("\n--> 2. Verifying GameSession victory triggers on final levels...")
    # Mode 1: winning level 69
    s1 = GameSession("UGH.EXE", mode=1, level=69)
    s1.state.world.complete = True
    s1.state.world.fade_step = -1
    inputs1 = [{"up": False, "down": False, "left": False, "right": False, "fire": False}]
    for _ in range(50):
        s1.tick(inputs1)
        if s1.over:
            break
    assert s1.over and s1.won, f"Mode 1 level 69 win failed: over={s1.over}, won={s1.won}"
    print("    [OK] Mode 1: Level 69 completion properly flagged won=True, over=True")

    # Mode 2: winning level 81
    s2 = GameSession("UGH.EXE", mode=2, level=81)
    s2.state.world.complete = True
    s2.state.world.fade_step = -1
    inputs2 = inputs1 * 2
    for _ in range(50):
        s2.tick(inputs2)
        if s2.over:
            break
    assert s2.over and s2.won, f"Mode 2 level 81 win failed: over={s2.over}, won={s2.won}"
    print("    [OK] Mode 2: Level 81 completion properly flagged won=True, over=True")


def test_ending_scene():
    print("\n--> 3. Verifying Ending Scene ('ending' / script offset 0x1020)...")
    fm = FrontendModel("UGH.EXE")
    base, script, water = fm.scene("ending")
    assert len(script.objects) == 15, f"Unexpected script object count: {len(script.objects)}"
    assert water == 180, f"Unexpected water line: {water}"
    
    ticks = 0
    fade_out_tick = None
    for t in range(1, 3000):
        fo = script.tick()
        if fo and fade_out_tick is None:
            fade_out_tick = t
        if script.done:
            ticks = t
            break
    assert ticks == 2313, f"Expected ending script to complete at tick 2313, got {ticks}"
    print(f"    [OK] Ending scene ran 15 animated objects across {ticks} ticks (~33.0 s).")


def test_music_selection():
    print("\n--> 4. Verifying authentic DOS music track mapping...")
    print(f"    TRACK_TITLE  = 0x{TRACK_TITLE:04X} (Menu, Attract loop, game_intro/apple scene)")
    print(f"    TRACK_INGAME = 0x{TRACK_INGAME:04X} (In-game gameplay levels)")
    print(f"    TRACK_ENDING = 0x{TRACK_ENDING:04X} (Ending animation 0x2652 & Highscore entry 0x203C)")
    assert TRACK_TITLE == 0x3637
    assert TRACK_INGAME == 0x3444
    assert TRACK_ENDING == 0x3279
    print("    [OK] Exact match with disassembly far call addresses.")


if __name__ == "__main__":
    test_level_counts()
    test_session_victory()
    test_ending_scene()
    test_music_selection()
    print("\n=======================================================")
    print("  ALL ENDING / OUTRO VERIFICATION TESTS PASSED (100%)  ")
    print("=======================================================")
