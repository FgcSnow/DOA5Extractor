from scene47_common import *

def parse_ps4(A):
    if len(A) < 16:
        return None
    pA, pB = (q(A, 0), q(A, 8))
    n = u(A, pA)
    pl = q(A, pA + 8)
    tracks = [q(A, pl + 8 * i) for i in range(n)]
    hs = []
    o = pB
    while q(A, o):
        hs.append(q(A, o))
        o += 8
    H = [(u(A, h), u(A, h + 4), [q(A, h + 8 + 8 * k) for k in range(4)]) for h in hs]
    Aextra = [(u(A, pA + 16 * i), q(A, pA + 16 * i + 8)) for i in range(1, 5)]
    starts = {pA, pB, pl, *tracks, *hs, *(p for _, _, ps in H for p in ps if p)}
    return dict(A=pA, B=pB, list=pl, tracks=tracks, hs=hs, H=H, Aextra=Aextra, starts=sorted((s for s in starts if s)))

def parse_pc(B):
    if len(B) < 8:
        return None
    pA, pB = (u(B, 0), u(B, 4))
    n = u(B, pA)
    pl = u(B, pA + 4)
    tracks = [u(B, pl + 4 * i) for i in range(n)]
    hs = []
    o = pB
    while u(B, o):
        hs.append(u(B, o))
        o += 4
    H = [(u(B, h), u(B, h + 4), [u(B, h + 8 + 4 * k) for k in range(4)]) for h in hs]
    starts = {pA, pB, pl, *tracks, *hs, *(p for _, _, ps in H for p in ps if p)}
    return dict(A=pA, B=pB, list=pl, tracks=tracks, hs=hs, H=H, starts=sorted((s for s in starts if s)))

def extent(X, starts, o):
    nxt = [s for s in starts if s > o]
    return X[o:nxt[0] if nxt else len(X)]

def convert_b1(A):
    p = parse_ps4(A)
    if p is None:
        return A
    out = bytearray(32)
    where = {}

    def emit(o, align):
        if o in where:
            return where[o]
        out.extend(bytes(-len(out) % align))
        where[o] = len(out)
        out.extend(extent(A, p['starts'], o))
        return where[o]
    for _, _, ps in p['H']:
        for x in ps:
            if x:
                emit(x, 32)
    for t in p['tracks']:
        emit(t, 16)
    hpos = []
    for fr, kind, ps in p['H']:
        out.extend(bytes(-len(out) % 4))
        hpos.append(len(out))
        out.extend(struct.pack('<II', fr, kind) + b''.join((struct.pack('<I', where[x] if x else 0) for x in ps)))
    out.extend(bytes(-len(out) % 4))
    B = len(out)
    for h in hpos:
        out.extend(struct.pack('<I', h))
    out.extend(b'\x00' * 4)
    L = len(out)
    for t in p['tracks']:
        out.extend(struct.pack('<I', where[t]))
    for a, b in p['Aextra']:
        assert a == 0 and b == 0, 'Scene tracks: A pairs 1-4 not empty (unsupported)'
    Aoff = len(out)
    out.extend(struct.pack('<II', len(p['tracks']), L) + bytes(32))
    put(out, 0, Aoff)
    put(out, 4, B)
    out.extend(bytes(-len(out) % 16))
    return bytes(out)

def same(Bx, Cx):
    pb, pc = (parse_pc(Bx), parse_pc(Cx))
    if pc and any((u(Cx, pc['A'] + 8 + 4 * i) for i in range(8))):
        return False
    if (pb is None) != (pc is None):
        return False
    if pb is None:
        return True
    if len(pb['H']) != len(pc['H']) or len(pb['tracks']) != len(pc['tracks']):
        return False
    for (f1, k1, a1), (f2, k2, a2) in zip(pb['H'], pc['H']):
        if (f1, k1) != (f2, k2):
            return False
        for x, y in zip(a1, a2):
            if bool(x) != bool(y):
                return False
            if x:
                ex = extent(Bx, pb['starts'], x)
                ey = extent(Cx, pc['starts'], y)
                m = min(len(ex), len(ey))
                if ex[:m] != ey[:m] or m < len(ex) - 32:
                    return False
    for x, y in zip(pb['tracks'], pc['tracks']):
        ex = extent(Bx, pb['starts'], x)
        ey = extent(Cx, pc['starts'], y)
        m = min(len(ex), len(ey))
        if ex[:m] != ey[:m] or m < len(ex) - 32:
            return False
    return True
