import hashlib
import os
import shutil
import struct
import zlib
from pathlib import Path

TAILLE_PC = 280528896
SHA_PC = "9c4709085aa1ca30be478be11675a54f6b00a1d1281e5b999b96ac638e1a63b7"
TAILLE_SORTIE = 282050560
SHA_SORTIE = "91ac20f972cbb2f9256b4d2c15e4817c5140ae800589f5ec8cfa7d42b1271f90"

TMC_PC = (931, 354308, 1023488, "09070b47d2415cc4b87d6e2da0f8fb9b6c84770d88dd3af541028b95f1f7be5b")
TMCL_PC = (543, 1054532, 2926592, "70594180f97ed723bc4d4f0cef68e70b5a52f33e7b08c11f47fe5155fc70fc8f")
TMC_PS4 = (632784, "38d5c8e56efdd7ce4a6f6c783d607fd38af4079133aa4976410c2e675427ecae")
TMCL_PS4 = (4323072, "49800e3c239294130de27825525d043f224532633ff4bbde41a2009d6af2fed6")

ETAPES = {"TMC": (1133872, "77f68834ddd23253ca8512cb1349efa9915edbfcfce43d87fb4a952239ca80a4",
                  399604, "a6263cdf9ef4a76c8575fcb094ec521958e5c76553d74bd9788b8b92c68ce57b"),
          "TMCL": (3493248, "dfa8086cf3da240daa41344e417aa1134b52b09e96fe7102b0abfe3ed4ef8d57",
                   1119092, "a540e1c5530c7a65962f0e2b0824b8a96222dc3359b8e9722d5a6589066e91cb")}

HACHAGES_PC = [
    ("cmn_filter", 0xC0694AA8), ("cmn_launcher", 0xC9C44515), ("cmn_dust_cemen", 0x7B38DCEE),
    ("cmn_hit_pb", 0x4C0B3AA3), ("cmn_dust_earth", 0x8DF6D72C), ("cmn_dust_cemen", 0xDDDD31D9),
    ("cmn_spark", 0x367D7B55), ("cmn_sweat_pb_a", 0x329F7301), ("cmn_sweat_pb", 0x11B0C053),
    ("cmn_dust_wood", 0x60384865), ("cmn_dust", 0x65D69926), ("cmn_dust_grass", 0x976C2061),
    ("cmn_dust_earth", 0x0589F274), ("cmn_guard", 0x91479409), ("cmn_sweat", 0x7E441ACD),
    ("cmn_dust_wood_", 0x2E51CDF7), ("cmn_trail", 0xF8978544), ("cmn_splash", 0xF122EC11),
    ("cmn_fallingcra", 0x7F5A980E), ("cmn_forced_cha", 0x1A894183), ("cmn_dust_metal", 0xE28CDEB4),
    ("cmn_dust_snow", 0x14843A95), ("cmn_sweat_texa", 0x6735C95E), ("cmn_dust_cemen", 0xAA46A62B),
    ("cmn_dust_matte", 0x68772B54), ("cmn_dirty", 0x83611379), ("cmn_charge_pb", 0x98B892B6),
    ("cmn_hit", 0x3F1897D7),
]
EFFETS_PS4 = [("cmn_cos_break", 0x67A59705, 214), ("cmn_cos_transf", 0x3941725C, 222)]

MODELES_PS4 = range(94, 100)
TEXTURES = {ancien: 214 + ancien - 222 for ancien in range(222, 233)}


def empreinte(data):
    return hashlib.sha256(data).hexdigest()


def empreinte_fichier(chemin):
    with Path(chemin).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def controler(libelle, data, taille, sha):
    if len(data) != taille or empreinte(data) != sha:
        raise ValueError(libelle + ": size or SHA-256 mismatch")
    return data


def deja_modifie(racines):
    for racine in racines:
        for chemin in Path(racine).rglob("common.lnk"):
            if chemin.is_file() and chemin.stat().st_size == TAILLE_SORTIE and empreinte_fichier(chemin) == SHA_SORTIE:
                return True
    return False


def sections_par_nom(data):
    from convert_structures import children
    offsets = sorted(set(o for o in children(data) if o))
    return {data[o:o + 8].rstrip(b"\0").decode(): data[o:z]
            for o, z in zip(offsets, offsets[1:] + [len(data)])}


