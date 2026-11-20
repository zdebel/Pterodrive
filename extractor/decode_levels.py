#!/usr/bin/env python3
"""Decode the level data of UGH.EXE (Bones Park / PlayByte, 1992).

Usage: python3 decode_levels.py [UGH.EXE] [out.json]

Where the data lives (all reverse engineered from the 16-bit code):

  * levels.bin  = ICE!-unpacked blob at paragraph 0x1A38, 32000 bytes
                = 100 *screens* of 320 bytes.  A screen is a 20 x 16 grid of
                tile ids, row-major.  A tile id is a sprite id; tiles are the
                16x12 pixel sprites (20*16 = 320 px, 16*12 = 192 px).
                Screens 0 and 90 (offset 0x7080) are also used by the title /
                credits / intro screens.
  * DGROUP (segment paragraph 0x5C09) holds everything else:
        DS:0x2909   word[2]   pointer to the level list of stage 1 / stage 2
        level list  word[]    pointers to level descriptors, 0xFFFF ends
                              (stage 1: 69 entries, stage 2: 81 entries)
        level descriptor (26 bytes, copied to DS:0x28E9 by the loader at
        CS:0x5726):
          +0  u16 byte offset of the tile screen in levels.bin (/320 = index)
          +2  u16 ptr  "pads"      list, 16-byte records, 0xFFFF ends
          +4  u16 ptr  "people"    list, 6-byte records,  0xFFFF ends
          +6  u16 ptr  "hazards"   list, variable records, 0xFFFF ends
          +8  u16 number of people that must complete their trip
          +10 u16 ptr  intro text (0xFE = centre line, 0x0D/0xFD = newline,
                                   0x00 = end; chars are sprite 0x237+code)
          +12 u8  weather/particle mode (0 = none)
          +13 u16 start x1 (fixed point <<5, +0x200 bias)
          +15 u16 start y1 (as stored, scaled by 3/4 then -0x140 bias)
          +17 u16 start x2    +19 u16 start y2   (second start position)
          +21 u16 start water line (f75() 3/4 scale, clamped to 0x17E0, <<5)
          +23 s16 water rate added to the level every 2nd tick (1/32 px; <0 = rising)
          +25 u8  purpose unknown (values 4..6)

Anything marked 'raw' / 'unknown' is stored verbatim instead of guessed.

Game mode / level selection (menu key toggles DS:0x2634 ^= 3, CS:0x1EC5):
    DS:0x2634  game mode, 1 or 2           (2 = two-player game)
    DS:0x2636  mode * 2                    (word-table index, #players * 2)
    DS:0x261c  current level number, 0-based
    DS:0x2624  word[2]  levels per mode: 69, 81
    DS:0x00EA  word[2]  per-mode pointer to the NUL-separated level names
There is NO multiply-by-320 at run time: the descriptor stores the final byte
offset (screen * 320).  The two level lists are separate tables, not one list
split at 69: the mode-2 list repeats the 69 mode-1 descriptors and interleaves
12 extra ones.  The 320-byte block copied to DS:0x264C is *only* a tile
screen (no header, no RLE, no pointers); every other per-level value is in the
26-byte descriptor in DGROUP, never inside levels.bin.

C layout of the 26-byte descriptor (packed, little endian):

    struct level_desc {            /* copied to DS:0x28E9 */
        uint16_t tile_offset;      /* +0  byte offset in levels.bin, = screen*320 */
        uint16_t pads;             /* +2  DS ptr, struct pad[]  (0xFFFF ends)     */
        uint16_t people;           /* +4  DS ptr, struct person[] (0xFFFF ends)   */
        uint16_t hazards;          /* +6  DS ptr, variable records (0xFFFF ends)  */
        uint16_t required_people;  /* +8  decremented per finished passenger      */
        uint16_t text;             /* +10 DS ptr, intro text                      */
        uint8_t  weather;          /* +12 particle/wind mode, 0 = none            */
        uint16_t x1, y1, x2, y2;   /* +13 start positions, player 1 / player 2    */
        uint16_t water;            /* +21 start water line (3/4 scaled, <<5)      */
        int16_t  water_rate;       /* +23 per 2 ticks; negative = rising          */
        uint8_t  unknown;          /* +25                                         */
    } __attribute__((packed));     /* 26 bytes */
    struct pad    { uint16_t w0, w1, y, stand_x, w4, x, id, w7; };   /* 16 bytes */
    struct person { uint16_t type, start_pad, trip; };               /*  6 bytes */
    /* trip: uint16_t pad0, delay0, pad1, delay1, ..., padN, delayN, 0xFFFF */
"""
import json
import os
import struct
import sys

try:
    from .extract_assets import ice_unpack, load_image
