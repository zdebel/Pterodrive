"""Title, attract, menu, level-card and high-score frontend of UGH.EXE.

The code in this module is a port of the main-code-segment routines at
F=0x1DE9..0x29B6.  It deliberately consumes the strings, sprite scripts,
tile screens and packed full-screen pictures from UGH.EXE instead of carrying
parallel hand-authored resources.

The pygame-facing :class:`Frontend` is a small modal controller.  All image
construction and state machines below it are pygame-independent so the frames
can be compared directly with the original planar-VGA output in Unicorn.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import struct

from extractor import (LEVEL_PARA, PAL_PARA, SCREEN_PARAS, Game, ice_unpack,
                       load_image, load_sprites)
from game.fade import FADED_ENTRIES, blank_dac
from game.render import H, W, palette_from_dac, wave_patterns

TICK_HZ = 70.0863
PLAY_H = 192

# DGROUP offsets used by the original frontend.
MAIN_TEXT = 0x0AE4
CONTROL_TEXT = 0x0B64
LEVEL_LABEL = 0x0BF6
PASSWORD_LABEL = 0x0C00
FAIL_TEXT = 0x0C0C
CONGRATS_TEXT = 0x0C23
SCORE_HEADING = 0x0C7B
SCORE_DATA = 0x0C86
SCORE_BYTES = 107
CREDITS = 0x091F

SCENES = {
    'title': (0x24DE, 0, 180, False),
    'ending': (0x1020, 0, 180, False),
    'game_intro': (0x1792, 0, 180, False),
    'second_intro': (0x1E40, 0x7080, 192, True),
}


def _u16(data: bytes, off: int) -> int:
    return struct.unpack_from('<H', data, off)[0]


def _s16(data: bytes, off: int) -> int:
    return struct.unpack_from('<h', data, off)[0]


def _c_string(data: bytes, off: int) -> bytes:
    return data[off:data.index(0, off)]


def _text_block(data: bytes, off: int) -> bytes:
    """Return one NUL-terminated frontend text block, including its NUL."""
    return data[off:data.index(0, off) + 1]


def faded_palette(palette6: bytes, factor: int) -> bytes:
    """F=0x6BE6/0x6C22: fade and load DAC entries 0..216.

    Frontend screen palettes contain all 256 entries, but the original loader
    intentionally updates only 0xD9 of them after blanking the DAC.
    """
    factor = max(0, min(0x100, factor))
    dac = blank_dac()
    for i in range(min(FADED_ENTRIES, len(palette6) // 3)):
        dac[i] = [((palette6[3 * i + c] * factor) & 0xFFFF) >> 8
                  for c in range(3)]
    return palette_from_dac(dac)


class FrontendAssets:
    """Decoded frontend resources, all sourced from the executable image."""

    def __init__(self, exe: str):
        self.exe = exe
        self.image = load_image(exe)
        self.ds = self.image[0x5C09 * 16:]
        self.sprite = load_sprites(self.image)
        self._sprite_cache = {}
        self.level_blob = ice_unpack(self.image[LEVEL_PARA * 16:])
        self.game_palette6 = bytes(
            self.image[PAL_PARA * 16:PAL_PARA * 16 + 384]) * 2
        self.wave = wave_patterns(self.image)
        self._screens = {}
        self._backgrounds = {}
        self.passwords = {
            1: self._string_list(_u16(self.ds, 0x00EA), 69),
            2: self._string_list(_u16(self.ds, 0x00EC), 81),
        }
        self.credit_pages = self._credits()

    def _string_list(self, off: int, count: int):
        out = []
        for _ in range(count):
            end = self.ds.index(0, off)
            out.append(self.ds[off:end].decode('cp437'))
            off = end + 1
        return out

    def _credits(self):
        pages, off = [], CREDITS
        while self.ds[off] != 0xFF:
            end = self.ds.index(0, off)
            pages.append(bytes(self.ds[off:end + 1]))
            off = end + 1
        return pages

    def spr(self, sid: int):
        item = self._sprite_cache.get(sid)
        if item is None:
            w, h, rows = self.sprite(sid)
            item = self._sprite_cache[sid] = (w, h, tuple(bytes(r) for r in rows))
        return item

    def splash(self, index: int):
        item = self._screens.get(index)
        if item is None:
            data = ice_unpack(self.image[SCREEN_PARAS[index] * 16:])
            item = self._screens[index] = (bytes(data[:W * H]), bytes(data[W * H:W * H + 768]))
        return item

    @staticmethod
    def blit(fb: bytearray, sprite, sid: int, x: int, y: int,
             water_line: int = H, width: int | None = None):
        if sid < 0 or sid == 0x7FFF:
            return
        w, h, rows = sprite(sid)
        if width is not None:
            w = max(0, min(w, width))
        for yy in range(max(0, y), min(H, y + h)):
            row = rows[yy - y]
            tint = yy > water_line
            start, stop = max(0, -x), min(w, W - x)
            base = yy * W + x
            for sx in range(start, stop):
                value = row[sx]
                if value:
                    fb[base + sx] = value | 0x40 if tint else value

    def background(self, blob_offset: int, water_line: int,
                   fixed_title: bool = False) -> bytes:
        key = blob_offset, water_line, fixed_title
        cached = self._backgrounds.get(key)
        if cached is not None:
            return cached
        fb = bytearray(W * H)
        tiles = self.level_blob[blob_offset:blob_offset + 320]
        for n, sid in enumerate(tiles):
            self.blit(fb, self.spr, sid, (n % 20) * 16, (n // 20) * 12,
                      water_line)
        if fixed_title:
            self.draw_fixed_title(fb, water_line)
        result = self._backgrounds[key] = bytes(fb)
        return result

    def draw_fixed_title(self, fb: bytearray, water_line: int = H):
        # F=0x24A7
        self.blit(fb, self.spr, 0x0D7, 16, 0, water_line)
        self.blit(fb, self.spr, 0x0D8, 227, 0, water_line)
        self.blit(fb, self.spr, 0x0D6, 97, 0, water_line)


class TextRenderer:
    """The 16-pixel sprite-font renderer at F=0x252C/0x2526."""

    _SPECIAL = {0x8E: 0x5B, 0x99: 0x5C, 0x9A: 0x5D}

    def __init__(self, assets: FrontendAssets):
        self.assets = assets

    def render(self, fb: bytearray, text: bytes, y: int,
               water_line: int = H) -> int:
        """Render through NUL and return the final Y coordinate.

        0xFE centers the following line, CR advances 16 pixels, and 0xFD
        advances eight pixels.  Each glyph occupies one 16-pixel cell.
        """
        off = 0
        while off < len(text):
            x = 0
            value = text[off]
            off += 1
            if value == 0xFE:
                end = off
                while end < len(text) and text[end] not in (0, 0x0D, 0xFD):
                    end += 1
                x = (W - (end - off) * 16) // 2
                if off >= len(text):
                    break
                value = text[off]
                off += 1
            while True:
                if value == 0:
                    return y
                if value == 0x0D:
                    y += 16
                    break
                if value == 0xFD:
                    y += 8
                    break
                value = self._SPECIAL.get(value, value)
                self.assets.blit(fb, self.assets.spr, 0x237 + value, x, y,
                                 water_line)
                x += 16
                if off >= len(text):
                    return y
                value = text[off]
                off += 1
        return y


@dataclass
class _ScriptObject:
    x: int
    y: int
    period: int
    callback: int
    stream: int
    delay: int = 1
    loop_value: int = 0
    loop_start: int = 0
    sprite: int = -1
    done: bool = False
    clip_width: int | None = None


class SpriteScript:
    """Generic attract-script machine at F=0x2787..0x289A."""

    def __init__(self, assets: FrontendAssets, table: int):
        self.assets = assets
        self.ds = assets.ds
        self.objects = []
        off = table
        while True:
            ptr = _u16(self.ds, off)
            off += 2
            if ptr == 0xFFFF:
                break
            self.objects.append(_ScriptObject(
                x=_s16(self.ds, ptr) << 5,
                y=_s16(self.ds, ptr + 2) << 5,
                period=_u16(self.ds, ptr + 4),
                callback=_u16(self.ds, ptr + 6),
                stream=ptr + 8))

    @property
    def done(self):
        return all(obj.done for obj in self.objects)

    def tick(self) -> bool:
        """Advance one tick.  True means a script callback requested fade-out."""
        fade_out = False
        for obj in self.objects:
            if obj.done:
                continue
            obj.delay -= 1
            if obj.delay:
                continue
            obj.delay = obj.period
            while True:
                value = _s16(self.ds, obj.stream)
                obj.stream += 2
                if value >= 0:
                    dx = int.from_bytes(self.ds[obj.stream:obj.stream + 1],
                                        'little', signed=True)
                    dy = int.from_bytes(self.ds[obj.stream + 1:obj.stream + 2],
                                        'little', signed=True)
                    obj.stream += 2
                    obj.sprite = value
                    obj.x += dx * 4
                    obj.y += dy * 4
                    if obj.callback == 0x0989 and value == 0x7FFF:
                        fade_out = True                 # F=0x2739
                    elif obj.callback == 0x099F:       # F=0x274F
                        clip_width = 320 - (obj.x >> 5)
                        if clip_width > 0x7F:
                            clip_width = 0x7F
                        obj.clip_width = clip_width & 0xFF
                    break
                if value != -0x8000:
                    obj.loop_value = value
                    obj.loop_start = obj.stream
                    continue
                if obj.loop_value == 0:
                    obj.done = True
                    break
                obj.loop_value = (obj.loop_value + 1) & 0xFFFF
                if obj.loop_value & 0x8000:
                    obj.loop_value -= 0x10000
                if obj.loop_value:
                    obj.stream = obj.loop_start
        return fade_out

    def draw(self, fb: bytearray, water_line: int):
        for obj in self.objects:
            self.assets.blit(fb, self.assets.spr, obj.sprite,
                             obj.x >> 5, obj.y >> 5, water_line,
                             obj.clip_width)


class HighScores:
    """The five-record inverted UGH!.HI format at F=0x1FC0..0x2279."""

    RECORDS = 5
    RECORD_SIZE = 21

    def __init__(self, path: str, defaults: bytes):
        self.path = path
        self.data = bytearray(defaults[:SCORE_BYTES])
        try:
            with open(path, 'rb') as src:
                stored = src.read(SCORE_BYTES)
            self.data[:len(stored)] = stored
            self._invert()
        except OSError:
            pass

    def _invert(self):
        for i in range(SCORE_BYTES):
            self.data[i] ^= 0xFF

    @staticmethod
    def score_text(score: int) -> bytes:
        # F=0x5CBF plus F=0x1FCF..0x1FF9: six digits, leading zeroes blanked.
        return ('%6d' % max(0, min(999999, int(score)))).encode('ascii')

    def records(self):
        out = []
        for i in range(self.RECORDS):
            record = bytes(self.data[i * self.RECORD_SIZE:(i + 1) * self.RECORD_SIZE])
            name = record[:12].decode('cp437').rstrip()
            try:
                score = int(record[14:20].decode('ascii').strip() or '0')
            except ValueError:
                score = 0
            out.append((name, score))
        return out

    def qualifying_index(self, score: int):
        value = self.score_text(score)
        for i in range(self.RECORDS):
            base = i * self.RECORD_SIZE
            if value >= bytes(self.data[base + 14:base + 20]):
                return i
        return None

    def insert_score(self, score: int):
        index = self.qualifying_index(score)
        if index is None:
            return None
        for i in range(self.RECORDS - 1, index, -1):
            src = (i - 1) * self.RECORD_SIZE
            dst = i * self.RECORD_SIZE
            self.data[dst:dst + 20] = self.data[src:src + 20]
        base = index * self.RECORD_SIZE
        self.data[base:base + 12] = b' ' * 12
        self.data[base + 14:base + 20] = self.score_text(score)
        self.data[base + 20] = 0x0D
        return index

    def set_name(self, index: int, name: str):
        value = ''.join(c for c in name.upper()
                        if c == ' ' or c.isascii() and c.isalnum())[:12]
        if not value:
            value = 'INCOGNITUS'
        base = index * self.RECORD_SIZE
        self.data[base:base + 12] = value.encode('ascii').ljust(12, b' ')

    def save(self):
        stored = bytes(value ^ 0xFF for value in self.data[:SCORE_BYTES])
        with open(self.path, 'wb') as dst:
            dst.write(stored)

    def text(self, heading: bytes) -> bytes:
        return heading + bytes(self.data[:self.RECORDS * self.RECORD_SIZE]) + b'\0'


@dataclass(frozen=True)
class StartRequest:
    mode: int
    difficulty: int
    level: int
    controllers: tuple[int, int] = (-1, -1)


class FrontendModel:
    """Pure frame construction and persistent menu state."""

    def __init__(self, exe: str, mode=1, difficulty=1, joy_state=None):
        self.assets = FrontendAssets(exe)
        self.text = TextRenderer(self.assets)
        self.mode = mode
        self.difficulty = difficulty
        self.password = 'NONE'
        self.joy_state = joy_state
        self.controllers = [
            joy_state.p1 if joy_state and joy_state.p1 < 0x8000 else -1,
            joy_state.p2 if joy_state and joy_state.p2 < 0x8000 else -1,
        ]
        hi = os.path.join(os.path.dirname(os.path.abspath(exe)), 'UGH!.HI')
        defaults = self.assets.ds[SCORE_DATA:SCORE_DATA + SCORE_BYTES]
        self.scores = HighScores(hi, defaults)

    def cycle_controller(self, player: int):
        """F=0x23CE..0x23EE: cycle controller between Keyboard and present sticks."""
        present = (
            bool(self.joy_state and self.joy_state.presentA == 0xFFFF),
            bool(self.joy_state and self.joy_state.presentB == 0xFFFF),
        )
        curr = self.controllers[player]
        cand = curr + 1
        while cand < 2:
            if cand in (0, 1) and present[cand]:
                self.controllers[player] = cand
                return
            cand += 1
        self.controllers[player] = -1

    @property
    def palette(self):
        return faded_palette(self.assets.game_palette6, 0x100)

    def menu_text(self):
        ds = self.assets.ds
        block = bytearray(_text_block(ds, MAIN_TEXT))
        difficulty = _c_string(ds, _u16(ds, 0x00DA + self.difficulty * 2))
        mode = _c_string(ds, _u16(ds, 0x00E0 + (self.mode - 1) * 2))
        block[0x0B0D - MAIN_TEXT:0x0B0D - MAIN_TEXT + len(difficulty)] = difficulty
        block[0x0B19 - MAIN_TEXT:0x0B19 - MAIN_TEXT + len(mode)] = mode
        shown = self.password.encode('ascii')[:20].ljust(20, b' ')
        block[0x0B4E - MAIN_TEXT:0x0B4E - MAIN_TEXT + 20] = shown
        return bytes(block)

    def menu_frame(self):
        fb = bytearray(W * H)
        self.assets.draw_fixed_title(fb, 175)
        self.text.render(fb, self.menu_text(), 72, 175)
        return fb

    def control_frame(self):
        ds = self.assets.ds
        block = bytearray(_text_block(ds, CONTROL_TEXT))
        for player, absolute in enumerate((0x0B73, 0x0B8D)):
            ptr = _u16(ds, 0x00E4 + (self.controllers[player] + 1) * 2)
            value = _c_string(ds, ptr)
            start = absolute - CONTROL_TEXT
            block[start:start + len(value)] = value
        fb = bytearray(W * H)
        self.text.render(fb, bytes(block), 80, 200)
        return fb

    def credits_frame(self, page: bytes):
        fb = bytearray(W * H)
        self.assets.draw_fixed_title(fb, 200)
        breaks = sum(1 for value in page if value in (0x0D, 0xFD)) + 1
        y = ((144 - breaks * 16) // 2) + 56
        self.text.render(fb, page, y, 200)
        return fb

    def high_score_frame(self):
        fb = bytearray(W * H)
        self.assets.draw_fixed_title(fb, 200)
        heading = bytes(self.assets.ds[SCORE_HEADING:SCORE_DATA])
        self.text.render(fb, self.scores.text(heading), 80, 200)
        return fb

    def editor_frame(self, prompt: bytes, value: str, cursor: bool,
                     y: int, width: int, menu=False, fixed_title=False):
        fb = bytearray(W * H)
        if fixed_title:
            self.assets.draw_fixed_title(fb, 200)
        if menu:
            self.text.render(fb, self.menu_text(), 72, 175)
        else:
            self.text.render(fb, prompt, 80, 200)
        display = value.upper()[:width].ljust(width, ' ')
        if cursor and len(value) < width:
            display = display[:len(value)] + '?' + display[len(value) + 1:]
        # Both editors clear and redraw one 16-pixel-high strip.
        start = y * W
        fb[start:start + 16 * W] = bytes(16 * W)
        self.text.render(fb, display.encode('ascii') + b'\0', y, 200)
        return fb

    def failure_frame(self):
        fb = bytearray(W * H)
        self.text.render(fb, _text_block(self.assets.ds, FAIL_TEXT), 72, 200)
        return fb

    def level_for_password(self):
        value = self.password.strip().upper()
        try:
            return self.assets.passwords[self.mode].index(value) + 1
        except ValueError:
            return 1

    def raw_level_text(self, mode: int, level: int):
        decoded = Game(self.assets.exe).decode_all()[mode][level - 1]
        desc = int(decoded['descriptor'], 16)
        ptr = _u16(self.assets.ds, desc + 10)
        return _text_block(self.assets.ds, ptr)

    def level_card_frame(self, mode: int, level: int):
        fb = bytearray(W * H)
        label = bytearray(_text_block(self.assets.ds, LEVEL_LABEL))
        label[-3:-1] = ('%02d' % level).encode('ascii')
        self.text.render(fb, bytes(label), 32, 175)
        self.text.render(fb, self.raw_level_text(mode, level), 64, 175)
        y = self.text.render(fb, _text_block(self.assets.ds, PASSWORD_LABEL), 160, 175)
        password = self.assets.passwords[mode][level - 1].encode('cp437') + b'\0'
        self.text.render(fb, password, y, 175)
        return fb

    def scene(self, name: str):
        table, background, water, fixed = SCENES[name]
        return (bytearray(self.assets.background(background, water, fixed)),
                SpriteScript(self.assets, table), water)


class Frontend:
    """Pygame controller for the original frontend flow."""

    SPLASH_ORDER = (1, 0, 2, 3)       # F=0x2968
    SPLASH_HOLDS = (0x118, 0x118, 0x8C, 0x1A4)

    def __init__(self, exe: str, mode=1, difficulty=1, joy_state=None, stick=None, sound=None,
                 tick_hz: float = TICK_HZ):
        self.model = FrontendModel(exe, mode, difficulty, joy_state=joy_state)
        self.joy_state = joy_state
        self.stick = stick
        self.sound = sound
        self.tick_hz = tick_hz
        self.started = False
        self.running = True
        self.screen = None
        self.surface = None
        self._clock = None

    def bind(self, screen):
        import pygame
        self.screen = screen
        self.surface = pygame.Surface((W, H), depth=8)
        self._clock = pygame.time.Clock()

    def _events(self):
        import pygame
        keys = []
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_F11:
                    pygame.display.toggle_fullscreen()
                else:
                    keys.append(event)
            elif event.type in (pygame.JOYBUTTONDOWN, pygame.MOUSEBUTTONDOWN):
                keys.append(event)
        return keys

    @staticmethod
    def _scan(event):
        import pygame
        if not hasattr(event, 'key'):
            return None
        k = event.key
        sc = getattr(event, 'scancode', None)
        if k == pygame.K_ESCAPE or sc == getattr(pygame, 'KSCAN_ESCAPE', None):
            return 0x01
        if k == pygame.K_q or sc == getattr(pygame, 'KSCAN_Q', None):
            return 0x10
        if k == pygame.K_c or sc == getattr(pygame, 'KSCAN_C', None):
            return 0x2E
        if k == pygame.K_F1 or sc == getattr(pygame, 'KSCAN_F1', None):
            return 0x3B
        if k == pygame.K_F2 or sc == getattr(pygame, 'KSCAN_F2', None):
            return 0x3C
        if k == pygame.K_F3 or sc == getattr(pygame, 'KSCAN_F3', None):
            return 0x3D
        if k == pygame.K_F4 or sc == getattr(pygame, 'KSCAN_F4', None):
            return 0x3E
        if k == pygame.K_F5 or sc == getattr(pygame, 'KSCAN_F5', None):
            return 0x3F
        return None

    def _present(self, frame: bytes, palette: bytes):
        import pygame
        self.surface.set_palette([tuple(palette[i:i + 3]) for i in range(0, 768, 3)])
        buf = self.surface.get_buffer()
        buf.write(bytes(frame))
        del buf
        sw, sh = self.screen.get_size()
        bw, bh = (sw, sw * 3 // 4) if sw * 3 <= sh * 4 else (sh * 4 // 3, sh)
        scaled = pygame.transform.scale(self.surface.convert(), (bw, bh))
        self.screen.fill((0, 0, 0))
        self.screen.blit(scaled, ((sw - bw) // 2, (sh - bh) // 2))
        pygame.display.flip()

    def _tick(self):
        if self.sound:
            self.sound.tick(1.0 / self.tick_hz)
        self._clock.tick(self.tick_hz)

    def _fade(self, frame, palette6, fade_in=True):
        factors = range(0, 0x101, 4) if fade_in else range(0x100, -1, -4)
        pressed = []
        for factor in factors:
            pressed.extend(self._events())
            if not self.running:
                break
            self._present(frame, faded_palette(palette6, factor))
            self._tick()
        return pressed

    def _hold(self, frame, palette, ticks, stop_on_key=True):
        for _ in range(ticks):
            keys = self._events()
            if not self.running or keys and stop_on_key:
                return keys
            self._present(frame, palette)
            self._tick()
        return []

    def _stop_music(self, frame):
        """stop_music blocks until the music has faded out; the screen is
        still black (the next fade-in comes after it)."""
        wait = self.sound.stop_music()
        self._hold(frame, bytes(768), round(wait * self.tick_hz), stop_on_key=False)

    def _startup(self):
        result = None
        for index, hold in zip(self.SPLASH_ORDER, self.SPLASH_HOLDS):
            if not self.running:
                break
            frame, palette6 = self.model.assets.splash(index)
            keys = self._fade(frame, palette6, True)
            if not self.running:
                break
            keys += self._hold(frame, faded_palette(palette6, 0x100), hold)
            if not self.running:
                break
            self._fade(frame, palette6, False)
            for event in keys:
                scan = self._scan(event)
                if scan == 0x10 or not self.running:
                    self.running = False
                    return -1
                if scan is not None:
                    return 1
        return result

    def play_scene(self, name: str):
        if not self.started:
            self.started = True
            self._startup()
        return self._scene(name)

    def _scene(self, name):
        if self.sound:
            from game.sound import TRACK_TITLE, TRACK_ENDING
            if name == 'title':
                self.sound.start_music(TRACK_TITLE)
            elif name == 'ending':
                self.sound.start_music(TRACK_ENDING)
        base, script, water = self.model.scene(name)
        factor, step, wave_count, wave_index = 0, 4, 0, 0
        result = 0
        while self.running and factor >= 0:
            for event in self._events():
                scan = self._scan(event)
                if scan == 0x10 or not self.running:
                    self.running = False
                    result = -1
                elif scan == 0x01:
                    result = 1
                if step >= 0:
                    step = -4
                    factor += step
            frame = bytearray(base)
            if water < PLAY_H:
                frame[water * W:(water + 1) * W] = bytes(self.model.assets.wave[wave_index])
                wave_count -= 1
                if wave_count < 0:
                    wave_count = 6
                    wave_index = (wave_index - 1) % 3
            if script.tick() or script.done:
                if step >= 0:
                    step = -4
                    factor += step
            script.draw(frame, water)
            self._present(frame, faded_palette(self.model.assets.game_palette6, factor))
            if factor <= 0x100:
                factor += step
            self._tick()
        if not self.running or result < 0:
            self.running = False
            return -1
        return result

    def _credits(self):
        for page in self.model.assets.credit_pages:
            if not self.running:
                return -1
            frame = self.model.credits_frame(page)
            keys = self._fade(frame, self.model.assets.game_palette6, True)
            if not self.running:
                return -1
            keys += self._hold(frame, self.model.palette, 0x15E)
            if not self.running:
                return -1
            self._fade(frame, self.model.assets.game_palette6, False)
            if keys:
                scan = self._scan(keys[-1])
                if scan == 0x10 or not self.running:
                    self.running = False
                    return -1
                if scan == 0x01:
                    return 1
                return 0
        return 0

    def _high_scores(self, interactive=True):
        if not self.running:
            return -1
        frame = self.model.high_score_frame()
        keys = self._fade(frame, self.model.assets.game_palette6, True)
        if not self.running:
            return -1
        keys += self._hold(frame, self.model.palette, 0x578, interactive)
        if not self.running:
            return -1
        self._fade(frame, self.model.assets.game_palette6, False)
        if keys:
            scan = self._scan(keys[-1])
            if scan == 0x10 or not self.running:
                self.running = False
                return -1
            if scan == 0x01:
                return 1
            return 0
        return 0

    def _edit(self, prompt: bytes, initial: str, width: int, y: int,
              menu=False, fixed_title=False, allow_space=False):
        import pygame
        value, tick = initial, 0
        while self.running:
            for event in self._events():
                if not hasattr(event, 'key'):
                    continue
                if event.key == pygame.K_BACKSPACE:
                    value = value[:-1]
                elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    return value or ('NONE' if width == 20 else 'INCOGNITUS')
                elif event.key == pygame.K_ESCAPE:
                    return initial
                elif len(value) < width:
                    char = event.unicode.upper()
                    if len(char) == 1 and ((allow_space and char == ' ') or
                                           char.isascii() and char.isalnum()):
                        value += char
            cursor = ((-tick - 1) & 0x10) != 0
            frame = self.model.editor_frame(prompt, value, cursor, y, width,
                                            menu, fixed_title)
            self._present(frame, self.model.palette)
            self._tick()
            tick += 1
        return value

    def _controls(self):
        if not self.running:
            return
        frame = self.model.control_frame()
        events = list(self._fade(frame, self.model.assets.game_palette6, True))
        if not self.running:
            return
        ticks = 0
        while self.running and ticks < 0x834:
            events.extend(self._events())
            for event in events:
                if not self.running:
                    return
                scan = self._scan(event)
                if scan == 0x01:  # Esc (0x01) -> return to menu
                    self._fade(frame, self.model.assets.game_palette6, False)
                    return
                if scan == 0x10:  # Q (0x10) -> quit
                    self.running = False
                    self._fade(frame, self.model.assets.game_palette6, False)
                    return
                if scan == 0x3B:  # F1 (0x3B) -> Player 1 controller
                    self.model.cycle_controller(0)
                    frame = self.model.control_frame()
                    ticks = 0
                elif scan == 0x3C:  # F2 (0x3C) -> Player 2 controller
                    self.model.cycle_controller(1)
                    frame = self.model.control_frame()
                    ticks = 0
                else:
                    ticks = 0
            events.clear()
            self._present(frame, self.model.palette)
            self._tick()
            ticks += 1
        self._fade(frame, self.model.assets.game_palette6, False)

    def _menu(self):
        if not self.running:
            return None
        frame = self.model.menu_frame()
        # Collect any keys pressed during fade-in so they are not dropped
        events = list(self._fade(frame, self.model.assets.game_palette6, True))
        if not self.running:
            return None
        ticks = 0
        while self.running and ticks < 0x834:
            events.extend(self._events())
            for event in events:
                if not self.running:
                    return None
                scan = self._scan(event)
                if scan in (0x01, 0x10):  # Esc (0x01) or Q (0x10) -> fade out and quit
                    self.running = False
                    self._fade(frame, self.model.assets.game_palette6, False)
                    return None
                if scan == 0x2E:  # C (0x2E) -> fade out and restart intro cycle
                    self._fade(frame, self.model.assets.game_palette6, False)
                    return None
                if scan == 0x3B:  # F1 (0x3B) -> Start Game
                    self._fade(frame, self.model.assets.game_palette6, False)
                    return StartRequest(self.model.mode, self.model.difficulty,
                                        self.model.level_for_password(),
                                        controllers=tuple(self.model.controllers))
                if scan == 0x3C:  # F2 (0x3C) -> Password editor
                    self.model.password = self._edit(
                        _text_block(self.model.assets.ds, MAIN_TEXT), '', 20, 176,
                        menu=True)
                    frame = self.model.menu_frame()
                    ticks = 0
                elif scan == 0x3D:  # F3 (0x3D) -> Difficulty
                    self.model.difficulty = (self.model.difficulty + 1) % 3
                    frame = self.model.menu_frame()
                    ticks = 0
                elif scan == 0x3E:  # F4 (0x3E) -> Mode (1 / 2 players)
                    self.model.mode ^= 3
                    frame = self.model.menu_frame()
                    ticks = 0
                elif scan == 0x3F:  # F5 (0x3F) -> Controls
                    self._fade(frame, self.model.assets.game_palette6, False)
                    self._controls()
                    if not self.running:
                        return None
                    frame = self.model.menu_frame()
                    self._fade(frame, self.model.assets.game_palette6, True)
                    ticks = 0
                else:
                    # In DOS UGH.EXE (F=0x1E88..0x1EFD): any other key resets the timer
                    ticks = 0
            events.clear()
            self._present(frame, self.model.palette)
            self._tick()
            ticks += 1
        self._fade(frame, self.model.assets.game_palette6, False)
        return None

    def run(self):
        if not self.started:
            self.started = True
            self._startup()
        while self.running:
            status = self._scene('title')
            if status < 0 or not self.running:
                break
            if not status:
                status = self._credits()
            if status < 0 or not self.running:
                break
            if not status:
                status = self._scene('second_intro')
            if status < 0 or not self.running:
                break
            if not status:
                status = self._high_scores()
            if status < 0 or not self.running:
                break
            request = self._menu()
            if not self.running:
                break
            if request is not None:
                self._scene('game_intro')
                if not self.running:
                    return None
                return request
        return None

    def show_level(self, mode: int, level: int):
        if not self.running:
            return False
        frame = self.model.level_card_frame(mode, level)
        if self.sound:                           # F=0x247D..0x2497
            from game.sound import SFX_IMPACT
            self._stop_music(frame)
            self.sound.play_sfx(SFX_IMPACT)
        keys = list(self._fade(frame, self.model.assets.game_palette6, True))
        if not self.running:
            return False
        if not keys:
            keys = self._hold(frame, self.model.palette, 0x7FFFFFFF, True)
        if not self.running:
            return False
        self._fade(frame, self.model.assets.game_palette6, False)
        return self.running

    def finish_game(self, score: int, won: bool):
        if won:
            self._scene('ending')
        else:
            frame = self.model.failure_frame()
            if self.sound:                       # F=0x1F2D..0x1F47
                from game.sound import SFX_SPLASH
                self._stop_music(frame)
                self.sound.play_sfx(SFX_SPLASH)
            self._fade(frame, self.model.assets.game_palette6, True)
            self._hold(frame, self.model.palette, 0x2BC)
            self._fade(frame, self.model.assets.game_palette6, False)
        index = self.model.scores.insert_score(score)
        if index is None or not self.running:
            return
        if self.sound:
            from game.sound import TRACK_ENDING
            self.sound.start_music(TRACK_ENDING)
        frame = bytearray(W * H)
        prompt = _text_block(self.model.assets.ds, CONGRATS_TEXT)
        self.model.text.render(frame, prompt, 80, 200)
        self._fade(frame, self.model.assets.game_palette6, True)
        name = self._edit(prompt, '', 12, 160, fixed_title=True,
                          allow_space=True)
        self._fade(frame, self.model.assets.game_palette6, False)
        self.model.scores.set_name(index, name)
        self.model.scores.save()
        self._high_scores()
