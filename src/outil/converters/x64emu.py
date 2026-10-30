import struct

import capstone
import numpy as np
from capstone import x86_const as X

MASQUE = (1 << 64) - 1
f32 = np.float32

_GPR = {}
for _b, _n in (('rax', 'a'), ('rbx', 'b'), ('rcx', 'c'), ('rdx', 'd')):
    _GPR[_b] = (_b, 0, 8); _GPR['e%sx' % _n] = (_b, 0, 4); _GPR['%sx' % _n] = (_b, 0, 2)
    _GPR['%sl' % _n] = (_b, 0, 1); _GPR['%sh' % _n] = (_b, 1, 1)
for _b in ('rsi', 'rdi', 'rbp', 'rsp'):
    _GPR[_b] = (_b, 0, 8); _GPR['e' + _b[1:]] = (_b, 0, 4); _GPR[_b[1:]] = (_b, 0, 2); _GPR[_b[1:] + 'l'] = (_b, 0, 1)
for _i in range(8, 16):
    _b = 'r%d' % _i
    _GPR[_b] = (_b, 0, 8); _GPR[_b + 'd'] = (_b, 0, 4); _GPR[_b + 'w'] = (_b, 0, 2); _GPR[_b + 'b'] = (_b, 0, 1)


class Arret(Exception):
    pass


class Memoire:

    def __init__(self, image):
        self.image = image; self.pages = {}

    def _page(self, n):
        p = self.pages.get(n)
        if p is None:
            a = n << 12
            p = bytearray(self.image[a:a + 4096]) if a < len(self.image) else bytearray()
            p += bytes(4096 - len(p)); self.pages[n] = p
        return p

    def lire(self, a, n):
        a &= MASQUE; out = bytearray()
        while n:
            o = a & 0xFFF; k = min(n, 4096 - o); out += self._page(a >> 12)[o:o + k]; a += k; n -= k
        return bytes(out)

    def ecrire(self, a, d):
        a &= MASQUE; i = 0
        while i < len(d):
            o = a & 0xFFF; k = min(len(d) - i, 4096 - o); self._page(a >> 12)[o:o + k] = d[i:i + k]; a += k; i += k

    def u64(self, a):
        return struct.unpack('<Q', self.lire(a, 8))[0]


