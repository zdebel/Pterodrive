#!/usr/bin/env python3
"""UGH! - playable port driven by the reverse-engineered engine.

  python3 main.py [--exe UGH.EXE] [--mode 1|2] [--difficulty 0|1|2] [--level N]
                  [--record-telemetry trace.csv]
                  [--record-input inputs.json] [--playback-input inputs.json]
                  [--headless] [--ticks N] [--scale N] [--fullscreen]

Keys: player 1 = arrows + Right Ctrl/Keypad 0 (fire); player 2 (--mode 2) =
W/Z/A/S + Left Ctrl/Keypad Enter.  P = pause (any key resumes), Esc = abort
(lives := 0), F11 = fullscreen.

Per-tick order (original main loop): water -> input -> craft physics ->
passengers + stones -> hazards -> fruit -> end-of-level checks -> render.
Ticks run at engine.timing.TICK_HZ (70.086 Hz) from a FixedClock; rendering
happens once per displayed frame.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
FROZEN = getattr(sys, 'frozen', False)     # a PyInstaller release build
# where the program lives: next to the source, or next to the release executable
APP_DIR = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else ROOT

from engine.input_handler import (DosKeyboard, InputPlayer, InputRecorder,
                                  PygameKeyboard, UP, DOWN, LEFT, RIGHT, FIRE)
from version import __version__
from engine.file_picker import dialog_available, find_file, pick_file
from engine.joystick import JoyState, PygameStick, calibrate, poll
from engine.timing import FixedClock, TICK_HZ
from game.session import CSV_HEADER, GameSession
from game.sound import SoundDriver, TRACK_INGAME

ACTIONS = (('up', UP), ('down', DOWN), ('left', LEFT), ('right', RIGHT), ('fire', FIRE))


def default_assets_dir() -> str:
    """./assets when run from source; a per-user data folder for release builds
    (the executable's own folder may be read-only, e.g. Program Files)."""
    if not FROZEN:
        return os.path.join(ROOT, 'assets')
    if sys.platform == 'win32':
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
        return os.path.join(base, 'Pterodrive', 'assets')
    if sys.platform == 'darwin':
        return os.path.expanduser('~/Library/Application Support/Pterodrive/assets')
    base = os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share')
    return os.path.join(base, 'pterodrive', 'assets')


ASSETS_DIR = default_assets_dir()


def check_assets_complete(assets_dir: str = ASSETS_DIR) -> bool:
    """Check if all required game assets are present in assets_dir."""
    required = [
        os.path.join(assets_dir, 'game.pal'),
        os.path.join(assets_dir, 'levels.bin'),
        os.path.join(assets_dir, 'screens', 'screen_0.png'),
        os.path.join(assets_dir, 'sprites', 'sprites.json'),
        os.path.join(assets_dir, 'audio', 'track_title.wav'),
        os.path.join(assets_dir, 'audio', 'sfx_ptero_fly.wav'),
        os.path.join(assets_dir, 'UGH.EXE'),
    ]
    return all(os.path.isfile(p) and os.path.getsize(p) > 0 for p in required)


def is_ugh_exe(path: str | None) -> bool:
    """A DOS MZ executable with UGH!'s title music where the game keeps it."""
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except (OSError, TypeError):
        return False
    if data[:2] not in (b'MZ', b'ZM'):
        return False
    title = int.from_bytes(data[8:10], 'little') * 16 + 0x3637 * 16
    return data[title:title + 4] == b'ADLX'


def locate_exe(exe_arg: str | None) -> str | None:
    """--exe, then UGH.EXE (any case) next to the game or in the current
    directory, then the native file dialog, then a terminal prompt."""
    if exe_arg:
        if is_ugh_exe(exe_arg):
            return os.path.abspath(exe_arg)
        print(f"'{exe_arg}' is not the UGH! DOS executable.")
    found = find_file(dict.fromkeys((APP_DIR, os.getcwd())), 'UGH.EXE')
    if is_ugh_exe(found):
        print(f'Found the original game: {found}')
        return os.path.abspath(found)
    if dialog_available():
        print('Original UGH.EXE not found next to the game; asking where it is...')
    else:
        print('Original UGH.EXE not found next to the game, and no file dialog is')
        print('available here (Linux: install zenity, kdialog or python3-tk).')
    while dialog_available():
        picked = pick_file('Locate UGH.EXE (original DOS game)', APP_DIR, ('*.exe',), 'UGH.EXE')
        if picked is None:
            break
        if is_ugh_exe(picked):
            return os.path.abspath(picked)
        print(f"  '{picked}' is not the UGH! DOS executable, try again.")
    if not sys.stdin or not sys.stdin.isatty():
        return None
    while True:
        try:
            p = input("Enter path to UGH.EXE (or 'q' to quit): ").strip().strip('"')
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if p.lower() in ('q', 'quit', 'exit'):
            return None
        p = os.path.expanduser(p)
        if os.path.isdir(p):                         # a folder: look inside it
            p = find_file([p], 'UGH.EXE') or p
        if is_ugh_exe(p):
            return os.path.abspath(p)
        if p:
            print(f"  '{p}' is not the UGH! DOS executable. Please try again.")


def ensure_assets(exe_arg: str | None = None, assets_dir: str = ASSETS_DIR, gui: bool = False) -> str:
    """Ensure assets are ready. If missing, find the original UGH.EXE and extract
    (gui: with a progress window instead of terminal output, when a display exists)."""
    target_exe = os.path.join(assets_dir, 'UGH.EXE')
    if check_assets_complete(assets_dir):
        return exe_arg if (exe_arg and os.path.isfile(exe_arg)) else target_exe

    print("=" * 70)
    print("                      UGH! - Python Port Launcher")
    print("=" * 70)
    print(f"Game assets are not in {assets_dir} yet. They are extracted")
    print("from your original DOS UGH.EXE, which is not bundled with this port.")
    print("=" * 70)
    source_exe = locate_exe(exe_arg)
    if not source_exe:
        print("Aborted: UGH.EXE was not provided.")
        sys.exit(1)

    os.makedirs(assets_dir, exist_ok=True)
    ui = _open_setup_window(source_exe) if gui else None
    if ui:
        _extract_with_window(ui, source_exe, assets_dir, target_exe)
        return target_exe

    print("\n" + "=" * 70)
    print(f"Extracting assets from: {source_exe}")
    print(f"Target directory:       {assets_dir}/")
    print("=" * 70)

    print("\n[1/3] Extracting palettes, full screens, level maps, and sprites...")
    from extractor import extract_all
    extract_all(source_exe, assets_dir, quiet=False)

    print("\n[2/3] Rendering OPL2 music & sound effects (takes a while without the native core)...")
    from extractor import export_all_audio
    audio_dir = os.path.join(assets_dir, 'audio')
    export_all_audio(source_exe, audio_dir, quiet=False)

    print(f"\n[3/3] Copying executable reference into {assets_dir}...")
    _copy_reference_files(source_exe, assets_dir, target_exe)

    print("\n" + "=" * 70)
    print("✓ All assets extracted successfully! Starting UGH!...")
    print("=" * 70 + "\n")
    return target_exe


def _copy_reference_files(source_exe, assets_dir, target_exe):
    if os.path.abspath(source_exe) != os.path.abspath(target_exe):
        shutil.copyfile(source_exe, target_exe)
    hi_src = find_file([os.path.dirname(source_exe)], 'UGH!.HI')
    if hi_src:
        target_hi = os.path.join(assets_dir, 'UGH!.HI')
        if os.path.abspath(hi_src) != os.path.abspath(target_hi):
            shutil.copyfile(hi_src, target_hi)


def _open_setup_window(source_exe):
    """The progress window, or None when there is no display (text mode then)."""
    try:
        from engine.setup_screen import SetupScreen
        return SetupScreen(os.path.basename(source_exe))
    except Exception as e:
        print(f'No setup window ({e}); extracting in the terminal.')
        return None


def _extract_with_window(ui, source_exe, assets_dir, target_exe):
    from engine.setup_screen import SetupAborted
    from extractor import export_all_audio, extract_all
    try:
        try:
            ui.update(0, 0.5, 'Extracting palettes, screens, levels and sprites')
            extract_all(source_exe, assets_dir, quiet=True)
            ui.update(1, 0.0, 'Starting the sound chip emulation')
            export_all_audio(source_exe, os.path.join(assets_dir, 'audio'), quiet=True,
                             on_progress=lambda frac, detail: ui.update(1, frac, detail))
            ui.update(2, 0.5, 'Copying the executable reference')
            _copy_reference_files(source_exe, assets_dir, target_exe)
        except SetupAborted:
            print('Aborted: setup window closed.')
            sys.exit(1)
        except Exception as e:
            ui.error(f'{type(e).__name__}: {e}')
            raise
    finally:
        ui.close()


def read_inputs(kb, n_players, stick=None, joy_state=None):
    joy_inputs = (poll(stick.port(), joy_state, n_players)
                  if stick and joy_state else [None] * n_players)
    inputs = []
    for p in range(n_players):
        j_in = joy_inputs[p] if p < len(joy_inputs) else None
        if j_in is not None:
            inputs.append(j_in)
        else:
            inputs.append({name: kb.held(p, act) for name, act in ACTIONS})
    return inputs


def run_tick(sess, kb, tel, stick=None, joy_state=None):
    """One logic tick; returns False when the session ended."""
    esc, kb.quit_event = kb.quit_event, False
    sess.tick(read_inputs(kb, sess.mode, stick, joy_state), esc=esc)
    if tel:
        tel.write(','.join(str(v) for v in sess.telemetry_row()) + '\n')
    return not sess.over


def parse():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--version', action='version', version=f'Pterodrive {__version__}')
    ap.add_argument('--exe', default=None,
                    help='path to original UGH.EXE (defaults to assets/UGH.EXE)')
    ap.add_argument('--assets-dir', default=ASSETS_DIR,
                    help='directory containing extracted assets (default: ./assets)')
    ap.add_argument('--mode', type=int, choices=(1, 2), default=1)
    ap.add_argument('--difficulty', type=int, choices=(0, 1, 2), default=1)
    ap.add_argument('--level', type=int, default=1)
    ap.add_argument('--hz', type=float, default=TICK_HZ,
                    help=f'logic and render frequency in Hz (default: {TICK_HZ:.3f} Hz, DOS VGA Mode 13h)')
    ap.add_argument('--record-telemetry', metavar='CSV')
    ap.add_argument('--record-input', metavar='JSON')
    ap.add_argument('--playback-input', metavar='JSON')
    ap.add_argument('--headless', action='store_true',
                    help='no window; ticks run as fast as possible')
    ap.add_argument('--ticks', type=int, help='stop after N ticks')
    ap.add_argument('--scale', type=int, default=3, help='window = 320*scale x 240*scale')
    ap.add_argument('--fullscreen', action='store_true')
    ap.add_argument('--skip-frontend', action='store_true',
                    help='start gameplay directly, without title/menu/level cards')
    ap.add_argument('--no-joystick', action='store_true',
                    help='disable joystick/gamepad input')
    ap.add_argument('--no-sound', action='store_true',
                    help='disable sound driver')
    ap.add_argument('--scene', choices=('title', 'second_intro', 'game_intro', 'ending'),
                    help='play a specific cutscene/intro/outro and exit')
    ap.add_argument('--screenshot', metavar='PNG', help='save the last frame (windowed path)')
    ap.add_argument('--text-setup', action='store_true',
                    help='first-run asset extraction in the terminal instead of a window')
    return ap.parse_args()


def main():
    a = parse()
    a.exe = ensure_assets(a.exe, a.assets_dir, gui=not (a.headless or a.text_setup))

    if a.headless:
        os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
        os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')
    player = InputPlayer(a.playback_input) if a.playback_input else None
    tel = open(a.record_telemetry, 'w') if a.record_telemetry else None
    if tel:
        tel.write(CSV_HEADER + '\n')
    limit = a.ticks
    if limit is None and player and a.headless:
        limit = player.last_tick + 140
    direct = (a.skip_frontend or a.headless or a.playback_input or
              a.record_input or a.record_telemetry or a.ticks is not None)
    kb = (DosKeyboard() if player else PygameKeyboard()) if direct else None
    rec = InputRecorder(kb) if a.record_input else None
    sess = None
    try:
        if direct and a.headless and not os.environ.get('UGH_RENDER') and not a.scene:
            sess = GameSession(a.exe, a.mode, a.level,
                                difficulty=a.difficulty)
            _headless(sess, kb, rec, player, tel, limit)
        else:
            sess = _interactive(a, kb, rec, player, tel, limit,
                                use_frontend=(not direct) or bool(a.scene))
    finally:
        if tel:
            tel.close()
        if rec:
            rec.save(a.record_input)
    if sess is not None:
        print('ticks %d  level %d  score %d  lives %d%s' % (
            sess.total_ticks, sess.level_no, sess.score, sess.lives,
            '  GAME WON' if sess.won else ('  GAME OVER' if sess.over else '')))


def _headless(sess, kb, rec, player, tel, limit):
    while limit is None or sess.total_ticks < limit:
        if player:
            player.feed(kb, sess.total_ticks)
        if rec:
            rec.tick = sess.total_ticks
        if not run_tick(sess, kb, tel):
            break


def _interactive(a, kb, rec, player, tel, limit, use_frontend):
    import pygame
    from game.render import H, W, Renderer
    pygame.init()
    stick = None
    joy_state = JoyState()
    if not a.no_joystick:
        try:
            pygame.joystick.init()
            count = pygame.joystick.get_count()
            sticks = [pygame.joystick.Joystick(i) for i in range(count)]
            for s in sticks:
                s.init()
            if sticks:
                stick = PygameStick(sticks[0] if len(sticks) > 0 else None,
                                    sticks[1] if len(sticks) > 1 else None)
                calibrate(stick.port(), joy_state)
        except Exception:
            stick = None
    flags = pygame.RESIZABLE | (pygame.FULLSCREEN if a.fullscreen else 0)
    screen = pygame.display.set_mode((W * a.scale, 240 * a.scale), flags)
    pygame.display.set_caption('UGH!')
    surf = pygame.Surface((W, H), depth=8)
    audio_dir = os.path.join(a.assets_dir, 'audio')
    sound = SoundDriver(a.exe, enabled=not a.no_sound, audio_dir=audio_dir)
    frontend = None
    if use_frontend:
        from game.frontend import Frontend
        frontend = Frontend(a.exe, a.mode, a.difficulty,
                            joy_state=joy_state, stick=stick, sound=sound,
                            tick_hz=a.hz)
        frontend.bind(screen)
    if a.scene and frontend:
        frontend.play_scene(a.scene)
        if sound:
            sound.shutdown()
        pygame.quit()
        return None
    last_sess = None
    last_frame = last_palette = None
    running = True
    while running:
        if frontend:
            request = frontend.run()
            if request is None:
                break
            sess = GameSession(a.exe, request.mode, request.level,
                               difficulty=request.difficulty)
            kb = PygameKeyboard()
            if getattr(request, 'controllers', None):
                joy_state.p1 = request.controllers[0] if request.controllers[0] >= 0 else 0xFFFF
                joy_state.p2 = request.controllers[1] if request.controllers[1] >= 0 else 0xFFFF
            if not frontend.show_level(sess.mode, sess.level_no):
                break
        else:
            sess = GameSession(a.exe, a.mode, a.level,
                               difficulty=a.difficulty)
            if kb is None:
                kb = PygameKeyboard()
        last_sess = sess
        running, last_frame, last_palette = _play_session(
            a, screen, surf, sess, kb, rec, player, tel, limit, frontend,
            stick=stick, joy_state=joy_state, sound=sound)
        if not frontend:
            break
        if not running or not frontend.running:
            break
        if sess.over:
            frontend.finish_game(sess.score, sess.won)
            running = frontend.running
    if a.screenshot and last_frame is not None:
        from extractor import write_png
        write_png(a.screenshot, W, H, last_frame, last_palette)
    if sound:
        sound.shutdown()
    pygame.quit()
    return last_sess


def _play_session(a, screen, surf, sess, kb, rec, player, tel, limit,
                  frontend=None, stick=None, joy_state=None, sound=None):
    import pygame
    from game.render import H, W, Renderer
    rnd = Renderer(a.exe, sess.sprite)
    target_hz = getattr(a, 'hz', TICK_HZ)
    clock = FixedClock(tick_hz=target_hz)
    pg_clock = pygame.time.Clock()
    paused = False
    last = time.perf_counter()
    running = True
    last_frame = bytes(W * H)
    last_palette = rnd.pal
    if sound:
        sound.start_music(TRACK_INGAME)
    while running:
        resumed = False
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type in (pygame.KEYDOWN, pygame.KEYUP):
                if ev.type == pygame.KEYDOWN and ev.key == pygame.K_F11:
                    pygame.display.toggle_fullscreen()
                    continue
                if player is None:
                    kb.handle_event(ev)
                if ev.type == pygame.KEYDOWN and paused:
                    paused = False
                    resumed = True
                    kb.pause_event = False
                    clock.reset()
                    last = time.perf_counter()
            elif ev.type in (pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP):
                if ev.type == pygame.JOYBUTTONDOWN and paused:
                    paused = False
                    resumed = True
                    kb.pause_event = False
                    clock.reset()
                    last = time.perf_counter()
        if kb.pause_event and not resumed:
            kb.pause_event = False
            paused = True
        now = time.perf_counter()
        dt_elapsed = now - last
        n = 0 if paused else clock.advance(dt_elapsed)
        last = now
        if paused and sound:
            sound.tick(dt_elapsed)
        for _ in range(n):
            if player:
                player.feed(kb, sess.total_ticks)
            if rec:
                rec.tick = sess.total_ticks
            old_state = sess.state
            alive = run_tick(sess, kb, tel, stick=stick, joy_state=joy_state)
            if sound:
                if hasattr(sess.state, 'hz') and hasattr(sess.state.hz, 'events'):
                    while sess.state.hz.events:
                        ev, name = sess.state.hz.events.pop(0)
                        if ev == 'play':
                            sound.play_named(name)
                        elif ev == 'stop':
                            sound.stop_named(name)
                sound.tick(1.0 / target_hz)
            if sess.state is not old_state:
                if frontend:
                    if not frontend.show_level(sess.mode, sess.level_no):
                        running = False
                        break
                if sound:
                    sound.start_music(TRACK_INGAME)
                clock.reset()
                last = time.perf_counter()
            if not alive:
                break
            if limit is not None and sess.total_ticks >= limit:
                running = False
                break
        fb = rnd.frame(sess)
        surf.set_palette([tuple(rnd.pal[3 * i:3 * i + 3]) for i in range(256)])
        buf = surf.get_buffer()
        buf.write(bytes(fb))
        del buf
        last_frame, last_palette = bytes(fb), rnd.pal
        # 320x200 pixels shown on a 4:3 display: fit 4:3 box, nearest scaling
        sw, sh = screen.get_size()
        bw, bh = (sw, sw * 3 // 4) if sw * 3 <= sh * 4 else (sh * 4 // 3, sh)
        scaled = pygame.transform.scale(surf.convert(), (bw, bh))
        screen.fill((0, 0, 0))
        screen.blit(scaled, ((sw - bw) // 2, (sh - bh) // 2))
        pygame.display.flip()
        if sound:
            sound.tick()
        pg_clock.tick(target_hz)
        if sess.over:
            break
    if sound:
        sound.stop_music()
    return running, last_frame, last_palette


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()       # audio rendering workers in release builds
    main()
