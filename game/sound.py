"""Music and sound effects, played from the WAV files the extractor renders.

The original drives an AdLib/OPL2 through its own driver (segments C, D, E of
UGH.EXE, see extractor/adlx.py).  The port renders every ADLX blob once, at
asset-extraction time, with a pure-Python reimplementation of that driver and
of the chip (extractor/adlx.py, extractor/opl2.py), so at run time sound is
plain sample playback through pygame.mixer: music loops on a reserved mixer
channel, effects take the other channels.

Far call entry points of the driver (segment 0x878) and their port:
  - 0x0F88 (F=0xA108): start_music(off, seg, loop, tempo) -> start_music()
  - 0x1059 (F=0xA1D9): stop_music(flag=0x11)              -> stop_music()
  - 0x1852 (F=0xA9D2): play_sfx(off, seg, vol, prio)      -> play_sfx()
  - 0x1768 (F=0xA8E8): stop_channel(channel)              -> stop_channel()
"""
from __future__ import annotations

import os
import time

ROOT = os.path.dirname(os.path.abspath(__file__))

# ADLX blobs (paragraph of the blob in the load image)
TRACK_TITLE = 0x3637       # Title, menu and game_intro cutscene
TRACK_INGAME = 0x3444      # In-game levels
TRACK_ENDING = 0x3279      # Game won ending scene & high score entry
TRACK_INTRO = TRACK_TITLE  # the DOS game plays TRACK_TITLE through game_intro

SFX_PTERO_WARN = 0x325B    # pterodactyl about to appear (F=0x417E)
SFX_PTERO_FLY = 0x3260     # pterodactyl flying, loops until it leaves or is hit (F=0x4234)
SFX_BOULDER = 0x3265       # boulder field animation frame 6 (F=0x4752)
SFX_SPLASH = 0x326B        # game over screen (F=0x1F42)
SFX_PICKUP = 0x3274        # bonus token, shaken fruit tree (F=0x38AA, F=0x48FE)
SFX_IMPACT = 0x3629        # level card (F=0x2492)
SFX_SHOUT = 0x3632         # stone thrown (F=0x3A94)

# blob -> file name in assets/audio (written by extractor.export_audio, which
# renders each blob at the volume the game plays it with)
AUDIO_FILES = {
    TRACK_TITLE: 'track_title.wav',
    TRACK_INGAME: 'track_ingame.wav',
    TRACK_ENDING: 'track_ending.wav',
    SFX_PTERO_WARN: 'sfx_ptero_warn.wav',
    SFX_PTERO_FLY: 'sfx_ptero_fly.wav',
    SFX_BOULDER: 'sfx_boulder.wav',
    SFX_SPLASH: 'sfx_splash.wav',
    SFX_PICKUP: 'sfx_pickup.wav',
    SFX_IMPACT: 'sfx_impact.wav',
    SFX_SHOUT: 'sfx_shout.wav',
}
TRACKS = (TRACK_TITLE, TRACK_INGAME, TRACK_ENDING)

# game event -> (blob, play count; 0 = until stopped), as passed to play_sfx
NAMED_SFX = {
    'pterodactyl_warn': (SFX_PTERO_WARN, 1),
    'pterodactyl_fly': (SFX_PTERO_FLY, 0),
    'boulder': (SFX_BOULDER, 1),
    'pickup': (SFX_PICKUP, 1),
    'tree': (SFX_PICKUP, 1),
    'shout': (SFX_SHOUT, 1),
}

MIXER_CHANNELS = 5                 # channel 0 = music, 1..4 = the driver's effect slots 8..11
# stop_music(0x11) (D:1059) ramps the music level to 0 in 0x11 steps of a
# 119275-tick PIT callback (C:134E, C:12E5) and only returns when it is done
STOP_FADE = 0x12 * 119275 / 1193182      # 1.8 s


class SoundDriver:
    """Controls background music and sound effects.

    Silently does nothing when sound is disabled, the audio files are missing
    or no audio device can be opened.
    """

    def __init__(self, exe: str | None = None, enabled: bool = True,
                 audio_dir: str | None = None):
        self.exe = exe
        self.current_track: int | None = None
        self.sounds = {}
        self._music = None
        self._named_channels: dict[str, object] = {}
        self._fade_end = 0.0
        if audio_dir is None:
            audio_dir = os.path.join(ROOT, '..', 'assets', 'audio')
        self.audio_dir = audio_dir
        if not enabled:
            return
        try:
            import pygame
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=1024)
            pygame.mixer.set_num_channels(MIXER_CHANNELS)
            pygame.mixer.set_reserved(1)
            self._music = pygame.mixer.Channel(0)
            for para, name in AUDIO_FILES.items():
                path = os.path.join(audio_dir, name)
                if os.path.isfile(path):
                    self.sounds[para] = pygame.mixer.Sound(path)
        except Exception:
            self.sounds = {}
            self._music = None

    @property
    def active(self) -> bool:
        return self._music is not None and bool(self.sounds)

    def start_music(self, track_para: int):
        """Start (or restart) a music track, looping forever."""
        self.current_track = track_para
        self._fade_end = 0.0
        snd = self.sounds.get(track_para)
        if self._music is not None and snd is not None:
            self._music.play(snd, loops=-1)

    def stop_music(self) -> float:
        """Fade the music out; returns the seconds until it is silent, which
        is how long the original stalls here (0 if no music was playing)."""
        if self.current_track is not None:
            self.current_track = None
            self._fade_end = time.monotonic() + STOP_FADE
            if self._music is not None:
                self._music.fadeout(round(STOP_FADE * 1000))
        return max(0.0, self._fade_end - time.monotonic())

    def play_sfx(self, sfx_para: int, count: int = 1):
        """Trigger a sound effect (count 0 = loop until stopped); returns its
        mixer channel or None.  Like the driver (D:1852), a new effect takes
        the oldest one's place when every effect channel is busy."""
        snd = self.sounds.get(sfx_para)
        if snd is None:
            return None
        import pygame
        ch = pygame.mixer.find_channel(True)
        if ch is None:
            return None
        ch.play(snd, loops=count - 1)
        return ch

    def play_named(self, name: str):
        """Trigger a game event's sound effect (see NAMED_SFX)."""
        if name not in NAMED_SFX:
            return None
        ch = self.play_sfx(*NAMED_SFX[name])
        if ch is not None:
            self._named_channels[name] = ch
        return ch

    def stop_channel(self, ch):
        if ch is not None:
            ch.stop()

    def stop_named(self, name: str):
        """Silence a currently playing named sound effect."""
        self.stop_channel(self._named_channels.pop(name, None))

    def tick(self, dt: float | None = None):
        """Kept for the game loops; the mixer runs on its own thread."""

    def shutdown(self):
        if self._music is not None:
            try:
                import pygame
                if pygame.mixer.get_init():
                    pygame.mixer.stop()
            except Exception:
                pass
        self._music = None
        self.sounds = {}
