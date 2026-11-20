"""YM3812 (OPL2) emulator (Python + numpy), melodic mode.

Follows the hardware model reverse engineered from the chip die (log-sin and
exponent ROMs, 9-bit attenuation envelope with the global envelope timer,
19-bit phase accumulators), the same model Nuked-OPL3 implements.  Only what
OPL2 music needs is modelled: 9 two-operator channels, waveforms 0-3,
feedback, FM/AM connection, KSL, KSR, tremolo and vibrato.  Rhythm mode, the
timers and CSM are not (UGH! never writes 0xBD or 0x08).

The chip runs at 3.579545 MHz / 72 = 49716 Hz; render() returns that many
signed 16-bit mono samples per second.  Channels are independent apart from
the global LFO/envelope timers, which are pure functions of the sample index,
so each channel is rendered on its own for speed (see _render_channel).
"""
from __future__ import annotations

import ctypes
import glob
import math
import os
from bisect import bisect_left

import numpy as np

CHIP_RATE = 3579545 / 72                            # 49715.9 Hz

LOGSIN = [round(-math.log2(math.sin((i + 0.5) * math.pi / 512)) * 256) for i in range(256)]
EXP = [round(2 ** ((255 - i) / 256) * 1024) for i in range(256)]
MULT = (1, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 20, 24, 24, 30, 30)
KSL_ROM = (0, 32, 40, 45, 48, 51, 53, 55, 56, 58, 59, 60, 61, 62, 63, 64)
KSL_SHIFT = (8, 1, 2, 0)
EG_INCSTEP = ((0, 0, 0, 0), (1, 0, 0, 0), (1, 0, 1, 0), (1, 1, 1, 0))
ATTACK, DECAY, SUSTAIN, RELEASE = range(4)

# register offset (0x20 + n) -> slot number; -1 = unused
SLOT_OF_REG = [0, 1, 2, 3, 4, 5, -1, -1, 6, 7, 8, 9, 10, 11, -1, -1,
               12, 13, 14, 15, 16, 17, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1]
# channel -> (modulator slot, carrier slot)
CH_SLOTS = [(0, 3), (1, 4), (2, 5), (6, 9), (7, 10), (8, 11), (12, 15), (13, 16), (14, 17)]


def _wave_table(wf: int) -> list[int]:
    """Operator output for every 10-bit phase at zero attenuation, as
    (log attenuation, negate) folded into one table of attenuations
    (bit 13 = negative, 0x1000 = silence)."""
    t = []
    for p in range(1024):
        q = p & 0xFF
        ls = LOGSIN[q ^ 0xFF] if p & 0x100 else LOGSIN[q]
        neg = 0
        if wf == 0:
            neg = p & 0x200
        elif wf == 1:
            if p & 0x200:
                ls = 0x1000
        elif wf == 3:
            if p & 0x100:
                ls = 0x1000
        t.append(ls | (0x2000 if neg else 0))
    return t


WAVES = [_wave_table(w) for w in range(4)]


def _make_exp_out():
    """level (0..0x1FFF+) -> magnitude, like the exponent ROM lookup."""
    out = []
    for level in range(0x2000):
        out.append((EXP[level & 0xFF] << 1) >> (level >> 8))
    return out


EXP_OUT = _make_exp_out()


def _load_native():
    """The compiled inner loop (extractor/_opl2c.c, see build_native.py) or
    None.  Same integer arithmetic as the Python loop, so the output is
    identical; PTERODRIVE_NO_NATIVE=1 forces the Python path."""
    if os.environ.get('PTERODRIVE_NO_NATIVE'):
        return None
    here = os.path.dirname(os.path.abspath(__file__))
    for path in sorted(glob.glob(os.path.join(here, '_opl2c.*'))):
        if path.endswith(('.so', '.dylib', '.dll')):
            try:
                lib = ctypes.CDLL(path)
                lib.opl2_abi.restype = ctypes.c_int
                if lib.opl2_abi() != 1:
                    continue
                lib.opl2_run_slot.restype = None
                lib.opl2_run_slot.argtypes = [ctypes.c_void_p] * 2 + [ctypes.c_int64] * 5 \
                    + [ctypes.c_void_p] * 7 + [ctypes.c_int64] + [ctypes.c_void_p] * 4
                return lib
            except (OSError, AttributeError):
                continue
    return None