class Machine:
    def __init__(self, image, limite_code):
        self.m = Memoire(image); self.limite_code = limite_code
        self.r = {k: 0 for k in ('rax', 'rbx', 'rcx', 'rdx', 'rsi', 'rdi', 'rbp', 'rsp', 'r8', 'r9', 'r10', 'r11',
                                 'r12', 'r13', 'r14', 'r15')}
        self.y = [bytearray(32) for _ in range(16)]
        self.cf = self.zf = self.sf = self.of = False
        self.cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64); self.cs.detail = True
        self.cache = {}; self.compte = 0

    def lire_reg(self, nom):
        if nom.startswith(('xmm', 'ymm')):
            v = self.y[int(nom[3:])]; return bytes(v[:16] if nom[0] == 'x' else v)
        b, o, t = _GPR[nom]
        return (self.r[b] >> (8 * o)) & ((1 << (8 * t)) - 1)

    def ecrire_reg(self, nom, v):
        if nom.startswith(('xmm', 'ymm')):
            i = int(nom[3:]); d = bytes(v)
            self.y[i][:] = d + bytes(32 - len(d)); return
        b, o, t = _GPR[nom]; v &= (1 << (8 * t)) - 1
        if t == 8:
            self.r[b] = v
        elif t == 4:
            self.r[b] = v
        else:
            m = ((1 << (8 * t)) - 1) << (8 * o); self.r[b] = (self.r[b] & ~m) | (v << (8 * o))

    def adresse(self, i, op):
        mem = op.mem; a = mem.disp
        if mem.base:
            n = i.reg_name(mem.base)
            a += (i.address + i.size) if n == 'rip' else self.r[_GPR[n][0]] if _GPR[n][2] == 8 else self.lire_reg(n)
        if mem.index:
            a += self.lire_reg(i.reg_name(mem.index)) * mem.scale
        if mem.segment:
            raise Arret('segment %s' % i.reg_name(mem.segment))
        return a & MASQUE

    def lire_op(self, i, op, taille=None):
        t = taille or op.size
        if op.type == X.X86_OP_REG:
            n = i.reg_name(op.reg)
            if n.startswith(('xmm', 'ymm')):
                return self.lire_reg(n)[:t]
            return self.lire_reg(n)
        if op.type == X.X86_OP_IMM:
            return op.imm & ((1 << (8 * t)) - 1)
        a = self.adresse(i, op); d = self.m.lire(a, t)
        return d if taille is not None and taille > 8 or op.size > 8 else int.from_bytes(d, 'little')

    def ecrire_op(self, i, op, v, taille=None):
        t = taille or op.size
        if op.type == X.X86_OP_REG:
            self.ecrire_reg(i.reg_name(op.reg), v); return
        a = self.adresse(i, op)
        self.m.ecrire(a, v if isinstance(v, (bytes, bytearray)) else (v & ((1 << (8 * t)) - 1)).to_bytes(t, 'little'))

    def pousser(self, v):
        self.r['rsp'] = (self.r['rsp'] - 8) & MASQUE; self.m.ecrire(self.r['rsp'], struct.pack('<Q', v & MASQUE))

    def retirer(self):
        v = self.m.u64(self.r['rsp']); self.r['rsp'] = (self.r['rsp'] + 8) & MASQUE; return v

    def _arith(self, a, b, t, sous):
        m = (1 << (8 * t)) - 1; s = 1 << (8 * t - 1)
        if sous:
            r = (a - b) & m; self.cf = a < b; self.of = bool((a ^ b) & (a ^ r) & s)
        else:
            r = (a + b) & m; self.cf = a + b > m; self.of = bool(~(a ^ b) & (a ^ r) & s)
        self.zf = r == 0; self.sf = bool(r & s); return r

    def _logique(self, r, t):
        self.cf = self.of = False; self.zf = r == 0; self.sf = bool(r >> (8 * t - 1) & 1); return r

    def decoder(self, a):
        i = self.cache.get(a)
        if i is None:
            i = next(self.cs.disasm(self.m.lire(a, 16), a), None)
            if i is None:
                raise Arret('unreadable instruction at %#x' % a)
            self.cache[a] = i
        return i

    def pas(self, rip):
        i = self.decoder(rip); ops = i.operands; n = i.mnemonic; suiv = rip + i.size
        self.compte += 1
        if n in ('mov', 'movabs'):
            self.ecrire_op(i, ops[0], self.lire_op(i, ops[1], ops[0].size))
        elif n == 'movzx':
            self.ecrire_op(i, ops[0], self.lire_op(i, ops[1]))
        elif n in ('movsxd', 'movsx'):
            t = ops[1].size; v = self.lire_op(i, ops[1])
            if v >> (8 * t - 1):
                v -= 1 << (8 * t)
            self.ecrire_op(i, ops[0], v)
        elif n == 'lea':
            self.ecrire_op(i, ops[0], self.adresse(i, ops[1]))
        elif n == 'push':
            self.pousser(self.lire_op(i, ops[0], 8))
        elif n == 'pop':
            self.ecrire_op(i, ops[0], self.retirer())
        elif n == 'call':
            cible = self.lire_op(i, ops[0], 8) if ops[0].type != X.X86_OP_IMM else ops[0].imm
            self.pousser(suiv); return cible & MASQUE
        elif n == 'ret':
            return self.retirer()
        elif n == 'jmp':
            return (self.lire_op(i, ops[0], 8) if ops[0].type != X.X86_OP_IMM else ops[0].imm) & MASQUE
        elif n in _SAUTS:
            return ops[0].imm if _SAUTS[n](self) else suiv
        elif n in _POSITIONS:
            self.ecrire_op(i, ops[0], 1 if _POSITIONS[n](self) else 0)
        elif n in ('add', 'sub', 'cmp'):
            t = ops[0].size; a = self.lire_op(i, ops[0]); b = self.lire_op(i, ops[1], t)
            r = self._arith(a, b, t, n != 'add')
            if n != 'cmp':
                self.ecrire_op(i, ops[0], r)
        elif n in ('and', 'xor', 'or', 'test'):
            t = ops[0].size; a = self.lire_op(i, ops[0]); b = self.lire_op(i, ops[1], t)
            r = self._logique(a & b if n in ('and', 'test') else a ^ b if n == 'xor' else a | b, t)
            if n != 'test':
                self.ecrire_op(i, ops[0], r)
        elif n in ('inc', 'dec'):
            t = ops[0].size; cf = self.cf
            self.ecrire_op(i, ops[0], self._arith(self.lire_op(i, ops[0]), 1, t, n == 'dec')); self.cf = cf
        elif n == 'nop':
            pass
        elif n in _VECT:
            _VECT[n](self, i, ops)
        else:
            raise Arret('unsupported instruction at %#x: %s %s' % (rip, n, i.op_str))
        return suiv


