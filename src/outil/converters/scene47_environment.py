import sys
from scene47_common import *
import convert_scene as cs
import params, arealight
from convert_structures import children
import os

def sec(name, x):
    if name == 'LSCAT' and len(x) == 232:
        return default_scattering(x)
    tries = {'LIGHT': [cs.light, lambda x: arealight.area_light(x) if x[176:181] == b'area_' else params.simple(x, 'LIGHT')], 'FOG': [cs.fog, lambda x: params.simple(x, 'FOG')], 'LSCAT': [cs.scattering]}.get(name)
    if not tries:
        return x
    err = None
    for f in tries:
        try:
            return bytes(f(x))
        except Exception as e:
            err = e
    raise err

def slots(X):
    n = u(X, 20)
    tab = u(X, 32)
    return [u(X, tab + 4 * i) for i in range(n)]

def convert_b3(scene):
    ks = slots(scene)
    first = min((o for o in ks if o))
    out = bytearray(scene[:first])
    for i, o in enumerate(ks):
        if not o:
            continue
        size = u(scene, u(scene, 36) + 4 * i)
        x = scene[o:o + size]
        name = x[:8].rstrip(b'\x00').decode(errors='replace')
        x = sec(name, x)
        put(out, u(out, 32) + 4 * i, len(out))
        put(out, u(out, 36) + 4 * i, len(x))
        out.extend(x)
    put(out, 16, len(out))
    return bytes(out)

def secs(X):
    r = {}
    for i, o in enumerate(slots(X)):
        if o:
            size = u(X, u(X, 36) + 4 * i)
            x = X[o:o + size]
            r[i, x[:8].rstrip(b'\x00').decode(errors='replace')] = x
    return r

def default_scattering(x):
    if len(x) != 232 or u(x, 32) != 96 or u(x, 80) != 112:
        raise ValueError('Unsupported short scattering layout')
    out = bytearray(x[:32] + bytes(32))
    out.extend(x[112:192])
    for at in (192, 208, 224):
        out.extend(x[at + 4:min(at + 16, len(x))].ljust(12, b'\x00'))
        out.extend(x[at:at + 4])
    out.extend(bytes(32))
    meta = len(out)
    out.extend(x[48:76] + struct.pack('<I', 64))
    put(out, 32, len(out))
    out.extend(struct.pack('<I', meta) + bytes(28))
    return bytes(out)
