import struct

IMAGE = 0x1598000 + 0x2156B18
EXCLURE, INCLURE = 0x427CBFCE4B5D, 0x1BD834031B4A2
FICHES = 1915


def image(eboot):
    d = bytes(eboot)
    if len(d) < 0x40:
        raise ValueError('eboot.bin: file too small')
    n = struct.unpack_from('<H', d, 0x18)[0]
    segs = [struct.unpack_from('<QQQQ', d, 0x20 + i * 0x20) for i in range(n)]
    eo = 0x20 + n * 0x20
    phoff, = struct.unpack_from('<Q', d, eo + 0x20)
    phn, = struct.unpack_from('<H', d, eo + 0x38)
    ph = [struct.unpack_from('<IIQQQQQQ', d, eo + phoff + i * 0x38) for i in range(phn)]

    def segment(i):
        for fl, so, cs, us in segs:
            if (fl >> 20) & 0xFFF == i and fl & 0x800:
                if fl & 10:
                    raise ValueError('eboot.bin: encrypted or compressed segment (readable backup required)')
                return d[so:so + cs]
        raise ValueError('eboot.bin: missing segment')

    img = bytearray(IMAGE)
    dynoff = dynsz = dyndata = ddoff = None
    for i, (t, f, off, va, pa, fsz, msz, al) in enumerate(ph):
        if t == 1:
            b = segment(i)
            if va + len(b) > IMAGE:
                raise ValueError('eboot.bin: segment outside the image')
            img[va:va + len(b)] = b
        if t == 2:
            dynoff, dynsz = off, fsz
        if t == 0x61000000:
            dyndata, ddoff = segment(i), off
    if dyndata is None or dynoff is None:
        raise ValueError('eboot.bin: dynamic data missing')
    dyn = dyndata[dynoff - ddoff:dynoff - ddoff + dynsz]
    tags = {}
    for j in range(0, len(dyn) - 15, 16):
        tg, v = struct.unpack_from('<qQ', dyn, j)
        tags.setdefault(tg, v)
    ro, rs = tags[0x6100002F], tags[0x61000031]
    for j in range(ro, ro + rs, 24):
        off, info, add = struct.unpack_from('<QQq', dyndata, j)
        if info & 0xFFFFFFFF == 8:
            struct.pack_into('<Q', img, off, add)
    return bytes(img)


def donnees(eboot):
    img = image(eboot)
    premier = [0] * 64; nombre = [0] * 64; fiches = []
    for c in range(64):
        if (EXCLURE >> c) & 1 or not (INCLURE >> c) & 1:
            continue
        n = struct.unpack_from('<I', img, 0xFA2370 + 4 * c)[0]
        p = struct.unpack_from('<Q', img, 0x1B4ED10 + 8 * c)[0]
        if n >= 1000 or p + n * 224 > len(img):
            raise ValueError('eboot.bin: invalid costume table')
        premier[c] = len(fiches); nombre[c] = n
        for i in range(n):
            r = img[p + i * 224:p + (i + 1) * 224]
            fiches.append(bytes((r[0], r[1], r[2], r[0x3C], r[0x94], 0, 0, 0)) + r[4:60] + r[64:148] + r[152:224])
    if len(fiches) != FICHES:
        raise ValueError('eboot.bin: unexpected costume table')
    blocs = [('opt_lr', 4, img[0xFA25D0 + 4 * 48:0xFA25D0 + 5 * 48]),
             ('swing_ps4', 72, img[0x160BB20 + 5 * 72:0x160BB20 + 10 * 72]),
             ('constantes', 4, b''.join(img[p:p + 4] for p in (0x1044D30, 0x1044D3C, 0x1044D5C))),
             ('premier', 2, struct.pack('<64H', *premier)), ('nombre', 2, struct.pack('<64H', *nombre)),
             ('fiches', 220, b''.join(fiches))]
    out = bytearray(struct.pack('<4sII', b'D5SD', 1, len(blocs)))
    for nom, taille, data in blocs:
        n = nom.encode('ascii')
        out += struct.pack('<H', len(n)) + n + struct.pack('<II', taille, len(data)) + data
    return bytes(out)