_SAUTS = {'je': lambda s: s.zf, 'jne': lambda s: not s.zf, 'ja': lambda s: not s.cf and not s.zf,
          'jae': lambda s: not s.cf, 'jb': lambda s: s.cf, 'jbe': lambda s: s.cf or s.zf,
          'js': lambda s: s.sf, 'jns': lambda s: not s.sf, 'jl': lambda s: s.sf != s.of, 'jge': lambda s: s.sf == s.of,
          'jle': lambda s: s.zf or s.sf != s.of, 'jg': lambda s: not s.zf and s.sf == s.of}
_POSITIONS = {'set' + k[1:]: v for k, v in _SAUTS.items()}


def _taille(i, op):
    if op.type != X.X86_OP_REG:
        return op.size
    return 32 if i.reg_name(op.reg).startswith('ymm') else 16


def _src(m, i, op, t):
    if op.type == X.X86_OP_REG:
        return m.lire_reg(i.reg_name(op.reg))[:t]
    return m.m.lire(m.adresse(i, op), t)


def _ecrire_vect(m, i, op, d):
    if op.type == X.X86_OP_REG:
        m.ecrire_reg(i.reg_name(op.reg), d)
    else:
        m.m.ecrire(m.adresse(i, op), d)


def _mov(m, i, ops):
    t = _taille(i, ops[0]) if ops[0].type == X.X86_OP_REG else ops[0].size
    _ecrire_vect(m, i, ops[0], _src(m, i, ops[1], t))


def _movss(m, i, ops):
    if len(ops) == 3:
        a = m.lire_reg(i.reg_name(ops[1].reg)); b = m.lire_reg(i.reg_name(ops[2].reg))
        m.ecrire_reg(i.reg_name(ops[0].reg), b[:4] + a[4:16])
    elif ops[0].type == X.X86_OP_REG:
        m.ecrire_reg(i.reg_name(ops[0].reg), _src(m, i, ops[1], 4))
    else:
        m.m.ecrire(m.adresse(i, ops[0]), m.lire_reg(i.reg_name(ops[1].reg))[:4])


def _binaire(fn, dt):
    def h(m, i, ops):
        t = _taille(i, ops[0])
        a = np.frombuffer(_src(m, i, ops[1], t), dt); b = np.frombuffer(_src(m, i, ops[2], t), dt)
        m.ecrire_reg(i.reg_name(ops[0].reg), fn(a, b).astype(dt).tobytes())
    return h


