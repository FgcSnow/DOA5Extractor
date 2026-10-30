from scene47_common import *

class Model:

    def __init__(s, X, W):
        s.X = X
        s.W = W
        s.rd = (lambda o: q(X, o)) if W == 8 else lambda o: u(X, o)
        s.kind = {}
        d = [s.rd(W * i) for i in range(10)]
        s.dir = d
        s.kind[0] = 'w'
        s.add(d[0], 'r')
        for i in (1, 2, 3, 4, 5, 6, 7, 8, 9):
            if d[i]:
                s.add(d[i], 'S3' if i == 3 else 'w')
        S3 = d[3]
        f = s.s3(S3)
        s.S3f = f
        if f['scripts']:
            s.add(f['scripts'], 'w')
        for k in ('X', 'Z'):
            if f[k]:
                s.add(f[k], 'r')
        for k in ('Y', 'W'):
            if f[k]:
                s.add(f[k], 'w')
        if f['V'] >= s.minptr() and f['V'] < len(X):
            s.add(f['V'], 'w')
        if f['scripts']:
            for v in s.words(f['scripts']):
                if s.isptr(v):
                    s.kind.setdefault(v, 'r')
        for i in (5, 7):
            if d[i]:
                v = s.rd(d[i])
                if s.isptr(v):
                    s.kind.setdefault(v, 'w')
        for src in (f['W'], f['Y']):
            if not src:
                continue
            for v in s.words(src):
                if s.isptr(v) and v not in s.kind and (not s.inside_raw(v)):
                    s.kind[v] = 'r'

    def minptr(s):
        return 10 * s.W

    def s3(s, o):
        W = s.W
        r = s.rd
        return {'scripts': r(o), 'X': r(o + W), 'Y': r(o + 2 * W), 'a': u(s.X, o + 3 * W), 'b': u(s.X, o + 3 * W + 4), 'Z': r(o + 3 * W + 8), 'V': r(o + 4 * W + 8), 'W': r(o + 5 * W + 8), 'size': 6 * W + 8}

    def add(s, o, k):
        if o and o < len(s.X):
            s.kind.setdefault(o, k)

    def starts(s):
        return sorted(s.kind)

    def end(s, o):
        n = [x for x in s.kind if x > o]
        return min(n) if n else len(s.X)

    def words(s, o):
        e = s.end(o) if s.kind[o] != 'S3' else o + s.S3f['size']
        if o == s.S3f.get('Y') and s.kind[o] == 'w':
            e = min(e, o + s.W * s.S3f['a'])
        return [s.rd(p) for p in range(o, e - s.W + 1, s.W)]

    def inside_raw(s, v):
        return any((o < v < s.end(o) and k == 'r' for o, k in s.kind.items()))

    def inside(s, v):
        return any((o < v < s.end(o) for o in s.kind))

    def isptr(s, v):
        return s.minptr() <= v < len(s.X) and v % 4 == 0

def convert_b0(A):
    m = Model(A, 8)
    out = bytearray()
    where = {}
    order = [0] + [o for o in m.starts() if o != 0]
    sizes = {}
    for o in order:
        k = m.kind[o]
        if k == 'w':
            sizes[o] = 4 * len(m.words(o))
        elif k == 'S3':
            sizes[o] = 4 * 6 + 8
        else:
            sizes[o] = m.end(o) - o
    pos = 0
    for o in order:
        al = 16 if m.kind[o] == 'r' else 4
        pos += -pos % al
        where[o] = pos
        pos += sizes[o]
    out = bytearray(pos + -pos % 16)

    def mp(v):
        if not m.isptr(v):
            return v
        if v in where:
            return where[v]
        for o in m.kind:
            if o < v < m.end(o) and m.kind[o] == 'r':
                return where[o] + (v - o)
        return v
    for o in order:
        k = m.kind[o]
        p = where[o]
        if k == 'r':
            out[p:p + sizes[o]] = A[o:m.end(o)]
        elif k == 'w':
            for i, v in enumerate(m.words(o)):
                put(out, p + 4 * i, mp(v) if o != 0 or True else v)
        else:
            f = m.S3f
            vals = [mp(f['scripts']), mp(f['X']), mp(f['Y']), f['a'], f['b'], mp(f['Z']), mp(f['V']), mp(f['W'])]
            for i, v in enumerate(vals):
                put(out, p + 4 * i, v)
    for v in m.words(m.S3f['scripts']):
        if m.isptr(v) and v in where:
            remap_ops(out, where[v], m.end(v) - v)
    return bytes(out)
PS4_POST = {317: 5, 318: 6}

def remap_ops(out, o, n):
    i = 0
    while i + 4 <= n:
        op, sz = struct.unpack_from('<HH', out, o + i)
        if sz == 0:
            break
        if op in PS4_POST:
            struct.pack_into('<H', out, o + i, PS4_POST[op])
        elif op > 318:
            struct.pack_into('<H', out, o + i, op - 2)
        if op == 1:
            break
        i += 4 * sz

def semantic(X, W):
    m = Model(X, W)
    f = m.S3f

    def tgt(v):
        if not m.isptr(v):
            return ('int', v)
        if v in m.kind:
            k = m.kind[v]
            return ('obj', k, X[v:m.end(v)][:64] if k == 'r' else tuple(sem_w(v)))
        for o in m.kind:
            if o < v < m.end(o):
                return ('in', X[v:v + 20])
        return ('?', v)

    def strip(L):
        while L and L[-1] in (('int', 0),):
            L = L[:-1]
        return L

    def sem_w(o):
        return strip([('ptr' if m.isptr(v) else 'int', v if not m.isptr(v) else 0) for v in m.words(o)])
    keys = X[m.dir[0]:m.end(m.dir[0])].split(struct.pack('<I', 10))[0]
    scripts = [X[v:m.end(v)].rstrip(b'\x00') for v in m.words(f['scripts']) if m.isptr(v)]
    res = {'keys': keys, 'ab': (f['a'], f['b']), 'scripts': scripts, 'X': X[f['X']:f['X'] + 32] if f['X'] else None, 'Z': X[f['Z']:m.end(f['Z'])][:8 * f['b']] if f['Z'] else None, 'Y': strip([tgt(v) for v in m.words(f['Y'])]) if f['Y'] else None, 'V': f['V'] if not m.isptr(f['V']) else 'ptr'}
    for n, i in (('S5', 5), ('S7', 7)):
        o = m.dir[i]
        res[n] = strip([tgt(v) for v in m.words(o)]) if o else None
    return res
