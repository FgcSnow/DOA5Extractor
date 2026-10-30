import sys, os
from scene47_common import *
from scene47_script import convert_b0, semantic
from scene47_tracks import convert_b1, same
from scene47_environment import convert_b3
from convert_animation import convert_animation

def is_le(x):
    b = blocks(x)
    return x[:6] == b'tdpack' and len(b) == 4 and (b[3][8] != 255) and (len(b[0]) < 80 or all((q(b[0], 8 * i) < len(b[0]) for i in range(10))))

def convert(x):
    assert is_le(x), 'PS4 scene is not little-endian'
    b = blocks(x)
    nb = [convert_b0(b[0]) if b[0] else b[0], convert_b1(b[1]) if b[1] else b[1], bytes(convert_animation(b[2])) if b[2][:8] == b'char_dat' else b[2], convert_b3(b[3])]
    n = u(x, 20)
    to = u(x, 32)
    so = u(x, 36)
    first = min((u(x, to + 4 * i) for i in range(n) if u(x, so + 4 * i))) if any((u(x, so + 4 * i) for i in range(n))) else 80
    out = bytearray(x[:first])
    for i, blk in enumerate(nb):
        out.extend(bytes(-len(out) % 16))
        put(out, to + 4 * i, len(out) if blk else 0)
        put(out, so + 4 * i, len(blk))
        out.extend(blk)
    out.extend(bytes(-len(out) % 16))
    put(out, 16, len(out))
    return bytes(out)
