#!/usr/bin/env python3
"""Render the UGH! music and sound effects from UGH.EXE to WAV files.

  python3 -m extractor.export_audio path/to/UGH.EXE [--out assets/audio]

Each ADLX blob is run through the port of the game's AdLib driver
(extractor/adlx.py) and the register writes are fed to the OPL2
emulator (extractor/opl2.py).  Output: 16-bit mono WAV at the chip's native
49716 Hz, one shared gain for every file so music and effects keep the
balance they have on the real chip.  pygame.mixer resamples on load.

Streams the game loops (all music, the pterodactyl flight sound) are written
as exactly one loop period taken from the second pass, so the notes still
sounding at the loop point carry over and the file loops without a seam.
"""
from __future__ import annotations

import argparse
import array
import multiprocessing
import os
import shutil
import sys
import time
import wave
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

import numpy as np

if __package__ in (None, ''):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
    from extractor.adlx import PIT_HZ, Sequencer, blob_at
    from extractor.extract_assets import load_image
    from extractor.opl2 import CHIP_RATE, OPL2
else:
    from .adlx import PIT_HZ, Sequencer, blob_at
    from .extract_assets import load_image
    from .opl2 import CHIP_RATE, OPL2

WAV_RATE = round(CHIP_RATE)
PEAK = 30000                     # target peak of the loudest file

# blob paragraph -> (file, level, music, loops, what the game plays it for)
# Levels and loop counts are the arguments of the game's driver calls:
# start_music(.., level 0xA5) and play_sfx(.., vol, count), count 0 = forever.
STREAMS = {
    0x3637: ('track_title.wav', 0xA5, True, True, 'title, menu and intro'),
    0x3444: ('track_ingame.wav', 0xA5, True, True, 'levels'),
    0x3279: ('track_ending.wav', 0xA5, True, True, 'ending and high-score entry'),
    0x325B: ('sfx_ptero_warn.wav', 0xFF, False, False, 'pterodactyl warning (F=0x417E)'),
    0x3260: ('sfx_ptero_fly.wav', 0xFF, False, True, 'pterodactyl flight (F=0x4234, loops)'),
    0x3265: ('sfx_boulder.wav', 0xFF, False, False, 'boulder field (F=0x4752)'),
    0x326B: ('sfx_splash.wav', 0xE1, False, False, 'game over screen (F=0x1F42)'),
    0x3274: ('sfx_pickup.wav', 0xFF, False, False, 'bonus token, shaken tree (F=0x38AA, 0x48FE)'),
    0x3629: ('sfx_impact.wav', 0xE1, False, False, 'level card (F=0x2492)'),
    0x3632: ('sfx_shout.wav', 0xFF, False, False, 'stone thrown (F=0x3A94)'),
}

TAIL_LIMIT = WAV_RATE            # one-shot effects: at most 1 s of release after the stop
SILENCE = 4                      # |sample| below this counts as silence when trimming
BLOCK = 1 << 15                  # samples rendered per OPL2.render_block call


def render(blob: bytes, level: int, music: bool, loops: bool, progress=None) -> array.array:
    """Driver + chip -> raw chip samples (unscaled ints).

    A looping stream returns exactly one loop period; a one-shot stream
    returns everything up to the driver's stop plus the release tail.
    progress(done, total) is called per rendered block with the samples
    produced so far and the expected total (0 while it is not known yet).
    """
    seq = Sequencer(blob, level, music)
    chip = OPL2()
    out = array.array('i')
    pending = []                     # (absolute sample, reg, val) not rendered yet
    clock = 0.0                      # absolute sample time of the current tick
    free = 0                         # first sample the next write can land on

    def emit(writes):
        nonlocal free
        free = max(free, int(clock))
        for reg, val in writes:      # a register write takes ~1 chip sample
            pending.append((free, reg, val))
            free += 1

    def flush(upto):
        done = len(out)
        now = [(p - done, r, v) for p, r, v in pending if p < upto]
        del pending[:len(now)]
        out.extend(chip.render_block(upto - done, now))

    emit(seq.take_writes())
    marks = []                       # sample of each loop restart
    while True:
        restarts = seq.restarts
        writes = seq.tick(loop=loops)
        if seq.restarts != restarts:
            marks.append(int(clock))
        emit(writes)
        clock += CHIP_RATE * seq.divisor / PIT_HZ
        if int(clock) - len(out) >= BLOCK:
            flush(int(clock))
            if progress:      # a loop ends at its 2nd restart, about twice the 1st
                progress(int(clock), 2 * marks[0] if marks else 0)
        if loops and len(marks) == 2:
            flush(max(int(clock), free))
            return out[marks[0]:marks[1]]
        if not loops and not seq.playing:
            break
    flush(max(int(clock), free))
    stop = len(out)
    out.extend(chip.render(TAIL_LIMIT))
    end = len(out)
    while end > stop and abs(out[end - 1]) < SILENCE:
        end -= 1
    return out[:end]


