import struct
from fractions import Fraction

from x64emu import Controleur, Natif, OBJ, NODE_BASE, NODE_SZ

u32 = lambda b, o: struct.unpack_from('<I', b, o)[0]
u64 = lambda b, o: struct.unpack_from('<Q', b, o)[0]


def _subs(x, base=0):
    n = u32(x, base + 0x14); to = u32(x, base + 0x20); so = u32(x, base + 0x24)
    return [(base + u32(x, base + to + 4 * i), u32(x, base + so + 4 * i)) for i in range(n)]


def _projet(x):
    r1 = _subs(x)[1][0]; ss = _subs(x, r1)
    return ss[0][0], ss[1][0]


def aot(ps4, pc):
    co, so = _projet(ps4); pco, pso = _projet(pc)
    S0, NS, C0, NC, PCS0 = 0x2E8F, 44, 0x1875, 28, 0x2F5B
    ROW = [0x1878, 0x1879, 0x187C, 0x187D, 0x187E, 0x187F, 0x1880, 0x1881, 0x1882, 0x1883, 0x1884, 0x1885, 0x1886,
           0x188C, 0x1887, 0x1888, 0x188A, 0x188B, 0x188D, 0x1889, 0x188E, 0x188F, 0x1890]
    sons = bytearray()
    for k in range(NS):
        e = ps4[so + u64(ps4, so + 16 * (S0 + k)):][:0x30]
        sid, bank, smp = struct.unpack_from('<HHH', e, 0x22)
        c = pc[pso + u32(pc, pso + 8 * (PCS0 + k)):][:0x28]
        pid, pb = struct.unpack_from('<HH', c, 0x1A)
        if sid != S0 + k or bank != 149 or pid != PCS0 + k or pb != 144:
            raise ValueError('AoT sound project: unexpected sound row')
        sons += e[4:0x1C] + struct.pack('<H', smp) + e[0x28:0x2E]
    defs = []
    for k in range(NC):
        i = C0 + k; a, f = struct.unpack_from('<QQ', ps4, co + 16 * i)
        if f != 76:
            raise ValueError('AoT sound project: unexpected cue group')
        d = bytearray(ps4[co + a:co + a + 0x80]); n = 0; j = 0
        while j < len(d):
            if d[j] == 0 and j + 4 < len(d) and S0 <= (d[j + 1] << 8 | d[j + 2]) < S0 + NS and d[j + 3] == 0 and d[j + 4] == 0:
                v = PCS0 + ((d[j + 1] << 8 | d[j + 2]) - S0); d[j + 1] = v >> 8; d[j + 2] = v & 0xFF; n += 1; j += 5; continue
            j += 1
        nxt = u64(ps4, co + 16 * (i + 1)); ln = nxt - a if 0 < nxt - a < 0x80 else d.index(0x1E) + 1
        if not n:
            raise ValueError('AoT sound project: cue without sound')
        defs.append(bytes(d[:ln]))
    offs = [sum(len(x) for x in defs[:k]) for k in range(NC)]
    return {'AoTSounds': bytes(sons), 'AoTCueBlob': b''.join(defs), 'AoTCueOff': struct.pack('<%dH' % NC, *offs),
            'AoTRow': bytes(c - C0 for c in ROW)}


CONSOLE = (('Titan', 49, 149, 76), ('Azuchi1', 47, 147, 78), ('Fireworks', 46, 146, 102), ('Azuchi2', 48, 148, 79))


def _lignes_stage(image, stage, complet=True):
    lignes = {}
    for at in range(0x570000, 0x578000 - 10):
        if image[at:at + 2] != b'\xc7\x80':
            continue
        off, val = struct.unpack_from('<II', image, at + 2); st, rem = divmod(off, 512)
        if st == stage and rem % 4 == 0 and 0x1800 <= val < 0x1B00:
            slot = rem // 4
            if lignes.get(slot, val) != val:
                raise ValueError('eboot: conflicting stage sound row')
            lignes[slot] = val
    if not complet:
        return lignes
    if sorted(lignes) != list(range(len(lignes))):
        raise ValueError('eboot: incomplete stage sound row')
    return [lignes[s] for s in range(len(lignes))]


