import ctypes
import math
import struct
from fractions import Fraction

import numpy as np

f32 = np.float32
SORTIE = 32000
PHASES_MAX = 1024

u = lambda b, o: struct.unpack_from('<I', b, o)[0]
h = lambda b, o: struct.unpack_from('<H', b, o)[0]
aligner = lambda v, n: (v + n - 1) // n * n


def _i0(x):
    s, t, k, y = 1.0, 1.0, 1, x * x / 4.0
    while True:
        t *= y / (k * k)
        s += t
        if t < s * 1e-17:
            return s
        k += 1


def _fma32(a, b, c):
    r = a.astype(np.float64) * b.astype(np.float64) + c.astype(np.float64)
    out = r.astype(f32)
    lo = out.astype(np.float64)
    nxt = np.nextafter(out, np.where(r > lo, f32(np.inf), f32(-np.inf))).astype(np.float64)
    mid = (lo + nxt) / 2
    for k in np.nonzero((r == mid) & (lo != nxt))[0]:
        ex = Fraction(float(a[k])) * Fraction(float(b[k])) + Fraction(float(c[k]))
        cands = [float(out[k]), float(nxt[k])]
        d = [abs(Fraction(v) - ex) for v in cands]
        if d[0] != d[1]:
            out[k] = f32(cands[0] if d[0] < d[1] else cands[1])
        else:
            out[k] = f32(cands[0] if (np.array(cands[0], f32).view(np.uint32) & 1) == 0 else cands[1])
    return out


def _filtres(rate, phases, lineaire):
    fac = min(SORTIE * 0.97 / rate, 1.0)
    L = max(math.ceil(32 / fac), 1); L += L & 1
    c = (L - 1) // 2; nb = phases if phases % 2 else phases // 2 + 1
    alloc = (L + 7) // 8 * 8
    F = np.zeros((phases + 1, alloc), f32); norm = 0.0
    for p in range(nb):
        s = math.sin(math.pi * p / phases) * (1 if c & 1 else -1) if fac == 1.0 else 0.0
        t = []
        for i in range(L):
            x = math.pi * ((i - c) - p / phases) * fac
            y = 1.0 if x == 0 else (s / x if fac == 1.0 else math.sin(x) / x)
            w = 2.0 * x / (fac * L * math.pi)
            y *= _i0(9.0 * math.sqrt(max(1 - w * w, 0)))
            t.append(y); s = -s
            if not p:
                norm += y
        F[p, :L] = [f32(v / norm) for v in t]
        if phases % 2 == 0:
            for i in range(L):
                F[phases - p, L - 1 - i] = F[p, i]
    if lineaire:
        F[phases, 1:alloc] = F[0, 0:alloc - 1]; F[phases, 0] = F[0, alloc - 1]
    return F, L, alloc


def _sommes(acc):
    a = acc[:, 0:4] + acc[:, 4:8]
    return a, (a[:, 0] + a[:, 2]) + (a[:, 1] + a[:, 3])