_NATIVE = _load_native()


class Slot:
    __slots__ = ('am', 'vib', 'egt', 'ksr', 'mult', 'ksl', 'tl', 'ar', 'dr',
                 'sl', 'rr', 'wf', 'key', 'eg_rout', 'eg_gen', 'eg_ksl',
                 'phase', 'out', 'prout')

    def __init__(self):
        self.am = self.vib = self.egt = self.ksr = 0
        self.mult = self.ksl = self.tl = 0
        self.ar = self.dr = self.sl = self.rr = 0
        self.wf = 0
        self.key = 0
        self.eg_rout = 0x1FF
        self.eg_gen = RELEASE
        self.eg_ksl = 0
        self.phase = 0
        self.out = 0
        self.prout = 0


class Channel:
    __slots__ = ('fnum', 'block', 'fb', 'con', 'ksv', 'mod', 'car')

    def __init__(self, mod: Slot, car: Slot):
        self.fnum = self.block = self.fb = self.con = self.ksv = 0
        self.mod, self.car = mod, car


class OPL2:
    def __init__(self):
        self.slots = [Slot() for _ in range(18)]
        self.channels = [Channel(self.slots[m], self.slots[c]) for m, c in CH_SLOTS]
        self.wave_enable = 0
        self.nts = 0
        self.dam = 0
        self.dvb = 0
        # global timers
        self.timer = 0
        self.eg_timer = 0
        self.eg_state = 0
        self.eg_add = 0
        self.eg_lo = 0
        self.trem_pos = 0
        self.tremolo = 0
        self.vib_pos = 0
        self.globals_raw = {}

    # ------------------------------------------------------------ registers
    def write(self, reg: int, val: int):
        reg &= 0xFF
        val &= 0xFF
        hi = reg & 0xF0
        if reg in (0x01, 0x08, 0xBD):
            self.globals_raw[reg] = val
        if reg == 0x01:
            self.wave_enable = val & 0x20
        elif reg == 0x08:
            self.nts = (val >> 6) & 1
            for ch in self.channels:
                self._update_ch(ch)
        elif reg == 0xBD:
            self.dam = val >> 7
            self.dvb = (val >> 6) & 1
        elif 0x20 <= reg <= 0x95 or 0xE0 <= reg <= 0xF5:
            n = SLOT_OF_REG[reg & 0x1F] if (reg & 0x1F) < 0x20 else -1
            if n < 0:
                return
            s = self.slots[n]
            grp = reg & 0xE0
            if grp == 0x20:
                s.am, s.vib, s.egt, s.ksr = val >> 7, (val >> 6) & 1, (val >> 5) & 1, (val >> 4) & 1
                s.mult = MULT[val & 0x0F]
            elif grp == 0x40:
                s.ksl, s.tl = val >> 6, val & 0x3F
            elif grp == 0x60:
                s.ar, s.dr = val >> 4, val & 0x0F
            elif grp == 0x80:
                sl = val >> 4
                s.sl, s.rr = (0x1F if sl == 0x0F else sl), val & 0x0F
            elif grp == 0xE0:
                s.wf = val & 3
        elif hi in (0xA0, 0xB0, 0xC0) and (reg & 0x0F) < 9:
            ch = self.channels[reg & 0x0F]
            if hi == 0xA0:
                ch.fnum = (ch.fnum & 0x300) | val
            elif hi == 0xB0:
                ch.fnum = (ch.fnum & 0xFF) | ((val & 3) << 8)
                ch.block = (val >> 2) & 7
                key = (val >> 5) & 1
                ch.mod.key = ch.car.key = key
            else:
                ch.fb, ch.con = (val >> 1) & 7, val & 1
            self._update_ch(ch)

    def _update_ch(self, ch: Channel):
        ch.ksv = (ch.block << 1) | ((ch.fnum >> (9 - self.nts)) & 1)
        ksl = (KSL_ROM[ch.fnum >> 6] << 2) - ((8 - ch.block) << 5)
        ch.mod.eg_ksl = ch.car.eg_ksl = max(ksl, 0)

    # ------------------------------------------------------------- render
    def _globals(self, n: int):
        """Per-sample global state seen by sample i: eg_add, eg_state, eg_lo,
        tremolo, vibrato position.  All of it is a function of the sample
        index, so it is computed in closed form instead of stepped."""
        i = np.arange(n, dtype=np.int64)
        timer = self.timer + i                       # timer at the start of sample i
        tshift = 2 if self.dam else 4
        # tremolo / vibrato: the value seen by sample i is the one left by i - 1
        pos = (self.trem_pos + np.cumsum((timer & 0x3F) == 0x3F)) % 210
        trem = np.where(pos < 105, pos, 210 - pos) >> tshift
        g_trem = np.concatenate(([self.tremolo], trem[:-1]))
        vpos = (self.vib_pos + np.cumsum((timer & 0x3FF) == 0x3FF)) & 7
        g_vib = np.concatenate(([self.vib_pos], vpos[:-1]))
        # envelope timer: advances on every other sample (those starting with eg_state 1)
        g_st = (self.eg_state ^ i) & 1
        done = np.cumsum(g_st)                       # active samples up to and including i
        k = int(done[-1])
        et = (self.eg_timer + np.arange(k, dtype=np.int64)) & 0xFFFFFFFFF
        low = et & -et
        shift = np.log2(np.maximum(low, 1)).astype(np.int64)
        add = np.where((et == 0) | (shift > 12), 0, shift + 1)
        lo = et & 3
        before = done - g_st                         # active samples strictly before i
        add_all = np.concatenate(([self.eg_add], add))
        lo_all = np.concatenate(([self.eg_lo], lo))
        g_add, g_lo = add_all[before], lo_all[before]
        self.trem_pos, self.tremolo = int(pos[-1]), int(trem[-1])
        self.vib_pos = int(vpos[-1])
        self.timer = (self.timer + n) & 0xFFFFFFFF
        self.eg_timer = (self.eg_timer + k) & 0xFFFFFFFFF
        self.eg_state = (self.eg_state ^ n) & 1
        self.eg_add, self.eg_lo = int(add_all[k]), int(lo_all[k])
        return tuple(np.ascontiguousarray(x, dtype=np.int64) for x in (g_add, g_st, g_lo, g_trem, g_vib))

    def render(self, n: int) -> list[int]:
        """n chip samples -> list of signed ints (sum of 9 channels, unclipped)."""
        return self.render_block(n, ())

    def render_block(self, n: int, events) -> list[int]:
        """Render n samples applying register writes at given sample offsets.

        events = (offset, reg, val) in ascending offset order, 0 <= offset <= n;
        a write at offset k affects samples k and later.  Rendering a long
        block at once is much faster than many render() calls, because each
        channel is only split where its own registers change.
        """
        out = []
        base = 0
        part = []
        for off, reg, val in events:
            if reg in (0x01, 0x08, 0xBD):
                if self.globals_raw.get(reg) != val:
                    out.append(self._render_part(off - base, part))
                    base, part = off, []
                self.write(reg, val)
            else:
                part.append((off - base, reg, val))
        out.append(self._render_part(n - base, part))
        return np.concatenate(out).tolist()

    def _render_part(self, n: int, events) -> list[int]:
        if n <= 0:
            for _, reg, val in events:
                self.write(reg, val)
            return np.zeros(0, np.int64)
        g_np = self._globals(n)
        steps = None
        if _NATIVE is None:
            g = tuple(x.tolist() for x in g_np)
            by_add = [[] for _ in range(16)]       # eg_add -> samples where the EG may step
            for j, (add, st) in enumerate(zip(g[0], g[1])):
                if st:
                    by_add[add].append(j)
            cache = {}

            def steps(hi, lo):
                """Samples at which a rate below 12 adds 1 to the attenuation."""
                r = cache.get((hi, lo))
                if r is None:
                    r = list(by_add[12 - hi])
                    if lo & 2:
                        r += by_add[13 - hi]
                    if lo & 1:
                        r += by_add[14 - hi]
                    r.sort()
                    cache[hi, lo] = r
                return r
        else:
            g = g_np
        per_ch = [[] for _ in range(9)]
        for ev in events:
            c = channel_of(ev[1])
            if c < 0:
                self.write(ev[1], ev[2])
            else:
                per_ch[c].append(ev)
        mix = np.zeros(n, np.int64)
        ctx = g + (self.wave_enable, 0 if self.dvb else 1, steps, g_np)
        for ch, evs in zip(self.channels, per_ch):
            pos = 0
            for off, reg, val in evs:
                if off > pos:
                    _run_channel(ch, ctx, pos, off, mix)
                    pos = off
                self.write(reg, val)
            if pos < n:
                _run_channel(ch, ctx, pos, n, mix)
        return mix