def console_sons(ps4, pc, image, chute):
    co, so = _projet(ps4); pco, pso = _projet(pc); PS = 0x2F5B
    out = {}
    for nom, stage, banque, groupe in CONSOLE:
        ids = [i for i in range(0x2E00, 0x3400) if so + u64(ps4, so + 16 * i) + 48 <= len(ps4)
               and struct.unpack_from('<HH', ps4, so + u64(ps4, so + 16 * i) + 0x22) == (i, banque)]
        cues = [i for i in range(0x1800, 0x1B00) if u64(ps4, co + 16 * i + 8) == groupe]
        S0, NS, C0, NC = ids[0], len(ids), cues[0], len(cues)
        if ids != list(range(S0, S0 + NS)) or cues != list(range(C0, C0 + NC)):
            raise ValueError('Sound project: unexpected bank layout')
        sons = bytearray()
        for k in range(NS):
            e = ps4[so + u64(ps4, so + 16 * (S0 + k)):][:48]
            d = pso + u32(pc, pso + 8 * (PS + k))
            if struct.unpack_from('<HH', pc, d + 26) != (PS + k, 144):
                raise ValueError('PC sound project: unexpected donor sound')
            sons += e[4:28] + e[38:40] + e[40:46]
        defs = []
        for k in range(NC):
            a = u64(ps4, co + 16 * (C0 + k)); nxt = u64(ps4, co + 16 * (C0 + k + 1))
            if not 0 < nxt - a <= 1024:
                raise ValueError('Sound project: unexpected cue')
            d = bytearray(ps4[co + a:co + nxt]); j = 0; refs = 0
            while j + 4 < len(d):
                v = d[j + 1] * 256 + d[j + 2]
                if d[j] == 0 and d[j + 3:j + 5] == b'\0\0' and S0 <= v < S0 + NS:
                    m = PS + v - S0; d[j + 1] = m >> 8; d[j + 2] = m & 255; j += 5; refs += 1
                else:
                    j += 1
            if not refs or 0x1E not in d:
                raise ValueError('Sound project: cue without sound')
            defs.append(bytes(d))
        ligne = [c - C0 for c in _lignes_stage(image, stage)]
        if not all(0 <= v < NC for v in ligne):
            raise ValueError('eboot: stage sound row outside the cue group')
        if nom == 'Azuchi2':
            out['Az2Sounds'] = bytes(sons); out['Az2CueBlob'] = b''.join(defs); out['Az2SoundRow'] = bytes(ligne)
            out['Az2CueOff'] = struct.pack('<%dH' % NC, *[sum(map(len, defs[:i])) for i in range(NC)])
        if nom in chute:
            cue = _lignes_stage(image, 37, complet=False)[1]
            a = u64(ps4, co + 16 * cue); d = bytearray(ps4[co + a:co + u64(ps4, co + 16 * (cue + 1))])
            if len(d) != 6 or d[0] or d[3:] != b'\0\0\x1e':
                raise ValueError('Sound project: unexpected fall cue')
            sid = d[1] << 8 | d[2]; e = ps4[so + u64(ps4, so + 16 * sid):][:48]
            if struct.unpack_from('<HHH', e, 0x22) != (sid, 137, 4):
                raise ValueError('Sound project: unexpected fall sound')
            sons += e[4:28] + struct.pack('<H', chute[nom]) + e[40:46]
            m = PS + NS; d[1] = m >> 8; d[2] = m & 255; defs.append(bytes(d)); NC += 1
        offs = [sum(map(len, defs[:i])) for i in range(NC)]
        out['Console%sSounds' % nom] = bytes(sons); out['Console%sBlob' % nom] = b''.join(defs)
        out['Console%sOffsets' % nom] = struct.pack('<%dH' % NC, *offs); out['Console%sRow' % nom] = bytes(ligne)
    return out


def _f32_texte(v):
    return struct.pack('<f', float('%.6f' % v))