_PROGRESS = None                 # shared (samples, expected total) per stream, set in workers


def _init_worker(shared):
    global _PROGRESS
    _PROGRESS = shared


def _job(args):
    exe, para, slot = args
    _, level, music, loops, _ = STREAMS[para]

    def progress(done, total):
        _PROGRESS[2 * slot], _PROGRESS[2 * slot + 1] = done, total

    return para, render(blob_at(load_image(exe), para), level, music, loops, progress)


def write_wav(path: str, samples: array.array, gain: float):
    scaled = np.round(np.frombuffer(samples, dtype=np.intc) * gain)
    pcm = np.clip(scaled, -32767, 32767).astype('<i2')
    with wave.open(path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(WAV_RATE)
        wf.writeframes(pcm.tobytes())


def _progress(done, running, shared, order):
    """Overall fraction (0..1) and a label per running stream."""
    frac = float(len(done))
    parts = []
    for slot, para in enumerate(order):
        if para in done or para not in running:
            continue
        cur, est = shared[2 * slot], shared[2 * slot + 1]
        name = STREAMS[para][0].rsplit('.', 1)[0]
        if est:
            f = min(cur / est, 0.99)
            frac += f
            parts.append(f'{name} {f:.0%}')
        else:
            parts.append(f'{name} {cur / WAV_RATE:.0f}s')
    return frac / len(order), parts


def _status(done, running, shared, order, total_jobs, width):
    """One-line progress: overall bar + what each running stream has reached."""
    frac, parts = _progress(done, running, shared, order)
    bar_w = 20
    filled = int(bar_w * frac)
    line = f'[{len(done)}/{total_jobs}] {"█" * filled}{"░" * (bar_w - filled)} {frac:4.0%}  ' + ', '.join(parts)
    return line[:width - 1]


def export_all(exe: str, out_dir: str, quiet: bool = False, workers: int | None = None,
               on_progress=None):
    os.makedirs(out_dir, exist_ok=True)
    order = list(STREAMS)
    jobs = [(exe, para, slot) for slot, para in enumerate(order)]
    quiet = quiet or on_progress is not None     # a GUI shows the progress instead
    if not quiet:
        print(f'Rendering {len(jobs)} ADLX streams from {exe} (takes ~10 s with the native core, ~1 min without)...')
    live = not quiet and sys.stdout.isatty()
    # 'fork' on Linux (no re-import, works when frozen), 'spawn' elsewhere
    ctx = multiprocessing.get_context('fork' if sys.platform.startswith('linux') else 'spawn')
    shared = ctx.Array('d', 2 * len(jobs), lock=False)
    rendered = {}
    start = time.monotonic()
    pool = ProcessPoolExecutor(max_workers=workers, mp_context=ctx,
                               initializer=_init_worker, initargs=(shared,))
    try:
        pending = {pool.submit(_job, job): job[1] for job in jobs}
        while pending:
            finished, _ = wait(pending, timeout=0.05 if on_progress else 0.2, return_when=FIRST_COMPLETED)
            for fut in finished:
                para, samples = fut.result()
                del pending[fut]
                rendered[para] = samples
                if not quiet:
                    name, level, music, loops, what = STREAMS[para]
                    kind = 'loop' if loops else 'once'
                    print(f'{chr(13) + chr(27) + "[K" if live else ""}  ✓ [{len(rendered)}/{len(jobs)}] '
                          f'{name:20s} {len(samples) / WAV_RATE:6.2f}s {kind:4s}  {what}', flush=True)
            if on_progress:
                frac, parts = _progress(rendered, set(pending.values()), shared, order)
                on_progress(frac, ', '.join(parts))     # may raise to abort
            elif live and pending:
                width = shutil.get_terminal_size().columns
                print('\r' + _status(rendered, set(pending.values()), shared, order, len(jobs), width),
                      end='\x1b[K', flush=True)
    except BaseException:
        for proc in list(getattr(pool, '_processes', {}).values()):
            proc.terminate()                  # aborted: do not wait for the renders
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown()
    peak = max(int(np.abs(np.frombuffer(s, dtype=np.intc)).max()) for s in rendered.values() if len(s)) or 1
    gain = PEAK / peak
    for para in order:
        write_wav(os.path.join(out_dir, STREAMS[para][0]), rendered[para], gain)
    if not quiet:
        print(f'Wrote {len(order)} files to {out_dir} in {time.monotonic() - start:.1f} s')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('exe', help='path to the original UGH.EXE')
    ap.add_argument('--out', default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  '..', 'assets', 'audio'))
    args = ap.parse_args()
    export_all(args.exe, args.out)


if __name__ == '__main__':
    main()