def channel_of(reg: int) -> int:
    """Channel a register belongs to, -1 for chip-global registers."""
    if 0xA0 <= reg <= 0xC8:
        c = reg & 0x0F
        return c if c < 9 else -1
    if 0x20 <= reg <= 0x95 or 0xE0 <= reg <= 0xF5:
        n = SLOT_OF_REG[reg & 0x1F]
        return -1 if n < 0 else SLOT_CHANNEL[n]
    return -1


SLOT_CHANNEL = [0] * 18
for _c, (_m, _k) in enumerate(CH_SLOTS):
    SLOT_CHANNEL[_m] = SLOT_CHANNEL[_k] = _c


def _run_channel(ch: Channel, ctx, a: int, b: int, mix: list):
    m, c = ch.mod, ch.car
    if _silent(m) and _silent(c):
        # nothing audible or stateful until the next key-on (a key-on from
        # release resets the phase), so the samples can be skipped
        m.out = m.prout = 0
        return
    m_out = _run_slot(m, ch, ctx, a, b, None, 9 - ch.fb if ch.fb else 0)
    c_out = _run_slot(c, ch, ctx, a, b, None if ch.con else m_out, 0)
    if ch.con:
        mix[a:b] += m_out
        mix[a:b] += c_out
    else:
        mix[a:b] += c_out