except ImportError:
    from extract_assets import ice_unpack, load_image

TILE_W, TILE_H = 16, 12
GRID_W, GRID_H = 20, 16
SCREEN_BYTES = GRID_W * GRID_H
LEVEL_BLOB_PARA = 0x1A38
DGROUP_PARA = 0x5C09
DGROUP_SIZE = 0x7E2E
STAGE_TABLE = 0x2909       # per-mode level lists (mode 1, mode 2)
COUNT_TABLE = 0x2624       # levels per mode: 69, 81
NAME_TABLE = 0x00EA        # per-mode pointer to NUL separated level names
MODES = 2
DESC_SIZE = 26
FONT_BASE = 0x237


def f75(v):
    """CS:0x5AFD  ax - (ax >> 2): the 3/4 vertical scaling used for y values."""
    v = struct.unpack('<h', struct.pack('<H', v & 0xFFFF))[0]
    return v - (v >> 2)


def s16(v):
    return v - 0x10000 if v & 0x8000 else v


class Game:
    def __init__(self, exe, levels_bin=None):
        img = load_image(exe)
        self.ds = img[DGROUP_PARA * 16:DGROUP_PARA * 16 + DGROUP_SIZE]
        if levels_bin:                       # e.g. from extract_assets.py
            self.blob = open(levels_bin, 'rb').read()
        else:
            self.blob = ice_unpack(img[LEVEL_BLOB_PARA * 16:])
        assert len(self.blob) == 32000

    def w(self, o):
        return struct.unpack_from('<H', self.ds, o)[0]

    def sw(self, o):
        return struct.unpack_from('<h', self.ds, o)[0]

    def cstring(self, o):
        end = self.ds.index(b'\0', o)
        return self.ds[o:end]

    # ---- text ---------------------------------------------------------
    def decode_text(self, o):
        """Return the list of lines of a 0xFE/0x0D/0xFD text block."""
        lines, cur, centred = [], [], False
        while True:
            b = self.ds[o]
            o += 1
            if b == 0xFE:
                centred = True
            elif b in (0x0D, 0xFD):
                lines.append(''.join(cur))
                cur = []
            elif b == 0:
                if cur:
                    lines.append(''.join(cur))
                break
            else:
                cur.append(bytes([b]).decode('cp437'))
        return dict(centred=centred, lines=lines)

    # ---- lists --------------------------------------------------------
    def pads(self, o):
        out = []
        while self.w(o) != 0xFFFF:
            r = [self.w(o + 2 * i) for i in range(8)]
            out.append(dict(
                id=r[6],                        # 1-based number in the file
                x=s16(r[5]),                    # DS:0x2971 array
                y=f75(r[2]),                    # DS:0x2935 array (after 3/4)
                stand_x=s16(r[3]),              # DS:0x2949 array: passenger x
                raw=dict(w0=r[0], w1=r[1], w4=s16(r[4]), w2=r[2], w7=r[7])))
            o += 16
        return out

    def trip(self, o):
        """Chain  pad, delay, pad, delay, ..., pad, delay, 0xFFFF ."""
        steps, w = [], []
        while True:
            v = self.w(o)
            w.append(v)
            o += 2
            if v == 0xFFFF:
                break
        # words: p0 d0 p1 d1 ... pn dn FFFF
        for i in range(0, len(w) - 1, 2):
            steps.append(dict(pad=w[i], delay=w[i + 1] if i + 1 < len(w) - 1 else None))
        return dict(steps=steps, raw=w)

    def people(self, o):
        out = []
        while self.w(o) != 0xFFFF:
            typ, pad, param = self.w(o), self.w(o + 2), self.w(o + 4)
            e = dict(type='0x%04X' % typ, pad=pad, param='0x%04X' % param)
            if typ != 0x78DC and param:            # 0x78DC = static, no chain
                e['trip'] = self.trip(param)
            out.append(e)
            o += 6
        return out

    def hazards(self, o):
        out = []
        while self.w(o) != 0xFFFF:
            typ = self.w(o)
            if typ == 0x76E4:       # pad, ptr  (loader CS:0x58E9)
                e = dict(type='0x76E4', pad=self.w(o + 2), param='0x%04X' % self.w(o + 4))
                n = 6
                p = self.w(o + 4)
                pool = []
                while p and self.w(p) != 0xFFFF and len(pool) < 32:
                    pool.append('0x%04X' % self.w(p))
                    p += 2
                e['pointer_list'] = pool
            elif typ == 0x7630:     # (CS:0x591C)  x,y given directly
                e = dict(type='0x7630', raw=[self.w(o + 2 * i) for i in range(1, 4)])
                n = 8
            elif typ == 0x76A8:     # (CS:0x5942)  x<<5, y given directly
                e = dict(type='0x76A8', x=self.w(o + 2),
                         y=f75(self.w(o + 4)) - 22,
                         raw=[self.w(o + 2 * i) for i in range(1, 5)])
                n = 10
            else:                   # generic: pad, skipped word, param
                e = dict(type='0x%04X' % typ, pad=self.w(o + 2),
                         raw=[self.w(o + 2 * i) for i in range(1, 4)])
                n = 8
            out.append(e)
            o += n
        return out

    # ---- levels -------------------------------------------------------
    def level(self, ptr):
        d = self.ds[ptr:ptr + DESC_SIZE]
        u = lambda off: struct.unpack_from('<H', d, off)[0]
        off = u(0)
        scr = off // SCREEN_BYTES
        tiles = [list(self.blob[off + r * GRID_W: off + (r + 1) * GRID_W])
                 for r in range(GRID_H)]
        return dict(
            descriptor='0x%04X' % ptr,
            screen=scr,
            blob_offset=off,
            tiles=tiles,
            required_people=u(8),
            weather=d[12],
            start1=dict(x=(u(13) - 0x200) / 32.0, y=(f75(u(15)) - 0x140) / 32.0),
            start2=dict(x=(u(17) - 0x200) / 32.0, y=(f75(u(19)) - 0x140) / 32.0),
            # exact loader values (CS:0x5726): DS:27D0/27D4 and DS:27D2/27D6
            start1_fixed=[u(13) - 0x200, f75(u(15)) - 0x140],
            start2_fixed=[u(17) - 0x200, f75(u(19)) - 0x140],
            # +21: f75() scaling then clamp 0x17E0 (CS:0x57FA); >>5 = start water line px
            floor_y=min(f75(u(21)), 0x17E0) >> 5,
            water_level_fixed=min(f75(u(21)), 0x17E0),
            water_rate=struct.unpack_from('<h', d, 23)[0],   # DS:0x2900 per 2 ticks
            unknown_tail=[d[25]],
            pads=self.pads(u(2)),
            people=self.people(u(4)),
            hazards=self.hazards(u(6)),
            text=self.decode_text(u(10)),
        )

    def names(self, mode):
        p, out = self.w(NAME_TABLE + 2 * (mode - 1)), []
        for _ in range(self.w(COUNT_TABLE + 2 * (mode - 1))):
            e = self.ds.index(b'\0', p)
            out.append(self.ds[p:e].decode('cp437').strip('\xa0\xff'))
            p = e + 1
        return out

    def level_list(self, mode):
        p, out = self.w(STAGE_TABLE + 2 * (mode - 1)), []
        while self.w(p) != 0xFFFF:
            out.append(self.w(p))
            p += 2
        assert len(out) == self.w(COUNT_TABLE + 2 * (mode - 1))
        return out

    def decode_all(self):
        """{mode: [level dict, ...]} ; mode 1 = 1 player (69), 2 = 2 players (81)."""
        lists = {m: self.level_list(m) for m in (1, 2)}
        in_mode1 = set(lists[1])
        out = {}
        for m in (1, 2):
            names = self.names(m)
            out[m] = []
            for n, ptr in enumerate(lists[m]):
                lv = self.level(ptr)
                lv.update(mode=m, number=n + 1, name=names[n],
                          only_in_mode2=(m == 2 and ptr not in in_mode1))
                lv.pop('descriptor')
                lv['descriptor'] = '0x%04X' % ptr
                out[m].append(lv)
        return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    lb = [a.split('=', 1)[1] for a in sys.argv[1:] if a.startswith('--levels-bin=')]
    exe = args[0] if args else 'UGH.EXE'
    outdir = args[1] if len(args) > 1 else 'levels'
    data = Game(exe, lb[0] if lb else None).decode_all()
    meta = dict(tile_w=TILE_W, tile_h=TILE_H, grid=[GRID_W, GRID_H],
                note='tile id = sprite id (extract_assets.py)')
    for m, levels in data.items():
        d = os.path.join(outdir, 'mode%d_%dplayer' % (m, m))
        os.makedirs(d, exist_ok=True)
        for lv in levels:
            lv = dict(meta, **lv)
            with open(os.path.join(d, 'level_%02d.json' % lv['number']), 'w') as f:
                json.dump(lv, f, indent=1)
        print('mode %d: %d levels -> %s' % (m, len(levels), d))
    with open(os.path.join(outdir, 'index.json'), 'w') as f:
        json.dump({str(m): [dict(number=l['number'], name=l['name'],
                                 screen=l['screen'], only_in_mode2=l['only_in_mode2'])
                            for l in ls] for m, ls in data.items()}, f, indent=1)


if __name__ == '__main__':
    main()