def enregistrements(section):
    from convert_structures import children
    offsets = children(section)
    return [section[a:z] for a, z in zip(offsets, offsets[1:] + [len(section)])]


def ajouter_enregistrements(original, ajouts, secondaire=None):
    from convert_structures import children, u, put
    out = bytearray(original)
    offsets = children(original)
    for item in ajouts:
        out.extend(bytes((-len(out)) % 16))
        offsets.append(len(out))
        out.extend(item)
    out.extend(bytes((-len(out)) % 16))
    table = len(out)
    out.extend(struct.pack("<" + "I" * len(offsets), *offsets))
    put(out, 32, table)
    put(out, 20, len(offsets))
    put(out, 24, len(offsets))
    if secondaire is not None:
        ancien_nombre = u(original, 24)
        ancienne_table = u(original, 40)
        entete = bytearray(original[ancienne_table:ancienne_table + 16])
        valeurs = [u(original, ancienne_table + 16 + i * 4) for i in range(ancien_nombre)] + secondaire
        out.extend(bytes((-len(out)) % 16))
        put(out, 40, len(out))
        put(entete, 0, len(valeurs))
        out.extend(entete)
        out.extend(struct.pack("<" + "I" * len(valeurs), *valeurs))
    out.extend(bytes((-len(out)) % 16))
    put(out, 16, len(out))
    return out


def groupes_epm(data):
    from convert_structures import u
    if data[:4] != b"EPM1":
        raise ValueError("EPM1: unexpected section")
    nombre, table, corps = (u(data, n) for n in (0x10, 0x20, 0x24))
    groupes = []
    for slot in range(nombre):
        sorte, index, index_cle, _ = struct.unpack_from("<4I", data, table + slot * 16)
        if sorte != 0x80000000:
            continue
        debut = u(data, corps + index * 4)
        nom = data[debut:debut + 14].split(b"\0", 1)[0].decode("ascii")
        pos = debut + 32
        for _ in range(u(data, debut + 20)):
            pos += u(data, pos + 12)
        pos_cle = table + index_cle * 4
        cle = bytes(data[pos_cle:pos_cle + (u(data, pos_cle) >> 24) * 4])
        groupes.append(dict(nom=nom, slot=slot, offset=debut, taille=pos - debut, cle=cle))
    return groupes


def construire_epm(pc, ps):
    from convert_structures import u
    anciens, nouveaux = groupes_epm(pc), groupes_epm(ps)
    if [g["nom"] for g in anciens] != [n for n, _ in HACHAGES_PC]:
        raise ValueError("EPM1: unexpected PC groups")
    items = [dict(cle=g["cle"], hachage=h, donnees=pc[g["offset"]:g["offset"] + g["taille"]])
             for g, (_, h) in zip(anciens, HACHAGES_PC)]
    for nom, hachage, premiere_texture in EFFETS_PS4:
        g = next(g for g in nouveaux if g["nom"] == nom)
        donnees = bytearray(ps[g["offset"]:g["offset"] + g["taille"]])
        struct.pack_into("<I", donnees, 28, premiere_texture)
        items.append(dict(cle=g["cle"], hachage=hachage, donnees=donnees))
    nombre = len(items)
    seaux = {}
    for rang, item in enumerate(items):
        seaux.setdefault(item["hachage"] % nombre, []).append(rang)
    libres = iter(i for i in range(nombre) if i not in seaux)
    slots, chaines = {}, {}
    for seau, rangs in sorted(seaux.items()):
        chaine = [seau] + [next(libres) for _ in rangs[1:]]
        for rang, slot in zip(rangs, chaine):
            slots[rang] = slot
        chaines[seau] = chaine
    table = 0x30
    out = bytearray(pc[:table])
    out.extend(bytes(nombre * 16))
    cles = {}
    for rang, item in enumerate(items):
        cles[rang] = (len(out) - table) // 4
        out.extend(item["cle"])
    out.extend(bytes((-len(out)) % 16))
    corps = len(out)
    out.extend(bytes(nombre * 8))
    for rang, item in enumerate(items):
        out.extend(bytes((-len(out)) % 16))
        position = len(out)
        out.extend(item["donnees"])
        struct.pack_into("<2I", out, corps + rang * 8, position, len(item["donnees"]))
        chaine = chaines[item["hachage"] % nombre]
        place = chaine.index(slots[rang])
        lien = chaine[place + 1] if place + 1 < len(chaine) else 0x7FFFFFFF
        if place == 0:
            lien |= 0x80000000
        struct.pack_into("<4I", out, table + slots[rang] * 16, 0x80000000, rang * 2, cles[rang], lien)
    out.extend(bytes((-len(out)) % 16))
    for offset, valeur in ((12, len(out)), (16, nombre), (28, nombre * 2), (32, table), (36, corps)):
        struct.pack_into("<I", out, offset, valeur)
    refaits = {g["cle"]: g for g in groupes_epm(out)}
    for g in anciens:
        r = refaits[g["cle"]]
        if pc[g["offset"]:g["offset"] + g["taille"]] != out[r["offset"]:r["offset"] + r["taille"]]:
            raise ValueError("EPM1: PC group changed")
    if u(out, 16) != 30:
        raise ValueError("EPM1: 30 groups expected")
    return out