def console_positions(ps4):
    co, so = _projet(ps4); out = {}
    for nom, stage, banque, groupe in CONSOLE:
        ids = [i for i in range(0x2E00, 0x3400) if so + u64(ps4, so + 16 * i) + 48 <= len(ps4)
               and struct.unpack_from('<HH', ps4, so + u64(ps4, so + 16 * i) + 0x22) == (i, banque)]
        lignes = bytearray()
        for k, i in enumerate(ids):
            e = ps4[so + u64(ps4, so + 16 * i):][:48]; ptr = u64(e, 0)
            if not ptr:
                continue
            n = e[43]
            if not 1 <= n <= 8:
                raise ValueError('Sound project: unexpected sound position count')
            pts = b''.join(_f32_texte(v) for i2 in range(n) for v in struct.unpack_from('<3f', ps4, so + ptr + 0x20 * i2))
            lignes += bytes((k, n, 0, 0)) + pts + bytes(96 - len(pts))
        out['Console%sPositions' % nom] = bytes(lignes) or bytes((255, 0)) + bytes(98)
    return out


def fireworks_ambiance(ps4):
    co, so = _projet(ps4); B, taille = _subs(ps4, _subs(ps4)[1][0])[3]; fin = min(u64(ps4, B + 8), taille)
    C0 = min(i for i in range(0x1800, 0x1B00) if u64(ps4, co + 16 * i + 8) == 102)
    trouvees = []
    for at in range(B + 0x10, B + fin, 16):
        off, n = struct.unpack_from('<QQ', ps4, at)
        if off + 16 * n > taille:
            continue
        rows = [struct.unpack_from('<IIIf', ps4, B + off + 16 * k) for k in range(n)]
        if rows and all(r[2] != 0xFFFFFFFF and u64(ps4, co + 16 * r[2] + 8) == 102 for r in rows):
            trouvees.append((off, n))
    if len(trouvees) != 1:
        raise ValueError('Sound project: Fireworks ambience not found')
    off, n = trouvees[0]; out = bytearray()
    for k in range(n):
        m, nd, c = struct.unpack_from('<III', ps4, B + off + 16 * k)
        out += struct.pack('<III', m, nd, c - C0 + 0x190F) + ps4[B + off + 16 * k + 12:B + off + 16 * k + 16]
    return {'FireworksAmbientSoundRows': bytes(out)}


def cameras_pb(image):
    return {'TitanPowerBlowCameraRecipe': image[0xF9F9F0:0xF9F9F0 + 68],
            'FireworksPowerBlowCameraRecipe': image[0xF9F9A0:0xF9F9A0 + 68]}


def _noms(chaines):
    return bytes(4 * len(chaines)), {4 * i: s for i, s in enumerate(chaines)}


def _chaine(b, o):
    return b[o:b.index(b'\0', o)]


def eboot_direct(image):
    titan = [_chaine(image, u64(image, 0x1B68590 + 8 * i)) for i in range(76)]
    return {'Az2RoofAnchors': image[0xFC1D30:0xFC1D30 + 64], 'Az2CourtAnchors': image[0xFC1CD0:0xFC1CD0 + 96],
            'TitanNames': _noms(titan)}


LIGNES_PC = {'TITAN': (49, 28), 'AZUCHI1': (47, 23), 'FIREWORKS': (46, 18), 'AZUCHI2': (48, 32)}


def _phybind(pb):
    recs = []
    for o in range(0, len(pb) - 19, 20):
        first, nom, flags, son, last = struct.unpack_from('<5I', pb, o)
        if not nom:
            break
        recs.append((first, _chaine(pb, nom), flags, son, last))
    return recs


