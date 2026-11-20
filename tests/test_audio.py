#!/usr/bin/env python3
"""Audio pipeline: AdLib driver port, OPL2 emulator, rendered WAVs, playback.

The driver fingerprints below were taken after checking the port register
write for register write against a capture of the original driver code
running on a CPU emulator (every tick of all 10 streams; the only differences
were the tick before the first command and the music-channel restore after an
effect, which depend on what played before in the capture).
"""
import hashlib
import os
import sys
import time
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from extractor.adlx import PIT_HZ, Sequencer, blob_at, parse
from extractor.export_audio import STREAMS, WAV_RATE
from extractor.extract_assets import load_image
from extractor.opl2 import CHIP_RATE, OPL2

# blob -> (ticks until the 2nd loop restart / the stop, loop period, fingerprint)
DRIVER_STREAMS = {
    0x3637: (100803, 50400, '36e867f80dfa51ab'),   # title
    0x3444: (120963, 60480, '252a8ab9bb433278'),   # in game
    0x3279: (174723, 87360, 'ebf6d5487368c66b'),   # ending
    0x325B: (423, None, 'cd3d354b837aabd6'),
    0x3260: (843, 420, '61e13b013b914a3d'),
    0x3265: (1263, None, 'bd7964b4b30ba8bc'),
    0x326B: (843, None, '306302e64877b90a'),
    0x3274: (423, None, '60e51c1d26f07917'),
    0x3629: (843, None, '79cd152234c559dd'),
    0x3632: (213, None, '53097379251654cd'),
}
IMAGE = load_image(os.path.join(ROOT, 'assets', 'UGH.EXE'))


def test_driver_streams():
    print("--> 1. AdLib driver port: register streams of all 10 ADLX blobs...")
    for para, (name, level, music, loops, _) in STREAMS.items():
        seq = Sequencer(blob_at(IMAGE, para), level, music)
        h = hashlib.sha256(repr(seq.take_writes()).encode())
        t, marks = 0, []
        while True:
            restarts = seq.restarts
            writes = seq.tick(loops)
            t += 1
            h.update(repr((t, writes)).encode())
            if seq.restarts != restarts:
                marks.append(t)
            if (loops and len(marks) == 2) or (not loops and not seq.playing):
                break
        period = marks[1] - marks[0] if loops else None
        assert (t, period, h.hexdigest()[:16]) == DRIVER_STREAMS[para], (name, t, period)
        secs = (period or t) * seq.divisor / PIT_HZ
        print(f"    [OK] {name:20s} {secs:6.2f}s at {PIT_HZ / seq.divisor:6.1f} Hz")


def test_effect_writes():
    print("--> 2. Stone-throw effect: instrument and note writes as captured from DOS...")
    seq = Sequencer(blob_at(IMAGE, 0x3632), 0xFF, music=False)
    seq.take_writes()
    writes = []
    while not writes:
        writes = seq.tick(False)
    # captured: 32=c0 52=53 72=33 92=81 f2=02 35=42 55=7f 75=f6 95=57 01=20
    # f5=02 c8=0e, TL from the stale volume, 55=4c after 30 d4, a8/b8 twice
    assert writes[:12] == [(0x32, 0xC0), (0x52, 0x53), (0x72, 0x33), (0x92, 0x81),
                           (0xF2, 0x02), (0x35, 0x42), (0x55, 0x7F), (0x75, 0xF6),
                           (0x95, 0x57), (0x01, 0x20), (0xF5, 0x02), (0xC8, 0x0E)]
    assert writes[13:] == [(0x55, 0x4C), (0xA8, 0x02), (0xB8, 0x2E), (0xA8, 0x02), (0xB8, 0x2E)]
    print("    [OK] channel 8, TL 0x4C after volume 0xD4, key on fnum 0x202 block 3.")


def test_opl2_pitch():
    print("--> 3. OPL2: a plain sine note has the chip's pitch...")
    chip = OPL2()
    for reg, val in ((0x20, 0x01), (0x23, 0x01), (0x40, 0x3F), (0x43, 0x00), (0x63, 0xF0),
                     (0x83, 0x00), (0xC0, 0x01), (0xA0, 0x41), (0xB0, 0x32)):
        chip.write(reg, val)
    out = chip.render(round(CHIP_RATE))
    fnum, block = 0x241, 4
    expected = fnum * CHIP_RATE / (1 << (20 - block))
    rising = sum(1 for a, b in zip(out[5000:], out[5001:]) if a < 0 <= b)
    measured = rising * CHIP_RATE / (len(out) - 5001)
    assert abs(measured - expected) < 1.0, (measured, expected)
    print(f"    [OK] fnum {fnum:#x} block {block}: {measured:.1f} Hz (expected {expected:.1f} Hz)")