def construire_commun(pc, pc_externe, ps, ps_externe):
    from convert_structures import children, u, put, convert_section, objgeo, convert_buffer
    from convert_textures import gnf_to_dds
    p, q = sections_par_nom(pc), sections_par_nom(ps)
    changees = {"EPM1": construire_epm(p["EPM1"], q["EPM1"])}

    geometrie = []
    for index in MODELES_PS4:
        obj = bytearray(objgeo(enregistrements(q["MdlGeo"])[index]))
        for maillage in children(obj):
            for j in range(u(obj, maillage + 12)):
                materiau = maillage + u(obj, maillage + 16 + 4 * j)
                slot = u(obj, materiau + 8)
                if slot not in TEXTURES:
                    raise ValueError("PS4 effect texture: unexpected slot")
                put(obj, materiau + 8, TEXTURES[slot])
        geometrie.append(obj)
    changees["MdlGeo"] = ajouter_enregistrements(p["MdlGeo"], geometrie)
    for nom in ("NodeLay", "MdlInfo", "HieLay", "GlblMtx", "BnOfsMtx"):
        converti = convert_section(q[nom])
        ajouts = [enregistrements(converti)[i] for i in MODELES_PS4]
        secondaire = None
        if nom == "HieLay":
            secondaire = [u(converti, u(converti, 40) + 16 + i * 4) for i in MODELES_PS4]
        changees[nom] = ajouter_enregistrements(p[nom], ajouts, secondaire)

    ps_lh = q["LHeader"]
    bases = children(ps_lh)
    for rang, nom in ((1, "VtxLay"), (2, "IdxLay")):
        debut = bases[rang]
        taille = u(ps_lh, u(ps_lh, 36) + 4 * rang)
        converti = convert_buffer(q[nom], ps_externe[debut:debut + taille])
        out = bytearray(p[nom])
        offsets = children(out)
        tailles = [u(out, u(out, 36) + i * 4) for i in range(len(offsets))]
        if len(offsets) != 100:
            raise ValueError(nom + ": 100 PC buffers expected")
        for i in range(100, 106):
            debut = children(converti)[i]
            taille = u(converti, u(converti, 36) + i * 4)
            out.extend(bytes((-len(out)) % 16))
            offsets.append(len(out))
            tailles.append(taille)
            out.extend(converti[debut:debut + taille])
        out.extend(bytes((-len(out)) % 16))
        put(out, 32, len(out))
        out.extend(struct.pack("<106I", *offsets))
        out.extend(bytes((-len(out)) % 16))
        put(out, 36, len(out))
        out.extend(struct.pack("<106I", *tailles))
        out.extend(bytes((-len(out)) % 16))
        put(out, 20, 106)
        put(out, 24, 106)
        put(out, 16, len(out))
        changees[nom] = out

    ps_td = q["TTGM"]
    ps_entetes = ps_td[48:u(ps_td, 40)]
    ps_index = ps_td[u(ps_td, 40):]
    ps_base = bases[0]
    ps_offsets = children(ps_index)
    td = p["TTDM"]
    entetes = td[48:u(td, 40)]
    index_td = td[u(td, 40):]
    nouveaux_entetes = []
    for slot in range(222, 233):
        entete = bytearray(enregistrements(ps_entetes)[slot][:16])
        if u(entete, 0) != 1 or u(entete, 4) != slot:
            raise ValueError("PS4 texture header: unexpected")
        put(entete, 4, TEXTURES[slot])
        nouveaux_entetes.append(entete)
    entetes_td = ajouter_enregistrements(entetes, nouveaux_entetes)
    externe = bytearray(pc_externe)
    base = children(p["LHeader"])[0]
    offsets = children(index_td)
    tailles = [u(index_td, u(index_td, 36) + i * 4) for i in range(len(offsets))]
    if len(offsets) != 214:
        raise ValueError("PC textures: 214 expected")
    for slot in range(222, 233):
        debut = ps_base + ps_offsets[slot]
        taille = u(ps_index, u(ps_index, 36) + 4 * slot)
        dds = gnf_to_dds(ps_externe[debut:debut + taille])
        externe.extend(bytes((-len(externe)) % 64))
        offsets.append(len(externe) - base)
        tailles.append(len(dds))
        externe.extend(dds)
    externe.extend(bytes((-len(externe)) % 128))
    put(externe, 4, len(externe))
    put(externe, base + 4, len(externe) - base)
    nouvel_index = bytearray(index_td)
    nouvel_index.extend(bytes((-len(nouvel_index)) % 16))
    put(nouvel_index, 32, len(nouvel_index))
    nouvel_index.extend(struct.pack("<225I", *offsets))
    nouvel_index.extend(bytes((-len(nouvel_index)) % 16))
    put(nouvel_index, 36, len(nouvel_index))
    nouvel_index.extend(struct.pack("<225I", *tailles))
    nouvel_index.extend(bytes((-len(nouvel_index)) % 16))
    put(nouvel_index, 20, 225)
    put(nouvel_index, 24, 225)
    put(nouvel_index, 64, 225)
    put(nouvel_index, 68, len(externe) - base)
    put(nouvel_index, 16, len(nouvel_index))
    nouveau_td = bytearray(td[:48])
    put(nouveau_td, 40, 48 + len(entetes_td))
    nouveau_td.extend(entetes_td)
    nouveau_td.extend(nouvel_index)
    put(nouveau_td, 16, len(nouveau_td))
    changees["TTDM"] = nouveau_td
    lh = bytearray(p["LHeader"])
    put(lh, 68, len(externe))
    put(lh, 144, len(externe) - base)
    changees["LHeader"] = lh

    a, b = p["MtrCol"], q["MtrCol"]
    ap, bp = children(a), children(b)
    if len(ap) != 3 or len(bp) != 3:
        raise ValueError("MtrCol: unexpected")
    for index in (0, 2):
        x = a[ap[index]:ap[index] + (416 if index == 0 else 240)]
        if x != b[bp[index]:bp[index] + len(x)]:
            raise ValueError("MtrCol: PC and PS4 differ")
    x = bytearray(a[ap[1]:ap[2]])
    y = bytearray(b[bp[1]:bp[2]])
    if u(x, 0xD4) != 67 or u(y, 0xD4) != 73:
        raise ValueError("MtrCol: unexpected counts")
    put(y, 0xD4, 67)
    if x != y[:len(x)] or [struct.unpack_from("<II", y, j) for j in range(len(x), len(y), 8)] != \
            [(i, 1) for i in MODELES_PS4]:
        raise ValueError("MtrCol: unexpected PS4 data")
    changees["MtrCol"] = b

    racine = children(pc)
    sortie = bytearray(pc[:min(o for o in racine if o)])
    for i, off in enumerate(racine):
        if not off:
            continue
        nom = pc[off:off + 8].rstrip(b"\0").decode()
        sortie.extend(bytes((-len(sortie)) % 16))
        put(sortie, u(sortie, 32) + i * 4, len(sortie))
        sortie.extend(changees.get(nom, p[nom]))
    put(sortie, 16, len(sortie))
    return bytes(sortie), bytes(externe)


