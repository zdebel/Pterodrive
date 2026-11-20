/* Native inner loop of the OPL2 emulator (extractor/opl2.py).
 *
 * One call advances one operator (slot) over n samples: envelope generator,
 * phase accumulator and the log-sin / exponent table lookups.  It is a literal
 * port of the generic per-sample loop in opl2._run_slot, integer-exact, so the
 * rendered audio is bit-identical to the pure-Python/numpy path.
 *
 * Plain C with no Python headers: it is loaded through ctypes, so one binary
 * per OS/CPU works for every Python version.  Build: python -m extractor.build_native
 */
#include <stdint.h>

#if defined(_WIN32)
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif

typedef int64_t i64;

enum { ATTACK, DECAY, SUSTAIN, RELEASE };

#define ABI_VERSION 1

EXPORT int opl2_abi(void) { return ABI_VERSION; }

/* st    key, gen, rout, phase, out, prout (in / out)
 * rates [4][3] = (hi, lo, nonzero) per envelope generator state
 * g_*   per-sample global state, already offset to the first sample
 * inc   phase increment per sample; mod phase modulation or NULL
 * res   output, n samples
 * wave  1024 entries; exp_out 0x2000; low_rate [4][32]; incstep [4][4] */
EXPORT void opl2_run_slot(i64 *st, const i64 *rates, i64 base, i64 am, i64 sl,
                          i64 a_hi, i64 fb_shift,
                          const i64 *g_add, const i64 *g_st, const i64 *g_lo,
                          const i64 *g_trem, const i64 *inc, const i64 *mod,
                          i64 *res, i64 n, const int32_t *wave,
                          const int32_t *exp_out, const int32_t *low_rate,
                          const int32_t *incstep)
{
    i64 key = st[0], gen = st[1], rout = st[2], phase = st[3];
    i64 out = st[4], prout = st[5];
    i64 hi = rates[gen * 3], lo = rates[gen * 3 + 1], nz = rates[gen * 3 + 2];

    for (i64 k = 0; k < n; k++) {
        /* envelope: eg_out from the current level, then one EG step */
        i64 eg = rout + base;
        if (am) eg += g_trem[k];
        if (eg > 0x1FF) eg = 0x1FF;
        int reset = 0;
        if (key && gen == RELEASE) {            /* key-on: restart attack */
            reset = 1;
            if (a_hi == 0x0F) rout = 0;
            gen = ATTACK;
        } else {
            i64 shift;
            if (!nz) {
                shift = 0;
            } else if (hi < 12) {
                shift = g_st[k] ? low_rate[lo * 32 + hi + g_add[k]] : 0;
            } else {
                shift = (hi & 3) + incstep[lo * 4 + g_lo[k]];
                if (shift & 4) shift = 3;
                else if (!shift) shift = g_st[k];
            }
            if (gen == ATTACK) {
                if (!rout) gen = DECAY;
                else if (key && shift && a_hi != 0x0F)
                    rout = (rout + ((~rout) >> (4 - shift))) & 0x1FF;
            } else {
                int off = (rout & 0x1F8) == 0x1F8;
                if (gen == DECAY && (rout >> 4) == sl) {
                    gen = SUSTAIN;
                    if (off) rout = 0x1FF;
                } else if (off) {
                    rout = 0x1FF;
                } else if (shift) {
                    rout = (rout + ((i64)1 << (shift - 1))) & 0x1FF;
                }
            }
        }
        if (!key && gen != RELEASE) gen = RELEASE;
        hi = rates[gen * 3]; lo = rates[gen * 3 + 1]; nz = rates[gen * 3 + 2];

        /* phase: output uses the phase before this step's increment */
        i64 ph = phase >> 9;
        if (reset) phase = 0;
        phase = (phase + inc[k]) & 0x7FFFF;

        /* operator output */
        i64 m;
        if (fb_shift) m = (prout + out) >> fb_shift;
        else if (mod) m = mod[k];
        else m = 0;
        prout = out;
        int32_t w = wave[(ph + m) & 0x3FF];
        out = exp_out[(w & 0x1FFF) + (eg << 3)];
        if (w & 0x2000) out = ~out;
        res[k] = out;
    }
    st[0] = key; st[1] = gen; st[2] = rout; st[3] = phase;
    st[4] = out; st[5] = prout;
}
