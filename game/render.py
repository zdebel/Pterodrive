"""Software renderer: 320x200 8-bit frame (320x192 playfield + 8-line HUD).

Follows the draw passes of the original main loop (F=0x2B97..0x2D4E), in this
order over the background page:
  1. passengers + stones (B list order)      sprite at (x>>5, y>>5)
  2. trees and boulders (hazard list)
  3. crafts                                  sprite DS:27E8 at (DS:27D8, DS:27DC)
  4. pterodactyls and dinosaurs
  5. fruit, slot 11 down to 0
  6. speech bubbles (B list order)           sprite DS:2C2D at (px + 11, py - 13)
  7. weather particles (game/weather.py), one pixel of colour 0x5B each
  8. the water line row: one of three unrolled 320-pixel wave patterns
     (F=0x4BB3 / 0x4F7D / 0x5347), phase changes every 7 iterations
  9. the status strip (game/hud.py)
Water tint: every sprite pixel on a row below the water line, and every
background pixel row the line has left behind, gets palette index | 0x40.
"""
from __future__ import annotations

import struct

from extractor import PAL_PARA, load_image

W, H, PLAY_H = 320, 200, 192


def palette_from_dac(dac) -> bytes:
    """Convert VGA DAC components to pygame RGB exactly as the port requires.

    The original state stores six-bit components.  Runtime presentation uses a
    straight multiply by four, not the bit-replicating ``vga6to8`` helper used
    when exporting standalone assets.
    """
    return bytes(int(component) * 4 for entry in dac for component in entry)


def build_palette(exe: str):
    """Build the unfaded game palette for tools that do not own a GameState."""
    img = load_image(exe)
    palette6 = img[PAL_PARA * 16:PAL_PARA * 16 + 384] * 2
    return bytes(component * 4 for component in palette6)


def wave_patterns(img: bytes):
    """Decode the three unrolled wave routines of CS:0x4B69 into 320-pixel rows.
    Each variant is 4 blocks (planes 0..3) of `mov [di+n], reg` with
    `shl al,1 / out dx,al` between them; bh=0x56 bl=0x55 ch=0x58 cl=0x57 ah=0x54."""
    cs = 0x13B0
    regs = {7: 0x56, 3: 0x55, 5: 0x58, 1: 0x57, 4: 0x54}
    pats = []
    for v in range(3):
        pos = cs + struct.unpack_from('<H', img, cs + 0x2DB3 + 2 * v)[0]
        row, plane = [0] * 320, 0
        while True:
            b = img[pos]
            if b == 0x88:
                modrm = img[pos + 1]
                mod, reg = modrm >> 6, (modrm >> 3) & 7
                assert modrm & 7 == 5, 'unexpected addressing in wave code'
                if mod == 0:
                    disp, n = 0, 2
                elif mod == 1:
                    disp, n = img[pos + 2], 3
                else:
                    disp, n = struct.unpack_from('<H', img, pos + 2)[0], 4
                row[disp * 4 + plane] = regs[reg]
                pos += n
            elif b == 0xD0 and img[pos + 1] == 0xE0 and img[pos + 2] == 0xEE:
                plane += 1                         # shl al,1 / out dx,al : next plane
                pos += 3
            elif b == 0xB0 and img[pos + 2] == 0xEE:
                if img[pos + 1] == 0x0F:           # mov al,0x0F / out dx,al : done
                    break
                plane = img[pos + 1].bit_length() - 1
                pos += 3
            else:
                raise ValueError('unexpected opcode %02x in wave code' % b)
        pats.append(row)
    return pats


class Renderer:
    def __init__(self, exe: str, sprite):
        self.pal = build_palette(exe)
        self.sprite = sprite
        self._cache = {}
        self.wave = wave_patterns(load_image(exe))

    def _spr(self, sid):
        s = self._cache.get(sid)
        if s is None:
            w, h, px = self.sprite(sid)
            s = self._cache[sid] = (w, h, [bytes(r) for r in px])
        return s

    def blit(self, fb, sid, x, y, water_line):
        """CS:0x5FAD for the playfield: colour 0 transparent, rows below the water
        line get +0x40; rows outside 0..191 and columns outside 0..319 are not
        visible."""
        if sid is None or sid < 0:
            return
        w, h, rows = self._spr(sid)
        for j in range(h):
            yy = y + j
            if not 0 <= yy < PLAY_H:
                continue
            tint = yy > water_line
            row = rows[j]
            base = yy * W
            for i in range(w):
                v = row[i]
                if v:
                    xx = x + i
                    if 0 <= xx < W:
                        fb[base + xx] = (v | 0x40) if tint else v

    def frame(self, sess) -> bytearray:
        st = sess.state
        self.pal = palette_from_dac(st.dac)
        wl = st.water.line
        fb = bytearray(st.bg) + bytearray(W * (H - PLAY_H))
        blit = self.blit
        # 1. passengers and stones
        for o in st.b_objects:
            if o.sprite is not None and o.sprite != -1:
                y = o.y >> 5
                if -11 < y < PLAY_H:
                    blit(fb, o.sprite, o.x >> 5, y, wl)
        # 2. trees and boulders, 4. pterodactyls and dinosaurs (crafts between)
        first = [h for h in st.hz.hazards if type(h).__name__ in ('Tree', 'Boulder')]
        second = [h for h in st.hz.hazards if type(h).__name__ not in ('Tree', 'Boulder')]
        for h in first:
            if h.sprite is not None and h.sprite != -1 and (h.y >> 5) < PLAY_H:
                blit(fb, h.sprite, h.x >> 5, h.y >> 5, wl)
        # 3. crafts (F=0x5F3D): stored pixel position, visible window only
        for c in st.crafts:
            if -20 < c.y_px < PLAY_H and -32 < c.x_px < W:
                blit(fb, c.anim_frame, c.x_px, c.y_px, wl)
        for h in second:
            if h.sprite is not None and h.sprite != -1 and (h.y >> 5) < PLAY_H:
                blit(fb, h.sprite, h.x >> 5, h.y >> 5, wl)
        # 5. fruit, highest slot first
        for f in st.hz.fruit:
            if (f.y >> 5) < PLAY_H:
                blit(fb, f.sprite, f.x >> 5, f.y >> 5, wl)
        # 6. speech bubbles
        for o in st.b_objects:
            b = getattr(o, 'bubble', -1)
            if o.sprite is not None and o.sprite != -1 and b != -1:
                blit(fb, b, (o.x >> 5) + 11, (o.y >> 5) - 13, wl)
        # 7. weather particles
        if st.particles:
            for x, y in st.particles.pixels(wl):
                if 0 <= x < W and 0 <= y < PLAY_H:
                    fb[y * W + x] = 0x5B
        # 8. water line row
        if 0 <= wl < PLAY_H:
            fb[wl * W:(wl + 1) * W] = bytes(self.wave[st.wave_i])
        # 9. status strip
        st.hud.render(fb)
        if sess.over:                            # red bar = game over, green = won
            col = 0x32 if sess.won else 0x20
            for y in range(94, 106):
                for x in range(100, 220):
                    fb[y * W + x] = col
        return fb