def liaisons(phybind):
    out = {}
    for st, pb in phybind.items():
        ps4, pc = LIGNES_PC[st]; specs = bytearray(); recs = bytearray(); ps = {}; pr = {}
        for k, (first, nom, flags, son, last) in enumerate(_phybind(pb)):
            if son >> 16 != 0x4000 | ps4:
                raise ValueError('PHYBIND: unexpected sound line')
            specs += struct.pack('<5I', 0, first, flags, son, last); ps[20 * k] = nom
            recs += struct.pack('<5I', first, 0, flags, (0x4000 | pc) << 16 | son & 0xFFFF, last); pr[20 * k + 4] = nom
        out['Console%sBindingSpecs' % st] = (bytes(specs), ps); out['Console%sBindingRecords' % st] = (bytes(recs), pr)
    out['Az2PhysicalSoundSpecs'] = out['ConsoleAZUCHI2BindingSpecs']; out['Az2PhysicalSoundRecords'] = out['ConsoleAZUCHI2BindingRecords']
    out['TitanPhysicalSoundSpecs'] = out['ConsoleTITANBindingSpecs']
    return out


KIND = {'SHOW': 1, 'COLL': 2, 'SLEEP': 3, 'WAKE': 4, 'FLAGS': 5, 'CLEAR': 6, 'ENABLE': 7, 'ANIM': 8}
HOTES = (('TITAN', 0x0BD40001, 0x0BD4004D, 0x767AE0, 0x762E20), ('AZUCHI1', 0x0E830001, 0x0E83003B, 0x79ED30, 0x79A620),
         ('FIREWORKS', 0x43820001, 0x43820072, 0x834200, 0x82EF40))


def _hote_ops(log, n):
    out = bytearray()
    for op, a in log:
        if op not in KIND:
            continue
        if op == 'SHOW': node, x, y, lo, hi, f = a[0], a[1], 0, 0, 0, 0.0
        elif op == 'COLL': node, x, y, lo, hi, f = a[1], a[2], 0, 0, 0, 0.0
        elif op in ('SLEEP', 'WAKE'): node, x, y, lo, hi, f = a[0], 0, 0, 0, 0, 0.0
        elif op in ('FLAGS', 'CLEAR'): node, x, y, lo, hi, f = a[1], 0, 0, a[2] & 0xFFFFFFFF, a[2] >> 32, 0.0
        elif op == 'ENABLE': node, x, y, lo, hi, f = a[0], a[1] & 0xFF, 0, 0, 0, 0.0
        else: node, x, y, lo, hi, f = a[0], a[1] & 0xFF, a[2] & 0xFF, a[3] & 0xFFFFFFFF, 0, a[4]
        if not isinstance(node, int) or node <= 0 or node > n:
            continue
        out += struct.pack('<BBBBIIf', KIND[op], node, x & 0xFF, y & 0xFF, lo, hi, float(repr(float(f))))
    return bytes(out)


def hotes(image):
    res = {}
    for key, first, last, si, init in HOTES:
        n = last - first; e = Controleur(image, n + 8)
        e.appeler(si); e.log.clear()
        if not e.appeler(init, (OBJ,)):
            raise ValueError('eboot: %s controller init did not finish' % key)
        res[key + 'Ops'] = _hote_ops(e.log, n)
    return res


K7 = {'SHOW': 1, 'COLL': 2, 'SLEEP': 3, 'WAKE': 4, 'FLAGS': 5, 'CLEAR': 6, 'ENABLE': 7}
N_AZ2 = 0x0E8375BE - 0x0E837401


def _az2_ops(log):
    out = bytearray()
    for op, a in log:
        if op not in K7:
            continue
        node = a[1] if op in ('COLL', 'FLAGS', 'CLEAR') else a[0]
        if not isinstance(node, int) or not 1 <= node <= 445:
            continue
        v = a[2] if op in ('COLL', 'FLAGS', 'CLEAR') else a[1] if op in ('SHOW', 'ENABLE') else 0
        out += struct.pack('<HBxII', node, K7[op], v & 0xFFFFFFFF, v >> 32)
    return bytes(out)


def _az2(image):
    e = Controleur(image, N_AZ2 + 8); e.appeler(0x774DF0); e.log.clear()
    if not e.appeler(0x76FD30, (OBJ,)):
        raise ValueError('eboot: Azuchi 2 init did not finish')
    return e


