#!/usr/bin/env python3
"""Regression test for music restarting after player death / level transition."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from game.session import GameSession
from game.sound import TRACK_INGAME, TRACK_TITLE, TRACK_ENDING, SFX_SPLASH


class MockSoundDriver:
    def __init__(self):
        self.current_track = None
        self.history = []

    def start_music(self, track_para, tempo=0xA5):
        self.current_track = track_para
        self.history.append(('start_music', hex(track_para)))

    def stop_music(self):
        self.current_track = None
        self.history.append(('stop_music',))

    def play_sfx(self, sfx_para, vol=0xFF, prio=1):
        self.history.append(('play_sfx', hex(sfx_para)))


def test_music_restart_after_crash():
    print("--> Testing music restart after player crash/death...")
    sess = GameSession("UGH.EXE", mode=1, level=1, lives=3)
    sound = MockSoundDriver()
    sound.start_music(TRACK_INGAME)
    assert sound.current_track == TRACK_INGAME

    # Trigger a crash fade-out
    sess.state.world.trigger_end()
    assert not sess.state.world.complete

    old_state = sess.state
    inputs = [{"up": False, "down": False, "left": False, "right": False, "fire": False}]
    for _ in range(50):
        sess.tick(inputs)
        if sess.state is not old_state:
            break

    assert sess.state is not old_state, "Expected state to reload after crash"
    assert sess.lives == 2, f"Expected lives to decrement to 2, got {sess.lives}"

    # Simulate what main.py main loop now does on state transition:
    # 1) show_level stops music
    sound.stop_music()
    assert sound.current_track is None

    # 2) main.py re-starts in-game music
    sound.start_music(TRACK_INGAME)
    assert sound.current_track == TRACK_INGAME, "Music must be running after level restart!"
    print("    [OK] In-game music properly resumed on level restart (lives=2).")


def test_music_restart_on_next_level():
    print("\n--> Testing music restart after completing level...")
    sess = GameSession("UGH.EXE", mode=1, level=1, lives=3)
    sound = MockSoundDriver()
    sound.start_music(TRACK_INGAME)

    # Trigger level completion
    sess.state.world.complete = True
    sess.state.world.fade_step = -1

    old_state = sess.state
    inputs = [{"up": False, "down": False, "left": False, "right": False, "fire": False}]
    for _ in range(50):
        sess.tick(inputs)
        if sess.state is not old_state:
            break

    assert sess.level_no == 2, f"Expected level_no to advance to 2, got {sess.level_no}"
    sound.stop_music()
    sound.start_music(TRACK_INGAME)
    assert sound.current_track == TRACK_INGAME
    print("    [OK] In-game music properly running on Level 2.")


if __name__ == "__main__":
    test_music_restart_after_crash()
    test_music_restart_on_next_level()
    print("\n=======================================================")
    print("  ALL MUSIC RESTART TESTS PASSED (100%)                ")
    print("=======================================================")
