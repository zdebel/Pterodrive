"""Port of the UGH.EXE AdLib driver: ADLX blob parser and event sequencer.

The driver lives in segments C (paragraph 0x664, F=0x7040) and D (0x878,
F=0x9180); addresses below are offsets in those segments.  It plays one music
stream (slot 0xC) and up to four effect streams (slots 8..11) on a shared OPL2.
This module runs ONE stream on its own driver instance and returns the timed
register writes, which is all the WAV renderer needs: in-game music uses 7
channels, so effects (taking channels from 8 downwards) never steal a music
channel, and the chip output is the plain sum of its channels.

Blob layout (D:0AA9): 'ADLX' u32be, 'AHDR' u32be=10 + 10 bytes (byte 2 =
channel count, bytes 8..9 = PIT divisor), 'IMAP' u32be + data (unused), 'TRCK'
u32 + the command stream.  Commands (C:15D3 music / C:176B effects, jump tables
at C:174B / C:1919, indexed by the high nibble):

  0n bb     wait ((n << 8) | bb) * 128 ticks
  1n lo hi  note: An = lo, Bn = hi                 (C:1503)
  2n        key off                                (C:14B1)
  3n vv     channel volume                         (C:1571)
  4n +11    instrument: 20/40/60/80/E0 of both operators, then C0  (C:1377)
  5x        no-op
  6x hi lo  PIT divisor (= tick rate 1193182 / divisor Hz)
  77        end of stream: loop or stop
  7n ll     skip ll bytes (n != 0), 70 xx: skip 2 more
  8x..Fx    wait (x & 0x7F) ticks

Channel numbers n are relative to the stream's first channel; n >= 9 skips
the operand bytes of 1n and 4n but not of 3n (as the original does).
"""
from __future__ import annotations

import struct

PIT_HZ = 1193182
DEFAULT_DIVISOR = 6211                   # C:8B59 timer period before a song sets one
OP_OFFSET = (0, 1, 2, 8, 9, 10, 16, 17, 18)   # C:10F5, modulator register offset


class AdlxError(ValueError):
    pass


def parse(blob: bytes) -> dict:
    """D:0AA9 - checks the chunk chain and returns channels, divisor, stream."""
    def chunk(pos, tag):
        if blob[pos:pos + 4] != tag:
            raise AdlxError(f'expected {tag!r} at {pos}')
        return pos + 8
    hdr = chunk(8, b'AHDR')
    ahdr = blob[hdr:hdr + 10]
    imap = chunk(hdr + struct.unpack('>I', blob[hdr - 4:hdr])[0], b'IMAP')
    trck = chunk(imap + struct.unpack('>I', blob[imap - 4:imap])[0], b'TRCK')
    total = struct.unpack('>I', blob[4:8])[0] + 8
    return dict(channels=ahdr[2], divisor=struct.unpack('<H', ahdr[8:10])[0],
                stream=bytes(blob[trck:total]))


def blob_at(image: bytes, para: int) -> bytes:
    """The ADLX blob stored at a paragraph of the load image."""
    off = para * 16
    if image[off:off + 4] != b'ADLX':
        raise AdlxError(f'no ADLX blob at paragraph {para:#x}')
    return image[off:off + 8 + struct.unpack('>I', image[off + 4:off + 8])[0]]


class Channel:
    """Per-channel driver record (C:0278 + 0x14 * channel)."""
    __slots__ = ('inst', 'level', 'vol', 'note')

    def __init__(self):
        self.inst = bytes(12)            # +0x278: instrument bytes as loaded
        self.level = 0                   # +0x284: stream level (start/play argument)
        self.vol = 0                     # +0x286: channel volume (command 3n)
        self.note = 0                    # +0x28A: last An/Bn word