def masque_pc(taille, longueur):
    import numpy as np
    from player_key import require_key
    phrase = require_key()
    valeur = ((taille + 999) * 7 // 11) + (taille % 17) + 428
    cle = bytes((valeur >> s) & 255 for s in (24, 16, 8, 0) if (valeur >> s) & 255)
    periode = bytes(v ^ cle[i % len(cle)] for i, v in enumerate(phrase))
    return np.resize(np.frombuffer(periode, dtype=np.uint8), longueur)


def encoder_pc(donnees):
    import numpy as np
    out = bytearray()
    vue = memoryview(donnees)
    while len(vue):
        morceau = vue[:0x4000]
        deflate = zlib.compressobj(-1, zlib.DEFLATED, -zlib.MAX_WBITS)
        bloc = b"\x78\x9c" + deflate.compress(morceau) + deflate.flush() + struct.pack(">I", zlib.adler32(morceau))
        out.extend(struct.pack("<I", len(bloc) | 0x8000))
        out.extend(bloc)
        out.extend(bytes((-len(out)) % 16))
        vue = vue[0x4000:]
    arr = np.frombuffer(out, dtype=np.uint8)
    masque = masque_pc(len(donnees), len(arr))
    np.bitwise_xor(arr, masque, out=arr, where=(arr != 0) & (arr != masque))
    return struct.pack("<I", len(donnees)) + bytes(out)


def construire(archive_pc, bruts_pc, ps4, dossier):
    from v2_alternates import decode_pc
    archive_pc = Path(archive_pc)
    if archive_pc.stat().st_size != TAILLE_PC or empreinte_fichier(archive_pc) != SHA_PC:
        raise ValueError("PC common.lnk is not the original Steam file (verify game files in Steam)")
    pc = controler("PC TMC", decode_pc(bruts_pc[0]), *TMC_PC[2:])
    pc_externe = controler("PC TMCL", decode_pc(bruts_pc[1]), *TMCL_PC[2:])
    ps = controler("PS4 TMC", ps4[0], *TMC_PS4)
    ps_externe = controler("PS4 TMCL", ps4[1], *TMCL_PS4)
    tmc, tmcl = construire_commun(pc, pc_externe, ps, ps_externe)
    paquets = []
    for ext, donnees in (("TMC", tmc), ("TMCL", tmcl)):
        taille, sha, taille_paquet, sha_paquet = ETAPES[ext]
        controler(ext + " rebuilt", donnees, taille, sha)
        paquet = encoder_pc(donnees)
        if len(paquet) != taille_paquet or empreinte(paquet) != sha_paquet:
            raise ValueError(ext + ": zlib " + zlib.ZLIB_RUNTIME_VERSION + " output differs from the reference")
        paquets.append(paquet)
    dossier = Path(dossier)
    dossier.mkdir(parents=True, exist_ok=True)
    sortie = dossier / "common.lnk"
    temp = dossier / "common.lnk.tmp"
    try:
        shutil.copyfile(archive_pc, temp)
        with temp.open("r+b") as f:
            for (index, taille_brute, *_), paquet in zip((TMC_PC, TMCL_PC), paquets):
                f.seek(32 + index * 32)
                if struct.unpack("<4Q", f.read(32))[1:] != (taille_brute, taille_brute, 0):
                    raise ValueError("PC common.lnk: unexpected archive record")
                fin = f.seek(0, os.SEEK_END)
                f.write(bytes((-fin) % 2048))
                offset = f.tell()
                f.write(paquet)
                f.write(bytes((-f.tell()) % 2048))
                f.seek(32 + index * 32)
                f.write(struct.pack("<4Q", offset, len(paquet), len(paquet), 0))
            total = f.seek(0, os.SEEK_END)
            f.seek(16)
            f.write(struct.pack("<Q", total))
            f.flush()
            f.seek(0)
            resultat = hashlib.file_digest(f, "sha256").hexdigest()
        if total != TAILLE_SORTIE or resultat != SHA_SORTIE:
            raise ValueError("common.lnk differs from the reference")
        os.replace(temp, sortie)
    finally:
        if temp.exists():
            temp.unlink()
    return sortie
