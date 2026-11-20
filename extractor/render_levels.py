#!/usr/bin/env python3
"""Render levels of UGH.EXE to PNG.

Usage: python3 render_levels.py [UGH.EXE] [outdir] [first] [last] [mode]
  first/last are level numbers (1-based, default 1..5), mode is 1 or 2.
Writes level_NN.png (plain, 320x192) and level_NN_marked.png (pads and start
positions overlaid), both scaled 2x.
"""
import os
import struct
import sys

try:
    from .decode_levels import Game
    from .extract_assets import (BANKS, PAL_PARA, SPRITE_TABLE_LEN,
                                SPRITE_TABLE_PARA, load_image, vga6to8, write_png)
except ImportError:
    from decode_levels import Game
    from extract_assets import (BANKS, PAL_PARA, SPRITE_TABLE_LEN,
                                SPRITE_TABLE_PARA, load_image, vga6to8, write_png)

SCALE = 2
W, H = 320, 192


def load_sprites(img):
    table = img[SPRITE_TABLE_PARA * 16:SPRITE_TABLE_PARA * 16 + SPRITE_TABLE_LEN]
    banks = [img[p * 16:p * 16 + n] for _, p, n in BANKS]

    def sprite(sid):
        off, w, h = struct.unpack_from('<HBB', table, sid * 4)
        bank = max(i for i, b in enumerate(BANKS) if sid >= b[0])
        col = banks[bank][off:off + w * h]          # column-major
        return w, h, [[col[x * h + y] for x in range(w)] for y in range(h)]
    return sprite


def box(fb, x, y, r, c):
    for yy in range(y - r, y + r + 1):
        for xx in range(x - r, x + r + 1):
            if 0 <= xx < W and 0 <= yy < H:
                fb[yy * W + xx] = c


def scaled(fb):
    out = bytearray()
    for y in range(H):
        row = b''.join(bytes([p]) * SCALE for p in fb[y * W:(y + 1) * W])
        out += row * SCALE
    return out


def main():
    a = sys.argv[1:]
    exe = a[0] if a else 'UGH.EXE'
    outdir = a[1] if len(a) > 1 else 'renders'
    first = int(a[2]) if len(a) > 2 else 1
    last = int(a[3]) if len(a) > 3 else 5
    mode = int(a[4]) if len(a) > 4 else 1
    img = load_image(exe)
    pal = vga6to8(img[PAL_PARA * 16:PAL_PARA * 16 + 384] * 2)   # 128 colours, duplicated (CS:0x641A)
    pal = pal[:765] + bytes([255, 255, 255])         # 255 = marker colour
    sprite = load_sprites(img)
    levels = Game(exe).decode_all()[mode]
    os.makedirs(outdir, exist_ok=True)
    for lv in levels[first - 1:last]:
        fb = bytearray(W * H)
        for r, row in enumerate(lv['tiles']):
            for c, t in enumerate(row):
                w, h, px = sprite(t)
                for y in range(h):
                    fb[(r * 12 + y) * W + c * 16:(r * 12 + y) * W + c * 16 + w] = bytes(px[y])
        n = lv['number']
        write_png(os.path.join(outdir, 'level_%02d.png' % n), W * SCALE, H * SCALE,
                  scaled(fb), pal)
        for p in lv['pads']:
            box(fb, p['x'], p['y'], 2, 255)
        for s in (lv['start1'], lv['start2']):
            box(fb, int(s['x']), int(s['y']), 3, 254)
        write_png(os.path.join(outdir, 'level_%02d_marked.png' % n), W * SCALE,
                  H * SCALE, scaled(fb), pal[:762] + bytes([255, 0, 0, 255, 255, 255]))
        print('level %d %s (screen %d, %d pads)' % (n, lv['name'], lv['screen'], len(lv['pads'])))


if __name__ == '__main__':
    main()
