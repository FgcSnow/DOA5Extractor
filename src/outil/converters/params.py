import struct
q = lambda b, o: struct.unpack_from('<Q', b, o)[0]
d = lambda b, o: struct.unpack_from('<I', b, o)[0]
A32 = lambda n: n + 31 & ~31
KIND = {'FOG': (64, 28, 0, 1), 'LIGHT': (120, 24, 1, 5)}

def simple(x, kind):
    base, nl, nint, nptr = KIND[kind]
    arr = q(x, 32)
    ents = []
    o = arr
    while o < len(x) and 0 < q(x, o) < arr:
        ents.append(q(x, o))
        o += 8
    k = (min(ents) - 32) // 16
    vals = sorted((q(x, 32 + 16 * i) for i in range(1, k)))
    vmin = vals[0]
    out = bytearray(x[:32]) + bytearray(8 * k)
    out += bytes(base - len(out)) if base > len(out) else b''
    assert len(out) == base, (hex(len(out)), hex(base))
    for v in vals:
        pos = base + (v - vmin) // 2
        if len(out) < pos + 4:
            out += bytes(pos + 4 - len(out))
        out[pos:pos + 4] = x[v:v + 4]
    for i in range(1, k):
        put = q(x, 32 + 16 * i)
        struct.pack_into('<I', out, 32 + 8 * i, base + (put - vmin) // 2)
    out += bytes(A32(len(out)) - len(out))
    ptroff = nl + 4 * nint + 7 & ~7
    firsts = [q(x, e + ptroff) for e in ents]
    order = sorted(range(len(ents)), key=lambda i: firsts[i])
    ends = [firsts[order[j + 1]] if j + 1 < len(order) else len(x) for j in range(len(order))]
    delta = {}
    for j, i in enumerate(order):
        s, e = (firsts[i], ends[j])
        out += bytes(A32(len(out)) - len(out))
        delta[i] = len(out) - s
        out += x[s:e]
    out += bytes(A32(len(out)) - len(out))
    epos = []
    for i, e in enumerate(ents):
        out += bytes(A32(len(out)) - len(out))
        epos.append(len(out))
        out += x[e:e + nl + 4 * nint]
        for p in range(nptr):
            out += struct.pack('<I', q(x, e + ptroff + 8 * p) + delta[i])
    if 4 * len(epos) > 32:
        out += bytes(A32(len(out)) - len(out))
    struct.pack_into('<I', out, 32, len(out))
    for p in epos:
        out += struct.pack('<I', p)
    out += bytes(A32(len(out) + 1) - len(out))
    return bytes(out)