def _rate(s: Slot, ksv: int, reg_rate: int):
    ks = ksv >> ((s.ksr ^ 1) << 1)
    rate = ks + (reg_rate << 2)
    hi = rate >> 2
    if hi & 0x10:
        hi = 0x0F
    return hi, rate & 3, reg_rate != 0


def _silent(s: Slot) -> bool:
    return not s.key and s.eg_gen == RELEASE and (s.eg_rout & 0x1F8) == 0x1F8


def _level_table(wf: int, eg: int) -> tuple[list[int], np.ndarray]:
    """Signed operator output for every phase at a fixed attenuation, as a
    list (scalar loops) and an int64 array (vector loops)."""
    key = (wf << 9) | eg
    t = _LEVEL_TABLES.get(key)
    if t is None:
        shift = eg << 3
        t = []
        for w in WAVES[wf]:
            v = EXP_OUT[(w & 0x1FFF) + shift]
            t.append(~v if w & 0x2000 else v)
        _LEVEL_TABLES[key] = t
        _LEVEL_NP[key] = np.array(t, np.int64)
    return t, _LEVEL_NP[key]


_LEVEL_TABLES: dict[int, list[int]] = {}
_LEVEL_NP: dict[int, np.ndarray] = {}
# eg_shift (rate_hi + eg_add) -> envelope step for rates below 12, per rate_lo
_LOW_RATE_SHIFT = [[1 if e == 12 else (lo >> 1) & 1 if e == 13 else lo & 1 if e == 14 else 0
                    for e in range(32)] for lo in range(4)]
STATIC_CHECK = 32                # samples between checks for a settled envelope
_NP_WAVES = [np.array(w, np.int32) for w in WAVES]
_NP_EXP_OUT = np.array(EXP_OUT, np.int32)
_NP_LOW_RATE = np.array(_LOW_RATE_SHIFT, np.int32)
_NP_INCSTEP = np.array(EG_INCSTEP, np.int32)


