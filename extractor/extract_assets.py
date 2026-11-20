#!/usr/bin/env python3
"""Extract embedded assets from UGH.EXE (Bones Park / PlayByte, 1992).

Usage:
  python3 extract_assets.py [path/to/UGH.EXE] [outdir]

Everything lives inside the MZ load image; the only external file the game
touches is the high-score table UGH!.HI. Paragraph offsets are relocated
segment values found in the 16-bit code.

Output (all under outdir):
  game.pal                 768 B VGA palette (6-bit), referenced by DS:0xB3
  screens/screen_N.png     the four ICE!-packed 320x200 full-screen images
  screens/screen_N.raw     64000 B chunky pixels      screens/screen_N.pal
  levels.bin               ICE!-unpacked 32000 B level/map block
  music/adlx_XXXX.adlx     AdLib 'ADLX' music / sfx blobs (raw, not decoded)
  sprites/NNNN.png         every sprite (indexed, colour 0 = transparent)
  sprites/bank_N.png       contact sheet per sprite bank
  sprites/sprites.json     id, bank, offset, width, height
  highscores.txt           decoded UGH!.HI if found next to the EXE
"""
from __future__ import annotations

import json
import os
import struct
import sys
import zlib

# ---------------------------------------------------------------- locations
PAL_PARA = 0x2BC6                 # 768 B game palette (CODE_9)
SCREEN_PARAS = [0x2BF6, 0x2D9A, 0x2F9E, 0x2FB7]   # 'ICE!' blobs -> 64768 B
LEVEL_PARA = 0x1A38               # 'ICE!' blob -> 32000 B
ADLX_PARAS = [0x325B, 0x3260, 0x3265, 0x326B, 0x3274, 0x3279,
              0x3444, 0x3629, 0x3632, 0x3637]
ADLX_END_PARA = 0x37A7            # first sprite bank follows the last ADLX
# sprite banks: (first sprite id, paragraph, byte length)
BANKS = [(0x000, 0x1E1F, 0x37A7 * 16 - 0x1E1F * 16),
         (0x0D9, 0x37A7, 0x438A * 16 - 0x37A7 * 16),
         (0x12A, 0x438A, 0x4E83 * 16 - 0x438A * 16),
         (0x223, 0x4E83, 0x5B63 * 16 - 0x4E83 * 16)]
SPRITE_TABLE_PARA = 0x5B63        # 4 B per sprite: u16 offset, u8 w, u8 h
SPRITE_TABLE_LEN = 0x5C09 * 16 - 0x5B63 * 16      # up to DGROUP


# ------------------------------------------------------------------ helpers
def load_image(path: str) -> bytes:
    """Load MZ executable image into memory, resolving fallback paths if needed."""
    if not os.path.exists(path):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates = [
            os.path.join(root, 'assets', os.path.basename(path)),
            os.path.join(root, 'assets', 'UGH.EXE'),
            os.path.join(root, 'UGH.EXE'),
            os.path.join(os.getcwd(), 'assets', 'UGH.EXE'),
            os.path.join(os.getcwd(), 'UGH.EXE'),
        ]
        for c in candidates:
            if os.path.exists(c):
                path = c
                break
        else:
            raise FileNotFoundError(f'Executable file not found: {path}')

    with open(path, 'rb') as f:
        d = f.read()
    if d[:2] not in (b'MZ', b'ZM'):
        raise ValueError(f'Not an MZ executable: {path}')
    hdr = struct.unpack_from('<H', d, 8)[0] * 16
    return d[hdr:]


def _find_ci(directory: str, name: str) -> str | None:
    """File `name` in `directory`, ignoring case (DOS names come in any case)."""
    try:
        for e in sorted(os.listdir(directory)):
            if e.lower() == name.lower() and os.path.isfile(os.path.join(directory, e)):
                return os.path.join(directory, e)
    except OSError:
        pass
    return None


def vga6to8(pal: bytes | bytearray) -> bytes:
    """Convert 6-bit VGA palette to 8-bit RGB."""
    return bytes((v << 2) | (v >> 4) for v in pal)


