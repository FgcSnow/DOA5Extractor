import struct

import numpy as np

from convert_textures import untile

VIGNETTES = ((25, 56), (28, 59), (3, 60), (1, 58), (13, 61))
LARGEUR, HAUTEUR = 124, 72


def u(b, o):
    return struct.unpack_from("<I", b, o)[0]


def u64(b, o):
    return struct.unpack_from("<Q", b, o)[0]


def morceaux(sprpack):
    if sprpack[:8] != b"sprpack\0" or u(sprpack, 24) != 4:
        raise ValueError("Stage menu: sprite bank not recognized")
    debuts = [u(sprpack, 48 + 4 * i) for i in range(4)]
    return [sprpack[a:c] for a, c in zip(debuts, debuts[1:] + [len(sprpack)])]


def table_textures(sprpack):
    tdm = morceaux(sprpack)[0]
    liste = tdm[u(tdm, 40):]
    n = u(liste, 20)
    pos = [u(liste, u(liste, 32) + 4 * i) for i in range(n)]
    lon = [u(liste, u(liste, 36) + 4 * i) for i in range(n)]
    return pos, lon


class MenuPS4:

    def __init__(self, sprpack, textures):
        pos, lon = table_textures(sprpack)
        if not pos or pos[-1] + lon[-1] > len(textures):
            raise ValueError("Stage menu: PS4 texture bank truncated")
        self.gnf = [textures[a:a + n] for a, n in zip(pos, lon)]
        infos = morceaux(sprpack)[1]
        self.infos = [(u64(infos, 0x20 + 24 * i), u64(infos, 0x28 + 24 * i),
                       struct.unpack_from("<f", infos, 0x30 + 24 * i)[0]) for i in range(u(infos, 16))]

    def pixels(self, i):
        g = self.gnf[i]
        if g[:4] != b"GNF ":
            raise ValueError("Stage menu: PS4 texture not recognized")
        w = (u(g, 24) & 0x3FFF) + 1
        h = (u(g, 24) >> 14 & 0x3FFF) + 1
        unite = {1: 1, 10: 4}[u(g, 20) >> 20 & 0x3F]
        px, _ = untile(g[0x30:], w, h, unite)
        return np.frombuffer(px, np.uint8).reshape(h, w, unite)

    def vignette(self, i):
        idx = self.pixels(i)[:, :, 0]
        if idx.shape != (HAUTEUR, LARGEUR):
            raise ValueError("Stage menu: PS4 thumbnail size differs")
        _, palette, v = self.infos[i]
        pal = self.pixels(palette)
        return idx, pal[int(v * pal.shape[0])]


def vignettes(sprpack_pc, textures_pc, sprpack_ps4, textures_ps4):
    ps4 = MenuPS4(sprpack_ps4, textures_ps4)
    pos, lon = table_textures(sprpack_pc)
    infos = morceaux(sprpack_pc)[1]
    infos_pc = [struct.unpack_from("<IIf", infos, 0x20 + 12 * i) for i in range(u(infos, 16))]
    sortie = bytearray(textures_pc)
    for emplacement, source in VIGNETTES:
        dds = textures_pc[pos[emplacement]:pos[emplacement] + lon[emplacement]]
        if (u(dds, 16), u(dds, 12), u(dds, 88)) != (LARGEUR, HAUTEUR, 8):
            raise ValueError("Stage menu: PC thumbnail slot differs")
        idx, ligne = ps4.vignette(source)
        a = pos[emplacement] + 128
        sortie[a:a + LARGEUR * HAUTEUR] = idx.tobytes()
        _, palette, v = infos_pc[emplacement]
        dds_pal = textures_pc[pos[palette]:pos[palette] + lon[palette]]
        if (u(dds_pal, 16), u(dds_pal, 12)) != (256, 8):
            raise ValueError("Stage menu: PC palette differs")
        a = pos[palette] + 128 + int(v * 8) * 256 * 4
        sortie[a:a + 1024] = ligne.tobytes()
    return bytes(sortie)


def raw8(sprpack_ps4, textures_ps4, source):
    idx, ligne = MenuPS4(sprpack_ps4, textures_ps4).vignette(source)
    return idx.tobytes() + ligne.tobytes()


def mise_en_page(lay_pc, cases=31):
    if len(lay_pc) != 928 or u(lay_pc, 0x30) != 27 or u(lay_pc, 0x34) != 0x180:
        raise ValueError("Stage menu: PC layout not recognized")
    fin = 0x180 + 27 * 16
    if any(struct.unpack_from("<4i", lay_pc, 0x180 + 16 * k) != (13, k, -1, -1) for k in range(27)):
        raise ValueError("Stage menu: PC layout list differs")
    decalages = [o for o in range(4, 0x180, 8) if fin <= u(lay_pc, o) < len(lay_pc)]
    if decalages != [0x3C, 0x7C, 0x84]:
        raise ValueError("Stage menu: PC layout offsets differ")
    ajout = b"".join(struct.pack("<4i", 13, k, -1, -1) for k in range(27, cases))
    sortie = bytearray(lay_pc[:fin] + ajout + lay_pc[fin:])
    struct.pack_into("<I", sortie, 0x30, cases)
    for o in decalages:
        struct.pack_into("<I", sortie, o, u(lay_pc, o) + len(ajout))
    return bytes(sortie)