def _osc(tab, phase, out, prout, inc_of, mod, fb_shift, res, k0, k1):
    """Samples k0..k1-1 of one operator at a constant attenuation (tab =
    (list, array) from _level_table).  Without feedback the phase is a
    running sum, so the whole span is one vector lookup; feedback depends on
    the previous output and stays a scalar loop."""
    t_list, t_np = tab
    if k1 <= k0:
        return phase, out, prout
    inc = inc_of[k0:k1]
    ph = np.empty(k1 - k0, np.int64)
    ph[0] = phase
    np.cumsum(inc[:-1], out=ph[1:])
    ph[1:] += phase
    idx = (ph & 0x7FFFF) >> 9
    end = (phase + int(inc.sum())) & 0x7FFFF
    if fb_shift:
        r = []
        add = r.append
        for b in idx.tolist():
            m = (prout + out) >> fb_shift
            prout = out
            out = t_list[(b + m) & 0x3FF]
            add(out)
        res[k0:k1] = r
        return end, out, prout
    last = out
    if mod is not None:
        idx += mod[k0:k1]
    res[k0:k1] = t_np[idx & 0x3FF]
    return end, int(res[k1 - 1]), (int(res[k1 - 2]) if k1 - k0 > 1 else last)


def _run_slot_native(s, g_np, a, n, mod, fb_shift, wf, base, am, rates, a_hi, inc_of):
    """_run_slot's envelope + oscillator loop, run by the C core."""
    st = np.array([s.key, s.eg_gen, s.eg_rout, s.phase, s.out, s.prout], np.int64)
    rt = np.array([(hi, lo, int(nz)) for hi, lo, nz in rates], np.int64)
    res = np.empty(n, np.int64)
    off = a * 8
    _NATIVE.opl2_run_slot(
        st.ctypes.data, rt.ctypes.data, base, am, s.sl, a_hi, fb_shift,
        g_np[0].ctypes.data + off, g_np[1].ctypes.data + off,
        g_np[2].ctypes.data + off, g_np[3].ctypes.data + off,
        inc_of.ctypes.data, mod.ctypes.data if mod is not None else None,
        res.ctypes.data, n, _NP_WAVES[wf].ctypes.data, _NP_EXP_OUT.ctypes.data,
        _NP_LOW_RATE.ctypes.data, _NP_INCSTEP.ctypes.data)
    s.eg_gen, s.eg_rout, s.phase, s.out, s.prout = (int(x) for x in st[1:])
    return res