def write_png(path: str, w: int, h: int, pix: bytes | bytearray,
              pal: bytes | bytearray | None = None, transparent0: bool = False):
    """8-bit indexed PNG (pal = 768 B RGB 8-bit) or greyscale."""
    raw = b''.join(b'\0' + bytes(pix[y * w:(y + 1) * w]) for y in range(h))

    def chunk(t: bytes, data: bytes) -> bytes:
        c = struct.pack('>I', len(data)) + t + data
        return c + struct.pack('>I', zlib.crc32(t + data) & 0xFFFFFFFF)

    out = b'\x89PNG\r\n\x1a\n'
    out += chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8,
                                      3 if pal else 0, 0, 0, 0))
    if pal:
        out += chunk(b'PLTE', bytes(pal))
        if transparent0:
            out += chunk(b'tRNS', b'\0')
    out += chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b'')
    with open(path, 'wb') as f:
        f.write(out)


# --------------------------------------------------- Pack-Ice 'ICE!' unpacker
# Port of the routine at CS:0x6A5D of UGH.EXE (backward bit-stream decoder).
_LIT_BITS = (1, 1, 2, 7, 14)           # bits-1 for each literal-count level
_LIT_BASE = (1, 4, 7, 14, 269)
_LIT_TERM = (3, 3, 7, 255, 32767)
_LEN_BITS = {3: -1, 2: -1, 1: 0, 0: 1, -1: 9}
_LEN_BASE = {3: 0, 2: 1, 1: 2, 0: 4, -1: 8}
_OFF_BITS = {1: 7, 0: 4, -1: 11}
_OFF_BASE = {1: 31, 0: -1, -1: 287}


def _s16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def ice_unpack(blob: bytes) -> bytes:
    """blob = 'ICE!' + u32be packed_size + u32be orig_size + data."""
    if blob[:4] != b'ICE!':
        raise ValueError('missing ICE! magic')
    psize, osize = struct.unpack('>II', blob[4:12])
    out = bytearray(osize)
    si = psize - 1
    ah = blob[si]
    si -= 1
    di = osize - 1

    def bit() -> int:
        nonlocal ah, si
        c = (ah >> 7) & 1
        ah = (ah << 1) & 0xFF
        if ah == 0:
            n = blob[si]
            si -= 1
            ah = ((n << 1) | c) & 0xFF
            c = (n >> 7) & 1
        return c

    def bits(cx: int) -> int:
        v = 0
        for _ in range(cx + 1):
            v = ((v << 1) | bit()) & 0xFFFF
        return v

    while True:
        if bit():                       # literal run
            if not bit():
                n = 1
            else:
                for lvl in range(5):
                    v = bits(_LIT_BITS[lvl])
                    if v != _LIT_TERM[lvl] or lvl == 4:
                        break
                n = v + _LIT_BASE[lvl] + 1
            for _ in range(n):
                out[di] = blob[si]
                si -= 1
                di -= 1
        if di < 0:
            break
        bp = 3                          # match length class
        while bp >= 0 and bit():
            bp -= 1
        cb = _LEN_BITS[bp]
        bx = (bits(cb) if cb >= 0 else 0) + _LEN_BASE[bp]
        if bx == 0:                     # long/short special distance
            dx = bits(5) - 1 if not bit() else bits(8) + 0x3F
        else:
            bp = 1
            while bp >= 0 and bit():
                bp -= 1
            dx = _s16(bits(_OFF_BITS[bp]) + _OFF_BASE[bp])
            if dx < 0:
                dx = _s16(dx - bx)
        src = di + bx + 2 + dx
        for _ in range(bx + 2):
            out[di] = out[src]
            src -= 1
            di -= 1
    return bytes(out)