def azuchi2(image):
    res = {'Az2Ops': _az2_ops(_az2(image).log)}
    e = _az2(image); e.log.clear()
    e.ecrire(OBJ + 0x420, struct.pack('<I', 1)); e.ecrire(OBJ + 0x434, struct.pack('<I', 2))
    if not e.appeler(0x771410, (OBJ,)):
        raise ValueError('eboot: Azuchi 2 courtyard did not finish')
    res['Az2CourtyardOps'] = _az2_ops(e.log)
    e = _az2(image); e.log.clear()
    e.ecrire(OBJ + 0x280, struct.pack('<Q', OBJ + 0x3000)); e.ecrire(OBJ + 0x420, struct.pack('<I', 0))
    e.ecrire(OBJ + 0x434, struct.pack('<I', 1)); e.ecrire(OBJ + 0x2D3, bytes((i * 47) & 255 for i in range(0x415 - 0x2D3)))
    if not e.appeler(0x771410, (OBJ,)):
        raise ValueError('eboot: Azuchi 2 roof replay did not finish')
    res['Az2RoofSceneOps'] = _az2_ops(e.log)
    return res


def azuchi1_casse(image):
    e0 = Controleur(image, 66); e0.appeler(0x79ED30)
    ids = [struct.unpack('<I', e0.lire(0x23B90A0 + 4 * k, 4))[0] for k in range(8)] + \
          [struct.unpack('<I', e0.lire(a, 4))[0] for a in (0x23B90C0, 0x23B90C8, 0x23B90D0)]
    ops = bytearray(); casses = bytearray()
    for i in ids:
        e = Controleur(image, 66); e.appeler(0x79ED30); e.appeler(0x79A620, (OBJ,)); e.log.clear()
        if not e.appeler(0x79C000, (OBJ, i)):
            raise ValueError('eboot: Azuchi 1 break did not finish')
        premier = len(ops) // 3; son = None
        for op, a in e.log:
            if op == 'SHOW': ops += bytes((1, a[0], a[1] & 0xFF))
            elif op == 'COLL': ops += bytes((2, a[1], a[2] & 0xFF))
            elif op == 'SLEEP': ops += bytes((3, a[0], 0))
            elif op == 'WAKE': ops += bytes((4, a[0], 0))
            elif op == 'SOUNDFX': son = int(a[0], 16)
        if son is None:
            raise ValueError('eboot: Azuchi 1 break without sound')
        casses += struct.pack('<IBBxxI', i & 0x3FF, premier, len(ops) // 3 - premier, son)
    return {'AzOps': bytes(ops), 'AzBreaks': bytes(casses)}


def titan_placement(image):
    n = Natif(image, {0xFE400: lambda mc: mc.r['rdi'] & 0xFFFFFFFF})
    for i in range(3):
        n.mc.m.ecrire(0x1CA0C58 + 8 * i, struct.pack('<Q', 0x1001 + i))
    n.mc.m.ecrire(0x16D82F, b'\xc3')
    n.appeler(0x16D383)
    return {'TitanAncillaryPlacementTableResearch': n.mc.m.lire(0x1CA09A0, 11 * 0x38)}


REMPLACEES = (0xFE400, 0x2B7450, 0x3A7F50, 0x463AE0, 0x463B30, 0x464600, 0x4647B0, 0x9068D0, 0x906A90, 0x906B20, 0x906C40,
              0x90AB20, 0x90AEE0, 0x90AF70, 0x90EC20, 0x91AE20, 0x91E860, 0x91E8B0, 0x91F7B0, 0x920170, 0x9201C0, 0x920300,
              0x9213E0, 0x9274C0, 0x97D0F0, 0xDE2650, 0xAA0, 0x91C9B0, 0xF90030, 0xF90040, 0xF90340, 0xF90350, 0x909550)
OISEAUX = (419, 421, 422, 423, 424, 425, 426)


def oiseaux(image):
    appels = []; etat = {'fe400': 0}

    def noeud(v):
        return (v - NODE_BASE) // NODE_SZ if NODE_BASE <= v < NODE_BASE + 453 * NODE_SZ else 0

    def matrice(mc):
        appels.append((0x91F7B0, noeud(mc.r['rdi']), mc.m.lire(mc.r['rsi'], 64)))

    def cadre(mc):
        appels.append((0x906B20, noeud(mc.r['rdi']), (mc.r['rsi'], mc.r['rdx'], mc.r['rcx']), bytes(mc.y[0][:4])))

    r = {a: (lambda mc: 0) for a in REMPLACEES}
    r[0xFE400] = lambda mc: mc.r['rdi'] & 0xFFFFFFFF if etat['fe400'] else 0
    r[0x91F7B0] = matrice; r[0x906B20] = cadre
    n = Natif(image, r); m = n.mc.m
    obj, tableau, garde = 0x30000000, 0x31000000, 0x32000000
    m.ecrire(0x1C7E4E0, struct.pack('<Q', garde))
    m.ecrire(tableau, b''.join(struct.pack('<Q', NODE_BASE + i * NODE_SZ if i else 0) for i in range(453)))
    for i in range(1, 453):
        a = NODE_BASE + i * NODE_SZ; m.ecrire(a + 0x28, struct.pack('<Q', a + 0x800)); m.ecrire(a + 0x30, struct.pack('<Q', a + 0x900))
    m.ecrire(obj + 0x50, struct.pack('<Q', tableau)); m.ecrire(obj + 0x10, struct.pack('<Q', obj + 0x4000))
    m.ecrire(obj + 0x280, struct.pack('<Q', obj + 0x5000))
    n.appeler(0x774DF0, (obj,))
    etat['fe400'] = 1; appels.clear()
    n.appeler(0x76FD30, (obj,))
    b = [c for c in appels if c[1] in OISEAUX]
    if len(b) != 14:
        raise ValueError('eboot: unexpected Azuchi 2 bird initialisation')
    out = bytearray()
    for k, nd in enumerate(OISEAUX):
        mat, clip = b[2 * k], b[2 * k + 1]
        if mat[0] != 0x91F7B0 or clip[0] != 0x906B20 or mat[1] != nd or clip[1] != nd or clip[2] != (0, 0, 0):
            raise ValueError('eboot: unexpected Azuchi 2 bird call')
        out += struct.pack('<Hxx', nd) + mat[2] + clip[3]
    return {'Az2BirdPoses': bytes(out)}


U = lambda d, o: struct.unpack_from('<I', d, o)[0]
CHAMPS = {'04021c02': 'object_names', '2220b002': 'bindings', '40201005': 'translation', '40201004': 'quaternion',
          '84424604': 'geometry_blob'}


def _sdp(d, start):
    if d[start + 8:start + 12] != b'1PDS':
        raise ValueError('UNDD: SDP block expected')
    size, nf, _, nr, stride, fo, ro, _, _ = struct.unpack_from('<9I', d, start + 12)
    if not (0 < size <= len(d) - start and nf < 100 and nr < 100000):
        raise ValueError('UNDD: invalid SDP block')
    champs = []
    for i in range(nf):
        typ, col, ni, _ = struct.unpack_from('<4I', d, start + fo + i * 16)
        champs.append((CHAMPS.get(d[start + fo + ni * 4:start + fo + ni * 4 + 4].hex()), typ, col))
    lignes = []
    for ri in range(nr):
        rec = start + ro + ri * stride * 4; v = {}
        for nom, typ, col in champs:
            if nom is None:
                continue
            off = rec + col * 4; val = U(d, off)
            if typ & 0xFF000000 == 0x14000000:
                n = typ & 0xFFFFFF
                v[nom] = list(struct.unpack_from('<%df' % n, d, start + val)) if val else None
            elif typ in (0x80000000, 0x12000000):
                v[nom] = (start + val if val else None, U(d, off + 4))
            else:
                v[nom] = (start + val if val else None, None)
        lignes.append(v)
    return lignes


def _geometrie(d, start, longueur):
    if d[start:start + 4] != b'OC1G':
        raise ValueError('UNDD: geometry block expected')
    h = start + 0x10; tag = d[h:h + 8].rstrip(b'\0')
    if tag == b'HLP_3':
        nf, fo, nv = struct.unpack_from('<3I', d, h + 0x10); vo = struct.unpack_from('<H', d, h + 0x1C)[0]
    elif tag == b'HVB_2':
        nf, fo, nv, vo = struct.unpack_from('<4I', d, h + 0x18)
    else:
        raise ValueError('UNDD: unknown geometry')
    if h + vo + nv * 16 > start + longueur or h + fo + nf * 6 > start + longueur:
        raise ValueError('UNDD: geometry outside its block')
    sommets = [struct.unpack_from('<3f', d, h + vo + i * 16) for i in range(nv)]
    faces = [struct.unpack_from('<3H', d, h + fo + i * 6) for i in range(nf)]
    if any(a >= nv for f in faces for a in f):
        raise ValueError('UNDD: invalid triangle')
    return sommets, faces


def colliders(d):
    racine = _sdp(d, 0)[0]
    cols = _sdp(d, d.find(b'Cols\0\0\0\0'))
    noms0 = racine['object_names'][0]; liens = racine['bindings']; noms = {}
    for i in range(liens[1] // 12):
        ns, ci, _ = struct.unpack_from('<3I', d, liens[0] + i * 12)
        no = noms0 + ns; noms.setdefault(ci, []).append(d[no + 1:no + 1 + d[no]].decode('ascii'))
    out = []
    for ci, v in enumerate(cols):
        blob = v.get('geometry_blob')
        if blob and blob[0] and blob[1]:
            out.append((noms.get(ci, []), v.get('translation'), v.get('quaternion'), _geometrie(d, blob[0], blob[1])))
    return out


def _f32_decimal(texte):
    q = Fraction(texte)
    if q == 0:
        return struct.pack('<f', -0.0 if texte.startswith('-') else 0.0)
    a = struct.unpack('<f', struct.pack('<f', float(q)))[0]
    cands = sorted({a, *(struct.unpack('<f', struct.pack('<I', (struct.unpack('<I', struct.pack('<f', a))[0] + k) & 0xFFFFFFFF))[0]
                         for k in (-1, 1))}, key=lambda c: (abs(Fraction(c) - q), struct.unpack('<I', struct.pack('<f', c))[0] & 1))
    return struct.pack('<f', cands[0])


def _litteral(v):
    return format(v, '.9g')


def triangles(d):
    roof = bytearray(); cour = bytearray()
    for noms, tr, qu, (sommets, faces) in colliders(d):
        if noms == ['NML_azc2_kawara_floor_col']:
            if tr is not None or qu is not None:
                raise ValueError('UNDD: unexpected roof transform')
            for f in faces:
                roof += b''.join(_f32_decimal(_litteral(c)) for i in f for c in sommets[i])
        elif noms == ['NML_azc2_floor_col']:
            shift = tr or [0, 0, 0]; x, y, z, w = qu or [0, 0, 0, 1]

            def transforme(v):
                vx, vy, vz = v
                tx, ty, tz = 2 * (y * vz - z * vy), 2 * (z * vx - x * vz), 2 * (x * vy - y * vx)
                return [vx + w * tx + y * tz - z * ty + shift[0], vy + w * ty + z * tx - x * tz + shift[1],
                        vz + w * tz + x * ty - y * tx + shift[2]]
            vs = [transforme(v) for v in sommets]
            for f in faces:
                cour += b''.join(_f32_decimal(_litteral(c)) for i in f for c in vs[i])
    return {'Az2RoofTriangles': bytes(roof), 'Az2CourtyardTriangles': bytes(cour)}


ORDRE = [('AoTSounds', 32), ('AoTCueBlob', 1), ('AoTCueOff', 2), ('AoTRow', 1), ('Az2RoofAnchors', 8), ('Az2CourtAnchors', 8),
         ('Az2BirdPoses', 72), ('Az2Names', 4), ('Az2Ops', 12), ('Az2RoofTriangles', 36), ('Az2CourtyardOps', 12),
         ('Az2CourtyardTriangles', 36), ('Az2RoofSceneOps', 12), ('Az2PhysicalSoundSpecs', 20), ('Az2PhysicalSoundRecords', 20),
         ('Az2Sounds', 32), ('Az2CueBlob', 1), ('Az2CueOff', 2), ('Az2SoundRow', 1), ('AzOps', 3), ('AzBreaks', 12)]
for st in ('TITAN', 'AZUCHI1', 'FIREWORKS', 'AZUCHI2'):
    ORDRE += [('Console%sBindingSpecs' % st, 20), ('Console%sBindingRecords' % st, 20)]
for st in ('Titan', 'Azuchi1', 'Fireworks', 'Azuchi2'):
    ORDRE += [('Console%sSounds' % st, 32), ('Console%sBlob' % st, 1), ('Console%sOffsets' % st, 2), ('Console%sRow' % st, 1)]
ORDRE += [('Console%sPositions' % st, 100) for st in ('Titan', 'Azuchi1', 'Fireworks', 'Azuchi2')]
ORDRE += [('TITANNames', 4), ('TITANOps', 16), ('AZUCHI1Names', 4), ('AZUCHI1Ops', 16), ('FIREWORKSNames', 4), ('FIREWORKSOps', 16),
          ('TitanNames', 4), ('TitanAncillaryPlacementTableResearch', 1), ('TitanPhysicalSoundSpecs', 20),
          ('TitanPowerBlowCameraRecipe', 4), ('FireworksPowerBlowCameraRecipe', 4), ('FireworksAmbientSoundRows', 16)]


def ecrire(tables):
    blocs = []; reserve = bytearray(); pos = {}
    for nom, el in ORDRE:
        t = tables[nom]
        if isinstance(t, tuple):
            raw, ptr = t; raw = bytearray(raw)
            for o in sorted(ptr):
                s = ptr[o]
                if s not in pos:
                    pos[s] = len(reserve) + 1; reserve += s + b'\0'
                struct.pack_into('<I', raw, o, pos[s])
            blocs.append((nom, el, bytes(raw))); blocs.append((nom + '.ptr', 4, struct.pack('<%dI' % len(ptr), *sorted(ptr))))
        else:
            blocs.append((nom, el, t))
    blocs.append(('chaines', 1, bytes(reserve)))
    out = bytearray(b'D5SD' + struct.pack('<II', 1, len(blocs)))
    for nom, el, d in blocs:
        if len(d) % el:
            raise ValueError('D5SD: table %s has a partial element' % nom)
        n = nom.encode('ascii'); out += struct.pack('<H', len(n)) + n + struct.pack('<II', el, len(d)) + d
    return bytes(out)


NOMS_NOEUDS = (('TITANNames', 0x1B68590, 76), ('AZUCHI1Names', 0x1B6BE40, 58), ('FIREWORKSNames', 0x1B74630, 113),
               ('Az2Names', 0x1B69850, 445))


def toutes(image, ps4_son, pc_son, phybind, undd_az2, programmes):
    t = {}
    t.update(aot(ps4_son, pc_son)); t.update(console_sons(ps4_son, pc_son, image, programmes))
    t.update(console_positions(ps4_son)); t.update(fireworks_ambiance(ps4_son)); t.update(cameras_pb(image))
    t.update(eboot_direct(image)); t.update(liaisons(phybind))
    for nom, tab, n in NOMS_NOEUDS:
        t[nom] = _noms([_chaine(image, u64(image, tab + 8 * i)) for i in range(n)])
    for f in (hotes, azuchi2, azuchi1_casse, titan_placement, oiseaux):
        t.update(f(image))
    t.update(triangles(undd_az2))
    return t


def construire(eboot, son_ps4, son_pc, phybind, undd_az2, xws_azuchi1, xws_azuchi2):
    import softengine_lr
    import stage_audio
    from bindings import bindings
    image = softengine_lr.image(eboot)
    pb = {st: bytes(bindings(raw)) for st, raw in phybind.items()}
    programmes = {'Azuchi1': len(stage_audio.programmes(bytes(xws_azuchi1))[0]),
                  'Azuchi2': len(stage_audio.programmes(bytes(xws_azuchi2))[0])}
    return ecrire(toutes(image, bytes(son_ps4), bytes(son_pc), pb, bytes(undd_az2), programmes))
