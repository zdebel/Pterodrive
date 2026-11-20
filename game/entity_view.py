"""Snapshots of Python entities in the same terms as the DOS arrays
(used by scripts/emu_entities.py)."""
from game.passenger import DEAD_SLOTS, Passenger, STATE_SLOTS

_P_SLOT = {name: off for off, name in STATE_SLOTS.items()}
_D_SLOT = {name: off for off, name in DEAD_SLOTS.items()}
_D_SLOT['DEAD_IDLE'] = 0x22      # word 0x1C0E: first matching slot in a dead struct
_S_SLOT = {'INIT': 0x10, 'IDLE': 0x12, 'CARRIED': 0x22, 'FLYING': 0x26, 'DEAD': 0x08}   # DEAD = word 0x1C0E, first match is +8
_PT = {'INIT': 8, 'WAIT': 0xA, 'WARN2': 0xE, 'FLY': 0x12, 'FALL': 0x1E}
_DN = {'WALK_INIT': 0x8, 'WALK': 0xA, 'ROAR': 0xE, 'CHARGE': 0x12, 'RECOVER': 0x16, 'STUN': 0x1E}
_BO = {'INIT': 0x10, 'CYCLE': 0x12, 'DEAD': 0x1E}
_TR = {'INIT': 0x8, 'ANIM': 0xA, 'WAIT': 0x1E, 'INERT': 0x20}


def py_b(o, m=None):
    if isinstance(o, Passenger):
        tab = _D_SLOT if o.type.is_dead else _P_SLOT
        return dict(type=o.type.ptr, slot=tab.get(o.state, -2), x=o.x, y=o.y,
                    px=o.px, py=o.py, sprite=o.sprite, idx=o.anim_i, delay=o.anim_t,
                    timer=o.timer, bubble=o.bubble, aux=o.aux, bonus=o.bonus,
                    src=o.src, dest=o.dest, ti=o.ti, vy=o.vy)
    return dict(type=0x78DC, slot=_S_SLOT.get(o.state, -2), x=o.x, y=o.y,
                sprite=o.sprite, vy=o.vy)


def py_h(h, m=None):
    n = type(h).__name__
    tab, ptr = {'Pterodactyl': (_PT, 0x7630), 'Dinosaur': (_DN, 0x766C),
                'Boulder': (_BO, 0x76A8), 'Tree': (_TR, 0x76E4)}[n]
    d = dict(type=ptr, slot=tab.get(h.state, -2), x=h.x, y=h.y,
             sprite=h.sprite if h.sprite is not None else -1)
    if n in ('Pterodactyl', 'Dinosaur'):
        d.update(vx=h.vx, timer=h.timer, idx=h.anim.i, delay=h.anim.t)
    return d


def py_fruit(st):
    # DS:2E5B is vx while falling and the landed-state timer afterwards
    return [None if f is None else
            (f.type, f.x, f.y, f.timer if f.state == 'LANDED' else f.vx, f.vy, f.sprite)
            for f in st.hz.slots]


def py_globals(st, npad):
    return dict(score=st.score, energy=st.world.energy,
                remaining=st.pm.remaining, water=st.water.level, line=st.water.line,
                pads=[(-1 if s is None else 2 * st.b_objects.index(s)) for s in st.pm.slot])