def test_opl2_block_render():
    print("--> 4. OPL2: block rendering == sample-by-sample rendering...")
    seq = Sequencer(blob_at(IMAGE, 0x3637), 0xA5, music=True)
    events, clock = [(i, r, v) for i, (r, v) in enumerate(seq.take_writes())], 0.0
    while clock < CHIP_RATE * 1.5:
        free = max(events[-1][0] + 1, int(clock))
        for k, (reg, val) in enumerate(seq.tick(True)):
            events.append((free + k, reg, val))
        clock += CHIP_RATE * seq.divisor / PIT_HZ
    n = int(clock)
    events = [e for e in events if e[0] < n]
    block = OPL2().render_block(n, events)
    chip, single, k = OPL2(), [], 0
    for i in range(n):
        while k < len(events) and events[k][0] == i:
            chip.write(events[k][1], events[k][2])
            k += 1
        single += chip.render(1)
    assert block == single
    assert max(map(abs, block)) > 100
    print(f"    [OK] {n} samples of the title music identical.")


def test_audio_files():
    print("--> 5. Rendered WAV files (assets/audio)...")
    audio_dir = os.path.join(ROOT, 'assets', 'audio')
    for para, (name, level, music, loops, _) in STREAMS.items():
        path = os.path.join(audio_dir, name)
        assert os.path.isfile(path), f"missing {name}: run python3 -m extractor.export_audio"
        with wave.open(path, 'rb') as wf:
            assert (wf.getnchannels(), wf.getsampwidth(), wf.getframerate()) == (1, 2, WAV_RATE)
            frames = wf.getnframes()
            data = wf.readframes(frames)
        ticks, period, _ = DRIVER_STREAMS[para]
        expect = (period or ticks) * parse(blob_at(IMAGE, para))['divisor'] / PIT_HZ
        secs = frames / WAV_RATE
        if loops:
            assert abs(secs - expect) < 0.01, (name, secs, expect)
        else:
            assert expect - 0.01 < secs < expect + 1.0, (name, secs, expect)
        peak = max(abs(int.from_bytes(data[i:i + 2], 'little', signed=True))
                   for i in range(0, len(data), 2))
        assert peak > 1000, (name, peak)
        print(f"    [OK] {name:20s} {secs:6.2f}s  peak {peak:5d}")


def test_sound_driver():
    print("--> 6. SoundDriver playback through pygame.mixer (dummy audio device)...")
    os.environ['SDL_AUDIODRIVER'] = 'dummy'
    from game.sound import STOP_FADE, SoundDriver, TRACK_INGAME, NAMED_SFX
    snd = SoundDriver(audio_dir=os.path.join(ROOT, 'assets', 'audio'))
    assert snd.active, "mixer did not open or no WAV files were loaded"
    assert snd.stop_music() == 0.0                 # nothing playing: no stall
    snd.start_music(TRACK_INGAME)
    assert snd._music.get_busy()
    for name in NAMED_SFX:
        assert snd.play_named(name) is not None, name
    fly = snd._named_channels['pterodactyl_fly']
    time.sleep(1.0)                                # longer than one 0.46 s loop
    assert fly.get_busy(), "the flight sound must loop until stopped"
    snd.stop_named('pterodactyl_fly')
    assert not fly.get_busy()
    wait = snd.stop_music()
    assert abs(wait - STOP_FADE) < 0.1, wait
    snd.shutdown()
    print(f"    [OK] music loops, effects play, flight sound loops, stop fades {STOP_FADE:.1f}s.")


if __name__ == '__main__':
    test_driver_streams()
    test_effect_writes()
    test_opl2_pitch()
    test_opl2_block_render()
    test_audio_files()
    test_sound_driver()
    print("\n=======================================================")
    print("  ALL AUDIO TESTS PASSED                               ")
    print("=======================================================")