def reechantillonner(x, rate):
    if rate == SORTIE:
        return x
    q = Fraction(SORTIE, rate)
    lineaire = q.numerator > PHASES_MAX
    ph = PHASES_MAX if lineaire else q.numerator
    F, L, alloc = _filtres(rate, ph, lineaire)
    n = len(x)
    if n < L + 1:
        raise ValueError('Sound too short to resample')
    idx, pos = -ph * ((L - 1) // 2), L
    while idx < 0:
        pos -= 1; idx += ph
    if lineaire:
        src_incr, dst_incr = SORTIE, rate * ph
    else:
        q = Fraction(SORTIE, rate * ph); src_incr, dst_incr = 1, q.denominator // q.numerator
    U = ph * src_incr; Q0 = (pos * ph + idx) * src_incr
    k = max(0, -(-((1 + n) * U - Q0) // dst_incr))
    reste = (L + n) - (Q0 + dst_incr * k) // U
    refl = (min(reste, L) + 1) // 2
    Q = np.arange(Q0, (1 + n + refl) * U, dst_incr, dtype=np.int64)
    e = Q // U; f = (Q // src_incr) % ph
    X = x.astype(f32) * f32(1 / 32768)
    B = np.concatenate([X[1:L + 1][::-1], X, X[n - 1 - np.arange(refl)], np.zeros(alloc + L, f32)])
    acc = np.zeros((len(Q), 8), f32)
    acc2 = np.zeros((len(Q), 8), f32) if lineaire else None
    for k in range(0, alloc, 8):
        for j in range(8):
            s = B[e + k + j]
            acc[:, j] = _fma32(s, F[f, k + j], acc[:, j])
            if lineaire:
                acc2[:, j] = _fma32(s, F[f + 1, k + j], acc2[:, j])
    v, total = _sommes(acc)
    if lineaire:
        w, _ = _sommes(acc2)
        fl = ((Q % src_incr).astype(f32) * (f32(1.0) / f32(src_incr))).astype(f32)
        v = v + (w - v) * fl[:, None]
        total = (v[:, 0] + v[:, 2]) + (v[:, 1] + v[:, 3])
    return np.clip(np.rint(total * f32(32768)), -32768, 32767).astype(np.int64)


ADAPT = (230, 230, 230, 230, 307, 409, 512, 614, 768, 614, 512, 409, 307, 230, 230, 230)
COEF1 = (64, 128, 0, 48, 60, 115, 98)
COEF2 = (0, -64, 0, 16, 0, -52, -58)


def _tdiv(a, b):
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


def msadpcm_encoder(x, bloc=256):
    par_bloc = (bloc - 7) * 2 + 2
    n = len(x)
    total = -(-n // par_bloc) * par_bloc
    s = [int(v) for v in x] + [0] * (total - n)
    out = bytearray()
    idelta = 0
    for a in range(0, total, par_bloc):
        if idelta < 16:
            idelta = 16
        s2, s1 = s[a], s[a + 1]
        out += struct.pack('<BHhh', 0, idelta, s1, s2)
        k = a + 2
        for _ in range(bloc - 7):
            octet = 0
            for decal in (4, 0):
                pred = _tdiv(s1 * 64, 64)
                d = s[k] - pred
                biais = idelta // 2 if d >= 0 else -(idelta // 2)
                nib = max(-8, min(7, _tdiv(d + biais, idelta))) & 15
                pred += (nib - 16 if nib & 8 else nib) * idelta
                s2, s1 = s1, max(-32768, min(32767, pred))
                idelta = max(16, (ADAPT[nib] * idelta) >> 8)
                octet |= nib << decal
                k += 1
            out.append(octet)
    return bytes(out), n


def msadpcm_decoder(data, bloc, par_bloc):
    out = []
    for a in range(0, len(data) - bloc + 1, bloc):
        b = data[a:a + bloc]
        pred = b[0]
        if pred > 6:
            raise ValueError('MS-ADPCM: invalid predictor')
        idelta, s1, s2 = struct.unpack_from('<hhh', b, 1)
        c1, c2 = COEF1[pred], COEF2[pred]
        out += [s2, s1]
        for octet in b[7:7 + (par_bloc - 2 + 1) // 2]:
            for nib in (octet >> 4, octet & 15):
                if len(out) % par_bloc == 0:
                    break
                t = s1 * c1 + s2 * c2
                p = abs(t) // 64 * (1 if t >= 0 else -1)
                p += (nib - 16 if nib & 8 else nib) * idelta
                p = max(-32768, min(32767, p))
                s2, s1 = s1, p
                idelta = max(16, (ADAPT[nib] * idelta) >> 8)
                out.append(p)
    return np.array(out, np.int64)


def encoder(pcm, rate):
    donnees, n = msadpcm_encoder(reechantillonner(np.asarray(pcm, np.int64), rate))
    return dict(frames=n, data=donnees, block=256, spb=500)


class _Info(ctypes.Structure):
    _fields_ = [('channels', ctypes.c_int), ('channelConfigIndex', ctypes.c_int), ('samplingRate', ctypes.c_int),
                ('superframeSize', ctypes.c_int), ('framesInSuperframe', ctypes.c_int), ('frameSamples', ctypes.c_int),
                ('wlength', ctypes.c_int), ('configData', ctypes.c_ubyte * 4)]


_dll = {}


def _atrac9(dll_path):
    cle = str(dll_path)
    if cle not in _dll:
        try:
            d = ctypes.CDLL(cle)
        except OSError:
            raise ValueError('libatrac9.dll missing or unreadable (tools folder)') from None
        d.Atrac9GetHandle.restype = ctypes.c_void_p
        d.Atrac9ReleaseHandle.argtypes = [ctypes.c_void_p]
        d.Atrac9InitDecoder.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        d.Atrac9GetCodecInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Info)]
        d.Atrac9Decode.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_short),
                                   ctypes.POINTER(ctypes.c_int)]
        _dll[cle] = d
    return _dll[cle]


def decoder_atrac9(config, data, dll_path):
    d = _atrac9(dll_path)
    handle = d.Atrac9GetHandle()
    if not handle:
        raise ValueError('ATRAC9: decoder unavailable')
    try:
        if d.Atrac9InitDecoder(handle, bytes(config)) != 0:
            raise ValueError('ATRAC9: configuration refused')
        info = _Info(); d.Atrac9GetCodecInfo(handle, ctypes.byref(info))
        if info.channels != 1 or not 0 < info.frameSamples <= 4096 or info.superframeSize <= 0:
            raise ValueError('ATRAC9: unexpected stream')
        pcm = (ctypes.c_short * info.frameSamples)(); used = ctypes.c_int(); sortie = []
        sf = info.superframeSize
        for pos in range(0, len(data) - sf + 1, sf):
            bloc = bytes(data[pos:pos + sf]) + bytes(16); dans = 0
            for _ in range(info.framesInSuperframe):
                if dans >= sf or d.Atrac9Decode(handle, bloc[dans:], pcm, ctypes.byref(used)) != 0:
                    raise ValueError('ATRAC9: unreadable frame')
                sortie.append(np.frombuffer(bytes(pcm), np.int16).copy()); dans += used.value
        return np.concatenate(sortie) if sortie else np.zeros(0, np.int16)
    finally:
        d.Atrac9ReleaseHandle(handle)


def ondes_ps4(b):
    if b[:7] != b'XWSFILE':
        raise ValueError('PS4 sound bank: XWS header expected')
    chunks = [struct.unpack_from('<II', b, 0x30 + 8 * i) for i in range(u(b, 0x14))]
    ho, hs = chunks[0]; bo, bs = chunks[1] if chunks[1][0] else (hs, len(b) - hs)
    if b[ho:ho + 4] != b'K4HD':
        raise ValueError('PS4 sound bank: K4HD expected')
    ppva = ho + h(b, ho + 0x18)
    if b[ppva:ppva + 4] != b'PPVA' or u(b, ppva + 8) != 0x1c:
        raise ValueError('PS4 sound bank: PPVA expected')
    out = []
    for i in range(u(b, ppva + 0x14) + 1):
        e = ppva + 0x20 + i * 0x1c
        off, rate, size, loop, codec, ls, le = struct.unpack_from('<IIIiIII', b, e)
        if off == 0xffffffff or size in (0, 0xffffffff):
            out.append(None); continue
        s = bo + off; extra = u(b, s)
        if s + size > len(b) or extra >= size:
            raise ValueError('PS4 sound bank: wave outside the bank')
        out.append(dict(index=i, rate=rate, config=b[s + 4:s + 8], loop_end=le, data=b[s + extra:s + size]))
    return out


def _pan(v): return -1.0 if v == 0 else 1.0 if v == 127 else (0.0 if v == 64 else (v - 64) / 63.0)
def _group(v): return -1.0 if v == 0 else 1.0 if v == 127 else (-0.0 if v == 64 else (v - 64) / 63.0)
def _volume(w): return 10 ** (round(40 * math.log10(w / 4096.0)) / 40) if w else 0.0


def programmes(b):
    ho = 0x40; pg = ho + h(b, ho + 0x10); tn = ho + h(b, ho + 0x14)
    offs = []; i = 0; first = None
    while True:
        at = pg + 0x20 + 4 * i
        if first is not None and at >= pg + first:
            break
        o = u(b, at)
        if o != 0xffffffff:
            if not (0x20 < o and pg + o < tn and o % 4 == 0):
                break
            first = o if first is None else min(first, o)
        offs.append(o); i += 1
        if i > 8192:
            raise ValueError('PS4 sound bank: unreadable program table')
    while offs and offs[-1] == 0xffffffff:
        offs.pop()
    ntn = u(b, tn + 0x14) + 1
    tones = [struct.unpack_from('<31I', b, tn + 0x20 + t * 0x7c) for t in range(ntn)]
    tabend = pg + 0x20 + 4 * len(offs); progs = []
    for o in offs:
        if o == 0xffffffff:
            progs.append(None); continue
        a = pg + o
        later = [pg + x for x in offs if x != 0xffffffff and pg + x > a]
        end = (min(later) - 0x40) if later else tn
        lst = [u(b, x) for x in range(a + 0xc, end, 4)]
        if not lst or any(t >= ntn for t in lst):
            raise ValueError('PS4 sound bank: inconsistent program')
        f0, f1 = struct.unpack_from('<ff', b, a - 0x2c) if a - 0x2c >= tabend else (10.0, 10.0)
        pre = struct.unpack_from('<IIIII', b, a - 0x40) if a - 0x40 >= tabend else (0, 0, 0xffffffff, 0xffffffff, 0)
        progs.append(dict(tones=lst, dist=(f0, f1), F=struct.unpack_from('<f', b, a)[0], X=pre[4],
                          block=b[a - 0x2c:a - 0x08] if a - 0x40 >= tabend else None))
    return progs, tones


def _verifier_kwb(b, quoi):
    if b[:7] != b'XWSFILE' or len(b) < 0x818:
        raise ValueError(quoi + ': XWS header expected')
    k = u(b, 0x30)
    if k != 0x800 or b[k:k + 4] != b'KWB2':
        raise ValueError(quoi + ': KWB2 expected')
    return k


def construire(ps4, modele, dll_path):
    b = bytes(ps4); k = _verifier_kwb(modele, 'PC template bank')
    progs, tones = programmes(b)
    ondes = ondes_ps4(b)
    loopw = {e['index'] for e in ondes if e and e['loop_end'] > 0}
    count = len(progs)
    size = k + 0x18 + count * 4 + sum(0x2c + len(p['tones']) * 0x48 for p in progs if p)
    body = aligner(size, 0x800); buf = bytearray(body); buf[:k + 0x18] = modele[:k + 0x18]
    struct.pack_into('<H', buf, k + 6, count); pcm = bytearray(); cur = k + 0x18 + count * 4; enc = {}
    for pi, p in enumerate(progs):
        if not p:
            struct.pack_into('<I', buf, k + 0x18 + pi * 4, 0); continue
        struct.pack_into('<I', buf, k + 0x18 + pi * 4, cur - k)
        n = len(p['tones']); hdr = bytearray(0x2c); X = p['X']; groups = {tones[t][22] for t in p['tones']}
        hdr[0] = (0x20 if X & 0x200 else 0) | (1 if any(((tones[t][24] >> 8) & 0xff) == 4 for t in p['tones'])
                                               and len(groups) > 1 else 0)
        hdr[1] = 0x80; w1 = max(tones[t][1] for t in p['tones'])
        hdr[2] = max(0, min(127, round(50 + (w1 - 63) * 5 / 3))); hdr[3] = n; hdr[7] = (X >> 16) & 0xff
        if p['block'] is not None:
            hdr[8:0x2c] = p['block']
        else:
            struct.pack_into('<ff', hdr, 8, *p['dist'])
        if p['F'] != -1.0:
            struct.pack_into('<f', hdr, 0x10, float(f"{p['F']:.6g}"))
        buf[cur:cur + 0x2c] = hdr
        for j, t in enumerate(p['tones']):
            w = tones[t]; wi = w[0]
            if wi not in enc:
                o = ondes[wi] if wi < len(ondes) else None
                if o is None:
                    raise ValueError('PS4 sound bank: missing wave')
                e = encoder(decoder_atrac9(o['config'], o['data'], dll_path), o['rate']); e['offset'] = len(pcm)
                pcm += e['data']; pcm += b'\0' * (aligner(len(pcm), 8) - len(pcm)); enc[wi] = e
            e = enc[wi]; v = bytearray(0x48)
            struct.pack_into('<HBBHHIIII', v, 0, SORTIE, 0x10, 1, e['block'], e['spb'], w[23], e['frames'],
                             e['offset'], len(e['data']))
            v[0x1c] = 0x21 if ((w[24] >> 8) & 0xff == 4 and wi in loopw) else 0
            b2 = (w[24] >> 16) & 0xff; v[0x1d] = 0x80 | (b2 if b2 != 0xff else 0); v[0x1f] = 0x64
            struct.pack_into('<f', v, 0x20, _volume(w[8])); struct.pack_into('<ff', v, 0x30, _pan(w[9]), _group(w[22]))
            buf[cur + 0x2c + j * 0x48:cur + 0x2c + (j + 1) * 0x48] = v
        cur += 0x2c + n * 0x48
    struct.pack_into('<I', buf, k + 8, len(pcm)); struct.pack_into('<II', buf, 0x30, k, body)
    buf += pcm; buf += b'\0' * (aligner(len(buf), 0x800) - len(buf)); struct.pack_into('<I', buf, 16, len(buf))
    return bytes(buf)


def _charger_kwb(b, quoi):
    k = _verifier_kwb(b, quoi); body = u(b, 0x34)
    pcm = b[body:body + u(b, k + 8)]; progs = []
    for i in range(h(b, k + 6)):
        o = u(b, k + 0x18 + 4 * i)
        if not o:
            progs.append(None); continue
        a = k + o; nt = b[a + 3]
        progs.append((b[a:a + 0x2c], [bytearray(b[a + 0x2c + j * 0x48:a + 0x2c + (j + 1) * 0x48]) for j in range(nt)]))
    return k, progs, pcm


def _sauver_kwb(modele, k, progs, pcm):
    count = len(progs); size = k + 0x18 + count * 4 + sum(0x2c + len(p[1]) * 0x48 for p in progs if p)
    body = aligner(size, 0x800); buf = bytearray(body); buf[:k + 0x18] = modele[:k + 0x18]
    struct.pack_into('<H', buf, k + 6, count); cur = k + 0x18 + count * 4
    for i, p in enumerate(progs):
        if not p:
            struct.pack_into('<I', buf, k + 0x18 + 4 * i, 0); continue
        struct.pack_into('<I', buf, k + 0x18 + 4 * i, cur - k); hdr, tons = p
        buf[cur:cur + 0x2c] = hdr
        for j, t in enumerate(tons):
            buf[cur + 0x2c + j * 0x48:cur + 0x2c + (j + 1) * 0x48] = t
        cur += 0x2c + len(tons) * 0x48
    struct.pack_into('<I', buf, k + 8, len(pcm)); struct.pack_into('<II', buf, 0x30, k, body)
    buf += pcm; buf += b'\0' * (aligner(len(buf), 0x800) - len(buf)); struct.pack_into('<I', buf, 16, len(buf))
    return bytes(buf)


def ajouter_programme(banque, source, prog):
    k, progs, pcm0 = _charger_kwb(banque, 'PC sound bank'); _, sprogs, spcm = _charger_kwb(source, 'PC source bank')
    if prog >= len(sprogs) or not sprogs[prog]:
        raise ValueError('PC source bank: program not found')
    hdr, tons = sprogs[prog]; pcm = bytearray(pcm0); nouveaux = []
    for t in tons:
        rate, _, _, bloc, spb, w23, frames, off, size = struct.unpack_from('<HBBHHIIII', t, 0)
        x = msadpcm_decoder(spcm[off:off + size], bloc, spb)
        donnees, n = msadpcm_encoder(reechantillonner(x, rate))
        pcm += b'\0' * (aligner(len(pcm), 8) - len(pcm)); o = len(pcm); pcm += donnees
        v = bytearray(t)
        struct.pack_into('<HBBHHIIII', v, 0, SORTIE, 0x10, 1, 256, 500, w23, n, o, len(donnees))
        struct.pack_into('<I', v, 0x18, 0); v[0x1c] = 0
        nouveaux.append(v)
    progs.append((hdr, nouveaux))
    return _sauver_kwb(banque, k, progs, bytes(pcm))


def banque_stage(inputs, dll_path, programme=None):
    if len(inputs) not in (2, 3) or (len(inputs) == 3) != (programme is not None):
        raise ValueError('Stage sound: wrong number of sources')
    banque = construire(inputs[0], inputs[1], dll_path)
    if programme is not None:
        banque = ajouter_programme(banque, inputs[2], programme)
    return banque
