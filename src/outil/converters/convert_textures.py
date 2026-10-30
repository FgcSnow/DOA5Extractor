from convert_structures import *
import numpy as np

def bc4_channel(blocks):
    first = blocks[:, 0].astype(np.uint16)
    second = blocks[:, 1].astype(np.uint16)
    palette = np.zeros((len(blocks), 8), dtype=np.uint16)
    palette[:, 0] = first
    palette[:, 1] = second
    high = first > second
    for k in range(2, 8):
        if k < 6:
            palette[:, k] = np.where(high, ((8 - k) * first + (k - 1) * second) // 7, ((6 - k) * first + (k - 1) * second) // 5)
        elif k == 6:
            palette[:, k] = np.where(high, (2 * first + 5 * second) // 7, 0)
        else:
            palette[:, k] = np.where(high, (first + 6 * second) // 7, 255)
    bits = np.zeros(len(blocks), dtype=np.uint64)
    for j in range(6):
        bits |= blocks[:, j + 2].astype(np.uint64) << 8 * j
    indices = (bits[:, None] >> 3 * np.arange(16, dtype=np.uint64) & 7).astype(np.intp)
    return np.take_along_axis(palette, indices, axis=1).astype(np.uint8)

def bc5_bgra(data, width, height):
    bw = (width + 3) // 4
    bh = (height + 3) // 4
    blocks = np.frombuffer(data, dtype=np.uint8).reshape(-1, 16)
    red = bc4_channel(blocks[:, :8])
    green = bc4_channel(blocks[:, 8:])
    pixels = np.zeros((len(blocks), 16, 4), dtype=np.uint8)
    pixels[:, :, 2] = red
    pixels[:, :, 1] = green
    pixels[:, :, 3] = 255
    return pixels.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)[:height, :width].tobytes()

def untile(data, w, h, unit):
    pw = w + 7 & ~7
    ph = h + 7 & ~7
    yy, xx = np.indices((h, w), dtype=np.int64)
    mort = np.zeros((h, w), dtype=np.int64)
    for j in range(3):
        mort |= (xx >> j & 1) << 2 * j
        mort |= (yy >> j & 1) << 2 * j + 1
    idx = (yy // 8 * (pw // 8) + xx // 8) * 64 + mort
    source = np.frombuffer(data, dtype=np.uint8, count=pw * ph * unit).reshape(-1, unit)
    return (source[idx.ravel()].tobytes(), pw * ph * unit)

def gnf_to_dds(b):
    assert b[:4] == b'GNF '
    width = (u(b, 24) & 16383) + 1
    height = (u(b, 24) >> 14 & 16383) + 1
    mips = (u(b, 28) >> 16 & 15) + 1
    kind = u(b, 28) >> 28
    fmt = u(b, 20) >> 20 & 63
    formats = {10: (b'\x00\x00\x00\x00', 4), 35: (b'DXT1', 8), 36: (b'DXT3', 16), 37: (b'DXT5', 16), 38: (b'ATI1', 8), 39: (b'ATI2', 16)}
    if fmt not in formats:
        raise ValueError(f'Unsupported GNF format {fmt}, {width}x{height}')
    fourcc, unit = formats[fmt]
    cube = kind == 11
    if kind not in (9, 10, 11):
        raise ValueError(f'Unsupported texture kind {kind}')
    head = bytearray(128)
    head[:4] = b'DDS '
    for off, val in [(4, 124), (8, 528391 | (131072 if mips > 1 else 0)), (12, height), (16, width), (20, max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * unit), (28, mips), (76, 32), (80, 4), (108, 4096 | (4194312 if mips > 1 else 0)), (112, 65024 if cube else 0)]:
        put(head, off, val)
    head[84:88] = fourcc
    if fmt in (10, 39):
        head[84:88] = bytes(4)
        put(head, 20, width * height * 4)
        put(head, 80, 65)
        put(head, 88, 32)
        for off, val in [(92, 16711680), (96, 65280), (100, 255), (104, 4278190080)]:
            put(head, off, val)
        if mips == 1:
            put(head, 28, 0)
    depth = (u(b, 32) & 8191) + 1 if kind == 10 else 1
    if kind == 10:
        put(head, 8, u(head, 8) | 8388608)
        put(head, 24, depth)
        put(head, 20, u(head, 20) * depth)
        put(head, 112, 2097152)
    pos = 48
    faces = [bytearray() for _ in range(6 if cube else 1)]
    for level in range(mips):
        block = 1 if fmt == 10 else 4
        w = max(1, (max(1, width >> level) + block - 1) // block)
        h = max(1, (max(1, height >> level) + block - 1) // block)
        for face in range(8 if cube else max(1, depth >> level)):
            data, used = untile(b[pos:], w, h, unit)
            pos += used
            if fmt == 39:
                assert u(b, 28) & 4095 == 556
                data = bc5_bgra(data, max(1, width >> level), max(1, height >> level))
            if kind == 10:
                faces[0].extend(data)
            elif face < len(faces):
                faces[face].extend(data)
    return bytes(head) + b''.join(faces)

def validate():
    ps = sections((ROOT / 'pairs/43-tmc-ps4.bin').read_bytes())['TTGM']
    ps = ps[u(ps, 40):]
    pc = sections((ROOT.parent / 'provided-port/payload/CodexDangerZone/DANGER_ZONE.TMC').read_bytes())['TTDM']
    pc = pc[u(pc, 40):]
    a = (ROOT / 'pairs/43-tmcl-ps4.bin').read_bytes()
    b = (ROOT.parent / 'provided-port/payload/CodexDangerZone/DANGER_ZONE.TMCL').read_bytes()
    report = []
    for i, (o, p) in enumerate(zip(children(ps), children(pc))):
        size = u(ps, u(ps, 36) + 4 * i)
        expected = u(pc, u(pc, 36) + 4 * i)
        actual = gnf_to_dds(a[128 + o:128 + o + size])
        target = b[128 + p:128 + p + expected]
        report.append({'index': i, 'equal': actual == target, 'size': len(actual), 'expected': expected, 'first_diff': next((j for j, (x, y) in enumerate(zip(actual, target)) if x != y), None)})
    (ROOT / 'texture-validation.json').write_text(json.dumps(report, indent=2))
    print('Exact', sum((r['equal'] for r in report)), '/', len(report))
    for r in report:
        if not r['equal']:
            print(r)
