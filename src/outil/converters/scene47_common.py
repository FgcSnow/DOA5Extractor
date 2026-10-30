import struct, glob, os
u = lambda b, o: struct.unpack_from('<I', b, o)[0]
q = lambda b, o: struct.unpack_from('<Q', b, o)[0]

def put(b, o, v):
    struct.pack_into('<I', b, o, v)

def subs(x, base=0):
    n = u(x, base + 20)
    to = u(x, base + 32)
    so = u(x, base + 36)
    return [(base + u(x, base + to + 4 * i), u(x, base + so + 4 * i)) for i in range(n)]

def blocks(x):
    return [x[o:o + s] for o, s in subs(x)]
