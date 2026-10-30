from convert_textures import *

def textures(g, external):
    split = u(g, 40)
    h = g[48:split]
    l = bytearray(g[split:])
    offsets = children(h)
    nh = bytearray(h[:64] + h[80:offsets[0]])
    nh[:4] = b'TTDH'
    put(nh, 32, 64)
    for i, (a, z) in enumerate(zip(offsets, offsets[1:] + [len(h)])):
        put(nh, 64 + i * 4, len(nh))
        nh.extend(h[a:a + 16])
    put(nh, 16, len(nh))
    blob = bytearray(external[:16])
    blob.extend(bytes(48))
    l[:4] = b'TTDL'
    for i, o in enumerate(children(l)):
        size = u(l, u(l, 36) + i * 4)
        dds = gnf_to_dds(external[o:o + size])
        put(l, u(l, 32) + i * 4, len(blob))
        put(l, u(l, 36) + i * 4, len(dds))
        blob.extend(dds)
        blob.extend(bytes(-len(blob) % 64))
    put(blob, 4, len(blob))
    put(l, 68, len(blob))
    out = bytearray(g[:48])
    out[:4] = b'TTDM'
    put(out, 40, 48 + len(nh))
    out.extend(nh)
    out.extend(l)
    put(out, 16, len(out))
    return (out, blob)

def convert_model(tmc, tmcl):
    s = sections(tmc)
    lh = s['LHeader']
    bases = children(lh)
    tex, blob = textures(s['TTGM'], tmcl[bases[0]:bases[0] + u(lh, u(lh, 36))])
    external = bytearray(tmcl[:128])
    put(external, 0, 1)
    external.extend(blob)
    external.extend(bytes(-len(external) % 128))
    put(external, 4, len(external))
    newlh = bytearray(lh)
    for pos in (20, 24, 64, 80):
        put(newlh, pos, 1)
    put(newlh, 68, len(external))
    for pos in (116, 120, 132, 136, 148, 152):
        put(newlh, pos, 0)
    put(newlh, 144, len(blob))
    converted = {'TTGM': tex, 'LHeader': newlh, 'MdlGeo': rebuild(s['MdlGeo'], objgeo)}
    for index, tag in [(1, 'VtxLay'), (2, 'IdxLay')]:
        size = u(lh, u(lh, 36) + 4 * index)
        converted[tag] = convert_buffer(s[tag], tmcl[bases[index]:bases[index] + size])
    table = u(tmc, 32)
    slots = u(tmc, 20)
    entries = [(i, u(tmc, table + 4 * i)) for i in range(slots) if u(tmc, table + 4 * i)]
    out = bytearray(tmc[:entries[0][1]])
    put(out, 56, 4)
    for k, (index, a) in enumerate(entries):
        z = entries[k + 1][1] if k + 1 < len(entries) else len(tmc)
        old = tmc[a:z]
        tag = old[:8].rstrip(b'\x00').decode(errors='replace')
        new = converted[tag] if tag in converted else convert_section(old)
        put(out, table + 4 * index, len(out))
        out.extend(new)
    put(out, 16, len(out))
    return (out, external)
