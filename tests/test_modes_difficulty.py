#!/usr/bin/env python3
"""Verification of Game Modes (1P vs 2P) and Difficulty Settings (Novice, Pro, Ace)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from game.session import GameSession
from engine.physics import CRASH_THRESHOLD
from game.passenger import MULTIPLIER_CAP


def test_difficulty_thresholds():
    print("--> 1. Testing Difficulty parameters (Novice, Pro, Ace)...")
    # Verify exact tuple matches with DOS disassembly DS:262E and DS:2628
    assert CRASH_THRESHOLD == (3100, 2300, 1380), f"CRASH_THRESHOLD mismatch: {CRASH_THRESHOLD}"
    assert MULTIPLIER_CAP == (3, 9, 99), f"MULTIPLIER_CAP mismatch: {MULTIPLIER_CAP}"
    
    # Test Novice (diff=0)
    s_novice = GameSession("UGH.EXE", mode=1, level=1, difficulty=0)
    assert s_novice.state.world.difficulty == 0
    assert CRASH_THRESHOLD[s_novice.difficulty] == 3100
    assert MULTIPLIER_CAP[s_novice.difficulty] == 3
    print("    [OK] Novice: Crash threshold 3100 (forgiving), Max Multiplier 3x.")

    # Test Pro (diff=1)
    s_pro = GameSession("UGH.EXE", mode=1, level=1, difficulty=1)
    assert s_pro.state.world.difficulty == 1
    assert CRASH_THRESHOLD[s_pro.difficulty] == 2300
    assert MULTIPLIER_CAP[s_pro.difficulty] == 9
    print("    [OK] Pro: Crash threshold 2300 (standard), Max Multiplier 9x.")

    # Test Ace (diff=2)
    s_ace = GameSession("UGH.EXE", mode=1, level=1, difficulty=2)
    assert s_ace.state.world.difficulty == 2
    assert CRASH_THRESHOLD[s_ace.difficulty] == 1380
    assert MULTIPLIER_CAP[s_ace.difficulty] == 99
    print("    [OK] Ace: Crash threshold 1380 (strict), Max Multiplier 99x.")


def test_two_player_mode_coop():
    print("\n--> 2. Testing 2-Player Cooperative Mode (mode=2, 81 levels)...")
    s2 = GameSession("UGH.EXE", mode=2, level=1, lives=3)
    assert len(s2.levels) == 81, f"Expected 81 two-player levels, got {len(s2.levels)}"
    assert len(s2.state.crafts) == 2, "Expected 2 craft instances for P1 and P2"
    
    # P1 and P2 initial positions from descriptor
    c1, c2 = s2.state.crafts
    assert c1.index == 0
    assert c2.index == 1
    assert (c1.x, c1.y) != (c2.x, c2.y), "Players must start on distinct pads"
    
    # Verify shared lives: crash of P1 decrements session lives
    print("    Testing shared lives on player crash...")
    s2.state.world.trigger_end()
    inputs = [{"up": False, "down": False, "left": False, "right": False, "fire": False}] * 2
    old_state = s2.state
    for _ in range(50):
        s2.tick(inputs)
        if s2.state is not old_state:
            break

    assert s2.lives == 2, f"Expected shared lives to decrement to 2, got {s2.lives}"
    assert len(s2.state.crafts) == 2, "Both players should respawn together"
    print("    [OK] Shared lives pool verified: crash restarts level for both players.")


if __name__ == "__main__":
    test_difficulty_thresholds()
    test_two_player_mode_coop()
    print("\n=======================================================")
    print("  ALL MODES & DIFFICULTY SCENARIOS VERIFIED 100%!      ")
    print("=======================================================")
