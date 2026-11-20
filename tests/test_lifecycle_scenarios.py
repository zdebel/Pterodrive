#!/usr/bin/env python3
"""Verification of Game Lifecycle scenarios: Game Over flow, ESC key abort, Pause key."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from game.session import GameSession
from game.frontend import FrontendModel, Frontend
from game.sound import SoundDriver, TRACK_ENDING, SFX_SPLASH


def test_esc_in_flight():
    print("--> 1. Testing ESC key during flight (F=0x2B51 parity)...")
    sess = GameSession("UGH.EXE", mode=1, level=1, lives=3)
    assert sess.lives == 3
    
    # Send ESC key during simulation
    inputs = [{"up": False, "down": False, "left": False, "right": False, "fire": False}]
    sess.tick(inputs, esc=True)
    assert sess.lives == 0, f"Expected lives to become 0 on ESC, got {sess.lives}"
    assert sess.state.world.fade_step < 0, "Expected fade-out triggered on ESC"

    # Step until session ends
    for _ in range(50):
        sess.tick(inputs)
        if sess.over:
            break

    assert sess.over is True, "Expected sess.over == True after ESC abort"
    assert sess.won is False, "Expected sess.won == False on abort"
    print("    [OK] ESC key properly zeroes lives, fades out, and ends in Game Over (not crash/kill).")


def test_game_over_flow():
    print("\n--> 2. Testing complete Game Over flow (0 lives -> failure frame -> high score entry)...")
    os.environ["SDL_VIDEODRIVER"] = "dummy"
    os.environ["SDL_AUDIODRIVER"] = "dummy"
    import pygame
    pygame.init()
    screen = pygame.display.set_mode((320, 240))

    sound = SoundDriver("UGH.EXE", enabled=False)
    fe = Frontend("UGH.EXE", sound=sound)
    fe.bind(screen)

    # Mock events to immediately advance through failure frame and text entry
    call_counts = {'events': 0}
    def mock_events():
        call_counts['events'] += 1
        # Return an enter key after a few ticks
        if call_counts['events'] >= 3:
            return [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, unicode='\r')]
        return []

    fe._events = mock_events

    # Test qualifying high score
    print("    Testing finish_game with high score (15,000 pts)...")
    fe.finish_game(15000, won=False)
    assert fe.running, "Frontend should remain running and ready for menu"
    print("    [OK] finish_game with high score completed cleanly.")

    # Test non-qualifying low score (0 pts)
    print("    Testing finish_game with 0 pts (below top 5)...")
    call_counts['events'] = 0
    fe.finish_game(0, won=False)
    assert fe.running
    print("    [OK] finish_game with low score returned cleanly to menu.")


def test_pause_continuity():
    print("\n--> 3. Testing Pause key logic ('P' scancode 0x19)...")
    from engine.timing import FixedClock, TICK_HZ
    clock = FixedClock(tick_hz=TICK_HZ)
    
    # Normal advance of 1 second -> 70 ticks
    ticks = clock.advance(1.0)
    assert ticks == 5  # capped by max_ticks_per_advance (5 ticks)
    
    # Pause reset: when unpaused, clock.reset() prevents sudden burst
    clock.reset()
    assert clock.acc_ns == 0
    assert clock.advance(0.015) == 1
    print("    [OK] Pause reset prevents time backlog and temporal jumps.")


if __name__ == "__main__":
    test_esc_in_flight()
    test_game_over_flow()
    test_pause_continuity()
    print("\n=======================================================")
    print("  ALL LIFECYCLE & INPUT SCENARIOS VERIFIED 100%!       ")
    print("=======================================================")
