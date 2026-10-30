import struct
q = lambda b, o: struct.unpack_from('<Q', b, o)[0]
d = lambda b, o: struct.unpack_from('<I', b, o)[0]
A = lambda n, a: n + a - 1 & ~(a - 1)

def pad(out, a):
    out += bytes(A(len(out), a) - len(out))

def ptrs(x, o, stop):
    r = []
    while o < stop and 0 < q(x, o) < len(x):
        r.append(q(x, o))
        o += 8
    return r

def area_light(x):
    S = [q(x, 32 + 16 * i) for i in range(9)]
    earr, aarr, sarr, s7, s8 = (S[0], S[1], S[4], S[7], S[8])
    areas = ptrs(x, aarr, sarr)
    ents = ptrs(x, earr, len(x))
    spts = ptrs(x, sarr, min(ents + [earr]))
    ents = [e for e in ents if e < earr]
    cells = [[(q(x, a + 48 + 16 * c), d(x, a + 56 + 16 * c)) for c in range(7)] for a in areas]
    lo = min((p for cs in cells for p, _ in cs if p))
    hdrv = sorted((S[i] for i in (2, 3, 5, 6)))
    out = bytearray(x[:32]) + bytearray(72)
    base1 = len(out)
    m = {}
    for v in range(lo, s8 + 8, 8):
        m[v] = base1 + (v - lo) // 2
    blk = bytearray((s8 + 8 - lo) // 2)
    for v in range(lo, s8 + 8, 8):
        struct.pack_into('<I', blk, (v - lo) // 2, d(x, v))
    out += blk
    pad(out, 32)
    apos = []
    for a, cs in zip(areas, cells):
        pad(out, 32)
        apos.append(len(out))
        o = bytearray(x[a:a + 48])
        for p, c in cs:
            o += struct.pack('<II', m[p] if p else 0, c)
        out += o
    pad(out, 32)
    aarr_pc = len(out)
    for p in apos:
        out += struct.pack('<I', p)
    pad(out, 8)
    hbase = len(out)
    for v in hdrv:
        m[v] = hbase + (v - hdrv[0]) // 2
    blk = bytearray((hdrv[-1] + 8 - hdrv[0]) // 2)
    for v in hdrv:
        struct.pack_into('<I', blk, (v - hdrv[0]) // 2, d(x, v))
    out += blk
    spos = []
    for s in spts:
        pad(out, 32)
        spos.append(len(out))
        out += x[s:s + 208]
    sarr_pc = len(out)
    for p in spos:
        out += struct.pack('<I', p)
    pad(out, 8)
    s7pc = len(out)
    out += x[s7:min((q(x, e + 32) for e in ents))]
    ptroff = 32
    first = min((q(x, e + ptroff) for e in ents))
    pad(out, 32)
    delta = len(out) - first
    out += x[first:]
    epos = []
    for e in ents:
        pad(out, 32)
        epos.append(len(out))
        out += x[e:e + 28]
        for p in range(5):
            out += struct.pack('<I', q(x, e + ptroff + 8 * p) + delta)
    if 4 * len(epos) > 32:
        pad(out, 32)
    earr_pc = len(out)
    for p in epos:
        out += struct.pack('<I', p)
    out += bytes(A(len(out) + 1, 32) - len(out))
    PC = [earr_pc, m[S[1]] if False else aarr_pc, m[S[2]], m[S[3]], sarr_pc, m[S[5]], m[S[6]], s7pc, m[S[8]]]
    for i, v in enumerate(PC):
        struct.pack_into('<I', out, 32 + 8 * i, v)
    return bytes(out)
