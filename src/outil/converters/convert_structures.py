from pathlib import Path
import struct, json
ROOT = Path(__file__).resolve().parent

def u(b, o):
    return struct.unpack_from('<I', b, o)[0]

def put(b, o, v):
    struct.pack_into('<I', b, o, v)

def children(b):
    n = u(b, 24)
    tab = u(b, 32)
    return list(struct.unpack_from('<' + 'I' * n, b, tab)) if n else []

def rebuild(b, fn):
    offs = children(b)
    if not offs:
        return bytearray(b)
    out = bytearray(b[:offs[0]])
    for i, (a, z) in enumerate(zip(offs, offs[1:] + [len(b)])):
        put(out, u(b, 32) + i * 4, len(out))
        out.extend(fn(b[a:z]))
    put(out, 16, len(out))
    return out

def nodeobj(b):
    b = bytearray(b)
    for pos in (32, 36, 40):
        if u(b, pos) >= 80:
            put(b, pos, u(b, pos) - 16)
    del b[64:80]
    if u(b, 24):
        for i in range(u(b, 24)):
            pos = u(b, 32) + i * 4
            put(b, pos, u(b, pos) - 16)
        b = rebuild(b, lambda x: x[:16] + x[32:])
    put(b, 16, len(b))
    return b

def objinfo(b):
    return rebuild(b, lambda x: x[:64] + x[80:])

def convert_section(b):
    tag = b[:8].rstrip(bytes([0]))
    if tag == b'NodeLay':
        return rebuild(b, nodeobj)
    if tag == b'HieLay':
        return rebuild(b, lambda x: x[:80] + x[96:])
    if tag == b'MdlInfo':
        return rebuild(b, objinfo)
    return bytearray(b)

def sections(b):
    return {b[o:o + 8].rstrip(bytes([0])).decode(): b[o:o + u(b, o + 16)] for o in children(b) if o}

def align(n):
    return n + 15 & ~15

def convert_buffer(sec, external):
    count = u(sec, 24)
    out = bytearray(sec[:48])
    put(out, 12, 48)
    put(out, 32, 48)
    lengths = align(48 + 4 * count)
    start = align(lengths + 4 * count)
    put(out, 36, lengths)
    out.extend(bytes(start - len(out)))
    for i in range(count):
        off = u(sec, u(sec, 32) + i * 4)
        length = u(sec, u(sec, 36) + i * 4)
        put(out, 48 + i * 4, len(out))
        put(out, lengths + i * 4, length)
        out.extend(external[off:off + length])
    put(out, 16, len(out))
    return out

def remove_ranges(b, ranges):
    out = bytearray(b)
    for a, z in reversed(ranges):
        del out[a:z]
    return out

def geodecl_record(b):
    count = u(b, 72)
    out = bytearray(b[:32] + b[48:80] + b[88:88 + count * 8])
    put(out, 4, 48)
    out.extend(bytes(align(len(out)) - len(out)))
    return out

def mesh(b):
    count = u(b, 16)
    header = remove_ranges(b[:232], [(12, 16), (68, 72), (100, 104), (108, 112), (212, 216), (220, 224)])
    assert len(header) == 208
    for i in range(count):
        pos = u(b, 20 + 4 * i)
        material = b[pos:pos + 144]
        put(header, 16 + 4 * i, len(header))
        header.extend(material[:16] + material[32:80] + material[96:144])
    header.extend(bytes(align(len(header)) - len(header)))
    return header

def objgeo(b):
    table = u(b, 32)
    decl = u(b, 40)
    offs = children(b)
    out = bytearray(b[:80] + b[96:decl])
    put(out, 32, table - 16)
    put(out, 40, len(out))
    newdecl = rebuild(b[decl:offs[0]], geodecl_record)
    out.extend(newdecl)
    for i, (a, z) in enumerate(zip(offs, offs[1:] + [len(b)])):
        put(out, u(out, 32) + 4 * i, len(out))
        out.extend(mesh(b[a:z]))
    put(out, 16, len(out))
    return out