class Sequencer:
    """One stream on a freshly initialised driver.

    level  = the start_music / play_sfx volume argument (game: music 0xA5,
             effects 0xFF, the two frontend effects 0xE1)
    music  = True: channels 0.., stream divisor applied to the PIT
             False: an effect on the top channels (9 - channels ..)
    """

    def __init__(self, blob: bytes, level: int, music: bool):
        info = parse(blob)
        self.nch = info['channels']
        self.stream = info['stream']
        self.music = music
        self.base = 0 if music else 9 - self.nch
        self.level = min(level, 0xFF)
        self.ch = [Channel() for _ in range(9)]
        self.writes: list[tuple[int, int]] = []
        self.divisor = DEFAULT_DIVISOR if music else info['divisor']
        self.factor = 0x100              # effect delay scale (C:184A), 1.0 here
        self.time = 0                    # C:023A/023C, timer ticks
        self.pos = 0
        self.next = 0
        self.playing = False
        self.restarts = 0                # times the stream hit 77 and looped
        self._init()
        self._play()

    # ------------------------------------------------------ chip interface
    def _out(self, reg, val):            # C:11F2
        self.writes.append((reg, val & 0xFF))

    def _init(self):
        """sound_init: waveform select on, every operator muted."""
        self._out(0x01, 0x00)
        self._out(0x01, 0x20)
        for c in range(9):
            o = OP_OFFSET[c]
            self._out(0x40 + o, 0x3F)
            self._out(0x43 + o, 0x3F)

    def _set_level(self, c, level):      # C:1211: TL of the carrier (+ modulator if AM)
        ch = self.ch[c]
        ch.level = level
        o = OP_OFFSET[c]

        def tl(b):
            att = ((0x3F - (b & 0x3F)) * ch.vol) >> 8
            att = (att * level) >> 8
            return (0x3F - att) | (b & 0xC0)
        self._out(0x43 + o, tl(ch.inst[6]))
        if ch.inst[10] & 1:
            self._out(0x40 + o, tl(ch.inst[1]))

    def _key_off(self, c):               # C:14B1
        ch = self.ch[c]
        ch.note &= ~0x2000
        self._out(0xB0 + c, ch.note >> 8)

    def _note(self, c, word):            # C:1503
        self.ch[c].note = word
        self._out(0xA0 + c, word & 0xFF)
        self._out(0xB0 + c, word >> 8)

    def _volume(self, c, vol):           # C:1571
        self.ch[c].vol = vol
        self._set_level(c, self.ch[c].level)

    def _instrument(self, c, data):      # C:1377
        ch = self.ch[c]
        ch.inst = bytes(data[:12]).ljust(12, b'\0')
        b = ch.inst
        o = OP_OFFSET[c]
        self._out(0x20 + o, b[0])
        self._out(0x40 + o, b[1] | (0x3F if b[10] & 1 else 0))
        self._out(0x60 + o, b[2])
        self._out(0x80 + o, b[3])
        self._out(0xE0 + o, b[4])
        o += 3
        self._out(0x20 + o, b[5])
        self._out(0x40 + o, b[6] | 0x3F)
        self._out(0x60 + o, b[7])
        self._out(0x80 + o, b[8])
        self._out(0x01, 0x20)
        self._out(0xE0 + o, b[9])
        self._out(0xC0 + c, b[10])
        self._set_level(c, ch.level)

    # ------------------------------------------------------------ control
    def _play(self):
        """D:0CFC (+ D:1128 for music): silence and key off the stream's
        channels, set their level, start 3 ticks from now."""
        for c in range(self.base, self.base + self.nch):
            self._set_level(c, 0)
            self._key_off(c)
            self.ch[c].level = self.level
        if self.music:
            for c in range(self.nch):
                self._set_level(c, self.level)
        self.playing = True
        self.next = self.time + 3

    def _stop(self):
        """D:1768 + D:14BB for an effect: key off, then mute (TL = 3F)."""
        self.playing = False
        for c in range(self.base, self.base + self.nch):
            self._key_off(c)
        for c in range(self.base, self.base + self.nch):
            self._key_off(c)
            self._set_level(c, 0)

    def _wait(self, ticks):
        self.next += (ticks * self.factor) >> 8

    def _run(self, loop: bool):
        """Interpret commands until a wait or the end (C:15D3 / C:176B)."""
        s = self.stream
        while True:
            b = s[self.pos]
            self.pos += 1
            op, n = b >> 4, b & 0x0F
            c = n + self.base
            if op == 0:
                self._wait(((n << 8) | s[self.pos]) << 7)
                self.pos += 1
                return
            if op >= 8:
                self._wait(b & 0x7F)
                return
            if op == 1:
                if c < 9:
                    self._note(c, s[self.pos] | (s[self.pos + 1] << 8))
                    self.pos += 2
            elif op == 2:
                if c < 9:
                    self._key_off(c)
            elif op == 3:
                if c < 9:
                    self._volume(c, s[self.pos])
                self.pos += 1
            elif op == 4:
                if c < 9:
                    self._instrument(c, s[self.pos:self.pos + 12])
                    self.pos += 11
            elif op == 6:
                div = (s[self.pos] << 8) | s[self.pos + 1]
                self.pos += 2
                if self.music:
                    self.divisor = div
            elif op == 7:
                if b == 0x77:
                    self.restarts += 1
                    if loop:
                        self.pos = 0
                        if self.music:
                            self.next = self.time
                    else:
                        self._stop()
                    return
                skip = s[self.pos]
                self.pos += 1
                self.pos += skip if n else 2

    def tick(self, loop: bool) -> list[tuple[int, int]]:
        """One timer interrupt (C:1939); returns the register writes."""
        self.writes = []
        self.time += 1
        if self.playing and self.time >= self.next:
            self._run(loop)
        return self.writes

    def take_writes(self) -> list[tuple[int, int]]:
        w, self.writes = self.writes, []
        return w
