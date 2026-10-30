from convert_model import *

def light(x):
    assert len(x) == 976 and u(x, 32) == 256 and (u(x, 256) == 176)
    out = bytearray(x[:32] + bytes(128))
    for dst, src in [(40, 312), (48, 320), (56, 328), (64, 352), (72, 336), (80, 344), (88, 360), (96, 304)]:
        target = 120 + (src - 304) // 2
        put(out, dst, target)
        out[target:target + 4] = x[src:src + 4]
    out.extend(x[368:])
    meta = len(out)
    out.extend(x[176:204])
    for pos in range(208, 248, 8):
        out.extend(struct.pack('<I', u(x, pos) - 208))
    put(out, 32, len(out))
    out.extend(struct.pack('<I', meta) + bytes(12))
    return out

def fog(x):
    assert len(x) == 336 and u(x, 32) == 144 and (u(x, 128) == 192)
    out = bytearray(x[:32] + bytes(64))
    for dst, src, target in [(40, 176, 72), (48, 160, 64), (56, 168, 68)]:
        put(out, dst, target)
        out[target:target + 4] = x[src:src + 4]
    out.extend(x[192:])
    out.extend(bytes(16))
    meta = len(out)
    out.extend(x[96:124])
    out.extend(struct.pack('<I', 96))
    put(out, 32, len(out))
    out.extend(struct.pack('<I', meta) + bytes(28))
    return out

def scattering(x):
    assert len(x) == 256 and u(x, 32) == 96 and (u(x, 80) == 112)
    out = bytearray(x[:32] + bytes(32))
    out.extend(x[112:])
    out.extend(bytes(16))
    meta = len(out)
    out.extend(x[48:76])
    out.extend(struct.pack('<I', 64))
    put(out, 32, len(out))
    out.extend(struct.pack('<I', meta) + bytes(28))
    return out

def convert_scene(scene, col10):
    out = bytearray(scene[:children(scene)[0]])
    converted_col = None
    for i, o in enumerate(children(scene)):
        size = u(scene, u(scene, 36) + 4 * i)
        x = scene[o:o + size]
        if i == 0:
            off = children(x)[1]
            tex, converted_col = textures(x[off:], col10)
            x = bytearray(x[:off]) + tex
            put(x, 16, len(x))
        elif i == 1:
            x = light(x)
        elif i == 2:
            x = fog(x)
        elif i == 3:
            x = scattering(x)
        put(out, u(out, 32) + 4 * i, len(out))
        put(out, u(out, 36) + 4 * i, len(x))
        out.extend(x)
    put(out, 16, len(out))
    return (out, converted_col)
