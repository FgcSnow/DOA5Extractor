import struct

GROUPES, PISTES = 5, 2
FINS = (0x0C, 0x26)


def _q(b, o):
    if o < 0 or o + 8 > len(b):
        raise ValueError('DANGER_ZONE.TABLE: offset outside the table')
    return struct.unpack_from('<Q', b, o)[0]


def _piste(b, debut):
    o = debut
    while True:
        if o + 4 > len(b):
            raise ValueError('DANGER_ZONE.TABLE: camera script not terminated')
        op, mots = struct.unpack_from('<HH', b, o)
        if not mots:
            raise ValueError('DANGER_ZONE.TABLE: empty camera instruction')
        o += 4 * mots
        if op in FINS:
            if o > len(b):
                raise ValueError('DANGER_ZONE.TABLE: camera script not terminated')
            return b[debut:o]


def pistes(table):
    b = bytes(table)
    repertoire = _q(b, 0)
    out = {}
    for g in range(GROUPES):
        nombre, liste = _q(b, repertoire + 16 * g), _q(b, repertoire + 16 * g + 8)
        if nombre != PISTES:
            raise ValueError('DANGER_ZONE.TABLE: unexpected camera group')
        for t in range(PISTES):
            out['Group%uTrack%u' % (g, t)] = _piste(b, _q(b, liste + 8 * t))
    return out


def d5sd(blocs, taille_element=4):
    out = bytearray(b'D5SD' + struct.pack('<II', 1, len(blocs)))
    for nom, donnees in blocs.items():
        n = nom.encode('ascii')
        if len(donnees) % taille_element:
            raise ValueError('D5SD: block size is not a multiple of the element size')
        out += struct.pack('<H', len(n)) + n + struct.pack('<II', taille_element, len(donnees)) + donnees
    return bytes(out)


def apercu(table):
    return d5sd(pistes(table))