def _run_slot(s: Slot, ch: Channel, ctx, a: int, b: int, mod, fb_shift: int) -> list[int]:
    """Advance one operator over samples a..b-1 of the current part.

    mod = per-sample phase modulation (carrier in FM mode) or None;
    fb_shift != 0 = self feedback (modulator).  The key does not change
    inside a segment, so once the envelope settles (sustain with EGT, fully
    released, or a zero rate) the rest runs a plain phase + table loop.
    """
    g_add, g_st, g_lo, g_trem, g_vib, waves_on, dvb_shift, steps, g_np = ctx
    fnum, block, ksv = ch.fnum, ch.block, ch.ksv
    mult = s.mult
    wf = s.wf if waves_on else 0
    wave = WAVES[wf]
    exp_out = EXP_OUT
    base = (s.tl << 2) + (s.eg_ksl >> KSL_SHIFT[s.ksl])
    n = b - a
    if s.vib:
        vr = (fnum >> 7) & 7
        incs = []
        for vpos in range(8):
            r = 0 if not vpos & 3 else (vr >> 1 if vpos & 1 else vr)
            r >>= dvb_shift
            if vpos & 4:
                r = -r
            incs.append(((((fnum + r) << block) >> 1) * mult) >> 1)
        inc_of = np.array(incs, np.int64)[g_np[4][a:b]]
    else:
        inc_of = np.full(n, (((fnum << block) >> 1) * mult) >> 1, np.int64)
    am = s.am
    rates = (_rate(s, ksv, s.ar), _rate(s, ksv, s.dr),
             (0, 0, False) if s.egt else _rate(s, ksv, s.rr), _rate(s, ksv, s.rr))
    a_hi = rates[ATTACK][0]
    if _NATIVE is not None:
        return _run_slot_native(s, ctx[-1], a, n, mod, fb_shift, wf, base, am, rates, a_hi, inc_of)

    key, gen, rout, phase, sl = s.key, s.eg_gen, s.eg_rout, s.phase, s.sl
    out, prout = s.out, s.prout
    hi, lo, nz = rates[gen]
    low = _LOW_RATE_SHIFT[lo]
    step = EG_INCSTEP[lo]
    res = np.zeros(n, np.int64)
    inc_list = inc_of.tolist()
    mod_list = mod.tolist() if mod is not None else None
    i = 0
    while i < n:
        if not am and (key or gen == RELEASE) and gen != ATTACK and not (key and gen == RELEASE):
            if not nz or rout == 0x1FF:
                if gen != DECAY:
                    # settled: constant attenuation for the rest of the segment
                    eg = rout + base
                    phase, out, prout = _osc(_level_table(wf, eg if eg < 0x1FF else 0x1FF),
                                             phase, out, prout, inc_of, mod, fb_shift, res, i, n)
                    break
            elif hi < 12 and (rout & 0x1F8) != 0x1F8 and not (gen == DECAY and (rout >> 4) == sl):
                # slow rate: the level only moves by 1 at sparse samples, so
                # run at constant attenuation up to and including the next step
                evs = steps(hi, lo)
                p = bisect_left(evs, a + i)
                e = evs[p] - a if p < len(evs) else n
                k1 = e + 1 if e < n else n
                eg = rout + base
                phase, out, prout = _osc(_level_table(wf, eg if eg < 0x1FF else 0x1FF),
                                         phase, out, prout, inc_of, mod, fb_shift, res, i, k1)
                if e < n:
                    rout += 1
                i = k1
                continue
        stop = min(n, i + STATIC_CHECK)
        for k in range(i, stop):
            j = a + k
            # ---- envelope: eg_out from the current level, then one EG step
            eg = rout + base
            if am:
                eg += g_trem[j]
            if eg > 0x1FF:
                eg = 0x1FF
            reset = False
            if key and gen == RELEASE:              # key-on: restart attack
                reset = True
                if a_hi == 0x0F:
                    rout = 0
                gen = ATTACK
            else:
                if not nz:
                    shift = 0
                elif hi < 12:
                    shift = low[hi + g_add[j]] if g_st[j] else 0
                else:
                    shift = (hi & 3) + step[g_lo[j]]
                    if shift & 4:
                        shift = 3
                    elif not shift:
                        shift = g_st[j]
                if gen == ATTACK:
                    if not rout:
                        gen = DECAY
                    elif key and shift and a_hi != 0x0F:
                        rout = (rout + ((~rout) >> (4 - shift))) & 0x1FF
                else:
                    off = (rout & 0x1F8) == 0x1F8
                    if gen == DECAY and (rout >> 4) == sl:
                        gen = SUSTAIN
                        if off:
                            rout = 0x1FF
                    elif off:
                        rout = 0x1FF
                    elif shift:
                        rout = (rout + (1 << (shift - 1))) & 0x1FF
            if not key and gen != RELEASE:
                gen = RELEASE
            hi, lo, nz = rates[gen]
            low = _LOW_RATE_SHIFT[lo]
            step = EG_INCSTEP[lo]
            # ---- phase (output uses the phase before this step's increment)
            ph = phase >> 9
            if reset:
                phase = 0
            phase = (phase + inc_list[k]) & 0x7FFFF
            # ---- operator output
            if fb_shift:
                m = (prout + out) >> fb_shift
            elif mod is not None:
                m = mod_list[k]
            else:
                m = 0
            prout = out
            w = wave[(ph + m) & 0x3FF]
            out = exp_out[(w & 0x1FFF) + (eg << 3)]
            if w & 0x2000:
                out = ~out
            res[k] = out
        i = stop
    s.eg_gen, s.eg_rout, s.phase, s.out, s.prout = gen, rout, phase, out, prout
    return res