# ---------------------------------------------------------------- extraction
def extract_all(exe: str = 'UGH.EXE', outdir: str = 'assets', quiet: bool = False) -> dict:
    """Extract all embedded assets from UGH.EXE into outdir."""
    img = load_image(exe)
    for sub in ('screens', 'music', 'sprites'):
        os.makedirs(os.path.join(outdir, sub), exist_ok=True)

    def at(para: int, n: int) -> bytes:
        return img[para * 16:para * 16 + n]

    # palette ---------------------------------------------------------------
    pal6 = at(PAL_PARA, 768)
    with open(os.path.join(outdir, 'game.pal'), 'wb') as f:
        f.write(pal6)
    pal8 = vga6to8(pal6[:384] * 2)   # game copies colours 0-127 into 128-255 (CS:0x641A)

    # full-screen images (64000 px + 768 palette) ---------------------------
    screens_extracted = 0
    for i, para in enumerate(SCREEN_PARAS):
        data = ice_unpack(img[para * 16:])
        px, p6 = data[:64000], data[64000:64768]
        base = os.path.join(outdir, 'screens', 'screen_%d' % i)
        with open(base + '.raw', 'wb') as f:
            f.write(px)
        with open(base + '.pal', 'wb') as f:
            f.write(p6)
        write_png(base + '.png', 320, 200, px, vga6to8(p6))
        screens_extracted += 1
        if not quiet:
            print('  ✓ screen_%d  para %04X  -> %d bytes' % (i, para, len(data)))

    # level / map block ------------------------------------------------------
    lv = ice_unpack(img[LEVEL_PARA * 16:])
    with open(os.path.join(outdir, 'levels.bin'), 'wb') as f:
        f.write(lv)
    if not quiet:
        print('  ✓ levels.bin para %04X -> %d bytes' % (LEVEL_PARA, len(lv)))

    # AdLib ADLX blobs -------------------------------------------------------
    ends = ADLX_PARAS[1:] + [ADLX_END_PARA]
    adlx_count = 0
    for p, e in zip(ADLX_PARAS, ends):
        blob = img[p * 16:e * 16]
        if blob[:4] == b'ADLX':
            with open(os.path.join(outdir, 'music', 'adlx_%04X.adlx' % p), 'wb') as f:
                f.write(blob)
            adlx_count += 1
    if not quiet:
        print('  ✓ %d ADLX music/sound blobs' % adlx_count)

    # sprites ----------------------------------------------------------------
    table = at(SPRITE_TABLE_PARA, SPRITE_TABLE_LEN)
    banks = [at(p, n) for _, p, n in BANKS]
    meta, sheets = [], [[] for _ in BANKS]
    for sid in range(len(table) // 4):
        off, w, h = struct.unpack_from('<HBB', table, sid * 4)
        bank = max(i for i, b in enumerate(BANKS) if sid >= b[0])
        if w == 0 or h == 0 or off + w * h > len(banks[bank]):
            continue
        col = banks[bank][off:off + w * h]          # column-major, h bytes/col
        pix = bytearray(w * h)
        for x in range(w):
            for y in range(h):
                pix[y * w + x] = col[x * h + y]
        write_png(os.path.join(outdir, 'sprites', '%04d.png' % sid),
                  w, h, pix, pal8, True)
        meta.append(dict(id=sid, bank=bank, offset=off, width=w, height=h))
        sheets[bank].append((w, h, pix))
    with open(os.path.join(outdir, 'sprites', 'sprites.json'), 'w') as f:
        json.dump(meta, f, indent=1)

    for b, items in enumerate(sheets):              # contact sheets
        if not items:
            continue
        cw = max(w for w, _, _ in items) + 2
        ch = max(h for _, h, _ in items) + 2
        cols = 16
        rows = (len(items) + cols - 1) // cols
        W, H = cols * cw, rows * ch
        sheet = bytearray(W * H)
        for n, (w, h, pix) in enumerate(items):
            ox, oy = (n % cols) * cw + 1, (n // cols) * ch + 1
            for y in range(h):
                sheet[(oy + y) * W + ox:(oy + y) * W + ox + w] = pix[y * w:(y + 1) * w]
        write_png(os.path.join(outdir, 'sprites', 'bank_%d.png' % b),
                  W, H, sheet, pal8, True)
    if not quiet:
        print('  ✓ %d sprites across 4 banks' % len(meta))

    # high-score file (bytes are stored bitwise-NOT) -------------------------
    hi = _find_ci(os.path.dirname(os.path.abspath(exe)), 'UGH!.HI')
    if hi:
        txt = bytes(255 - b for b in open(hi, 'rb').read())
        recs = txt.split(b'\r')
        with open(os.path.join(outdir, 'highscores.txt'), 'w') as f:
            for r in recs:
                if r.strip(b'\0\xff'):
                    f.write(r.decode('latin1') + '\n')

    return {
        'screens': screens_extracted,
        'levels_bytes': len(lv),
        'adlx_blobs': adlx_count,
        'sprites': len(meta),
    }


def main():
    exe = sys.argv[1] if len(sys.argv) > 1 else 'UGH.EXE'
    outdir = sys.argv[2] if len(sys.argv) > 2 else 'assets'
    print(f"Extracting assets from {exe} to {outdir}/...")
    extract_all(exe, outdir, quiet=False)
    print("Done!")


if __name__ == '__main__':
    main()