def _blend(m, i, ops):
    t = _taille(i, ops[0])
    a = np.frombuffer(_src(m, i, ops[1], t), np.uint32); b = np.frombuffer(_src(m, i, ops[2], t), np.uint32)
    if i.mnemonic == 'vblendps':
        sel = np.array([(ops[3].imm >> k) & 1 for k in range(t // 4)], bool)
    else:
        sel = np.frombuffer(_src(m, i, ops[3], t), np.uint32) >> 31 == 1
    m.ecrire_reg(i.reg_name(ops[0].reg), np.where(sel, b, a).astype(np.uint32).tobytes())


def _cvttps2dq(m, i, ops):
    t = _taille(i, ops[0]); a = np.frombuffer(_src(m, i, ops[1], t), f32)
    with np.errstate(invalid='ignore'):
        ok = np.isfinite(a) & (a > -2147483904.0) & (a < 2147483648.0)
        r = np.where(ok, np.trunc(np.where(ok, a, 0)).astype(np.int64), -2147483648).astype(np.int32)
    m.ecrire_reg(i.reg_name(ops[0].reg), r.tobytes())


def _cvtdq2ps(m, i, ops):
    t = _taille(i, ops[0])
    m.ecrire_reg(i.reg_name(ops[0].reg), np.frombuffer(_src(m, i, ops[1], t), np.int32).astype(f32).tobytes())


def _flottant(fn):
    def g(a, b):
        with np.errstate(all='ignore'):
            return fn(a, b)
    return g


_VECT = {
    'vmovaps': _mov, 'vmovdqa': _mov, 'vmovups': _mov, 'vmovdqu': _mov, 'vmovss': _movss,
    'vmulps': _binaire(_flottant(np.multiply), f32), 'vaddps': _binaire(_flottant(np.add), f32),
    'vsubps': _binaire(_flottant(np.subtract), f32),
    'vandps': _binaire(np.bitwise_and, np.uint32), 'vpand': _binaire(np.bitwise_and, np.uint32),
    'vxorps': _binaire(np.bitwise_xor, np.uint32), 'vpxor': _binaire(np.bitwise_xor, np.uint32),
    'vpcmpeqd': _binaire(lambda a, b: np.where(a == b, 0xFFFFFFFF, 0), np.uint32),
    'vpaddd': _binaire(lambda a, b: (a.astype(np.uint64) + b) & 0xFFFFFFFF, np.uint32),
    'vblendps': _blend, 'vblendvps': _blend, 'vcvttps2dq': _cvttps2dq, 'vcvtdq2ps': _cvtdq2ps,
}


NODE_BASE, NODE_SZ, OBJ, STACK, RET = 0x40000000, 0x1000, 0x30000000, 0x20000000, 0x10
OPS = {0x906c40: 'SHOW', 0x90af70: 'COLL', 0x920300: 'SLEEP', 0x9201c0: 'WAKE', 0x90aee0: 'FLAGS', 0x920170: 'ENABLE',
       0x20f0: 'ANIM', 0x909550: 'SOUNDFX', 0x9068d0: 'DESCLIST', 0x90ab20: 'MGRINIT', 0x907ff0: 'MGRUPD', 0x90af20: 'CLEAR'}


class Controleur:

    def __init__(self, image, noeuds, local=(0x700000, 0x900000)):
        self.mc = mc = Machine(image, ((len(image) + 0xFFF) & ~0xFFF) + 0x4000000)
        self.n = noeuds; self.local = local; self.log = []; self.entree = None
        mc.m.ecrire(0, bytes(0x1000)); mc.m.ecrire(0, bytes((0x31, 0xC0, 0xC3)))
        mc.m.ecrire(OBJ + 0x2000, b''.join(struct.pack('<Q', NODE_BASE + i * NODE_SZ if i else 0) for i in range(noeuds)))
        mc.m.ecrire(OBJ + 0x50, struct.pack('<Q', OBJ + 0x2000))
        for i in range(1, noeuds):
            n = NODE_BASE + i * NODE_SZ
            mc.m.ecrire(n + 0x28, struct.pack('<Q', n + 0x800)); mc.m.ecrire(n + 0x30, struct.pack('<Q', n + 0x900))
        mc.m.ecrire(RET, b'\xf4')

    def idx(self, v):
        return (v - NODE_BASE) // NODE_SZ if NODE_BASE <= v < NODE_BASE + self.n * NODE_SZ else None

    def _retour(self, rip):
        mc = self.mc; ret = mc.m.u64(mc.r['rsp']); mc.r['rsp'] = (mc.r['rsp'] + 8) & MASQUE; mc.r['rax'] = 0
        return ret

    def _remplace(self, addr):
        mc = self.mc; r = mc.r
        x0 = struct.unpack('<f', bytes(mc.y[0][:4]))[0]
        op = OPS.get(addr, hex(addr)); rdi, rsi = r['rdi'], r['rsi']
        a = [self.idx(rdi) if self.idx(rdi) is not None else hex(rdi), self.idx(rsi) if self.idx(rsi) is not None else rsi,
             r['rdx'], r['rcx'], round(x0, 4)]
        if op == 'SHOW' and NODE_BASE <= rdi < NODE_BASE + self.n * NODE_SZ:
            a[0] = (rdi - 0x800 - NODE_BASE) // NODE_SZ
        if op == 'ANIM' and NODE_BASE <= rdi < NODE_BASE + self.n * NODE_SZ:
            a[0] = (rdi - 0x900 - NODE_BASE) // NODE_SZ
        self.log.append((op, a))

    def appeler(self, f, args=(), limite=2000000):
        mc = self.mc; sp = STACK - 0x1000 - 8; mc.m.ecrire(sp, struct.pack('<Q', RET)); mc.r['rsp'] = sp
        self.entree = f
        for nom, v in zip(('rdi', 'rsi', 'rdx', 'rcx'), args):
            mc.r[nom] = v
        rip = f; pas = 0
        try:
            while rip != RET:
                if pas >= limite:
                    break
                pas += 1
                if rip < 0x1000 or rip >= mc.limite_code and not (STACK - 0x100000 <= rip < 0x60000000):
                    self.log.append(('VCALL', [hex(rip)])); rip = self._retour(rip); continue
                if not (self.local[0] <= rip < self.local[1]) and rip != self.entree:
                    self._remplace(rip); rip = self._retour(rip); continue
                rip = mc.pas(rip)
        except Arret as e:
            self.log.append(('ERROR', [str(e), hex(rip)]))
            raise
        self.rip = rip
        return rip == RET

    def lire(self, a, n):
        return self.mc.m.lire(a, n)

    def ecrire(self, a, d):
        self.mc.m.ecrire(a, d)


class Natif:

    def __init__(self, image, remplacees):
        self.mc = Machine(image, ((len(image) + 0xFFF) & ~0xFFF) + 0x4000000); self.remplacees = remplacees
        self.mc.m.ecrire(RET, b'\xf4')

    def appeler(self, f, args=(), limite=20000000):
        mc = self.mc; sp = STACK - 0x1000 - 8; mc.m.ecrire(sp, struct.pack('<Q', RET)); mc.r['rsp'] = sp
        for nom, v in zip(('rdi', 'rsi', 'rdx', 'rcx', 'r8', 'r9'), args):
            mc.r[nom] = v
        rip = f; pas = 0
        while rip != RET:
            pas += 1
            if pas > limite:
                raise Arret('too many instructions')
            fn = self.remplacees.get(rip)
            if fn is not None:
                v = fn(mc); ret = mc.m.u64(mc.r['rsp']); mc.r['rsp'] = (mc.r['rsp'] + 8) & MASQUE
                mc.r['rax'] = (v or 0) & MASQUE; rip = ret; continue
            if rip < 0x1000:
                raise Arret('call to %#x' % rip)
            rip = mc.pas(rip)
        return True
