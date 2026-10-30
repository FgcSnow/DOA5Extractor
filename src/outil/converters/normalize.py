import hashlib, subprocess, io
import numpy as np
from PIL import Image
from convert_structures import *
TEXCONV = None
CACHE = None
COMP = None
def normalize(tmc, tmcl, headers=True):
    a = bytearray(tmc)
    s = sections(a)
    t = s['TTDM']
    l = t[u(t, 40):]
    base = children(s['LHeader'])[0]
    tops = {a[o:o + 8].rstrip(b'\x00').decode(): o for o in children(a) if o}
    lp = tops['TTDM'] + u(t, 40)
    lhp = tops['LHeader']
    out = bytearray(tmcl[:base + 64])
    changes = []
    for i, o in enumerate(children(l)):
        n = u(l, u(l, 36) + 4 * i)
        original = tmcl[base + o:base + o + n]
        b = bytearray(original)
        change = {}
        if b[84:88] == bytes(4) and len(b) >= 262144:
            key = hashlib.sha256(b).hexdigest()
            f = CACHE / (key + '.dds')
            q = COMP / f.name
            if not q.exists():
                f.write_bytes(b)
                proc = subprocess.run([str(TEXCONV), '-nologo', '-y', '-f', 'BC3_UNORM', '-bc', 'u', '-m', str(max(1, u(b, 28))), '-o', str(COMP), str(f)], capture_output=True, text=True)
                if proc.returncode:
                    raise RuntimeError(proc.stdout + proc.stderr)
            b = bytearray(q.read_bytes())
            assert b[:4] == b'DDS ' and b[84:88] == b'DXT5'
            assert (u(b, 12), u(b, 16), max(1, u(b, 24)), u(b, 112), max(1, u(b, 28))) == (u(original, 12), u(original, 16), max(1, u(original, 24)), u(original, 112), max(1, u(original, 28)))
            x = np.array(Image.open(io.BytesIO(original)).convert('RGBA'), dtype=np.float32)
            y = np.array(Image.open(io.BytesIO(b)).convert('RGBA'), dtype=np.float32)
            rmse = float(np.sqrt(((x - y) ** 2).mean()))
            assert rmse < 12, (i, rmse)
            change.update(recompressed_bgra_to_bc3=True, bytes_before=len(original), bytes_after=len(b), rmse_first_slice=rmse)
        if headers and u(b, 112) == 2097152:
            if max(1, u(b, 28)) == 1:
                put(b, 8, u(b, 8) & ~131072)
            if b[84:88] in (b'DXT1', b'DXT3', b'DXT5'):
                unit = 8 if b[84:88] == b'DXT1' else 16
                put(b, 20, max(1, (u(b, 16) + 3) // 4) * max(1, (u(b, 12) + 3) // 4) * unit * max(1, u(b, 24)))
        elif headers and u(b, 24) == 1:
            put(b, 24, 0)
        if b[:128] != original[:128]:
            change['header_normalized_or_format_changed'] = True
        if change:
            changes.append(dict(index=i, **change))
        put(a, lp + u(l, 32) + 4 * i, len(out) - base)
        put(a, lp + u(l, 36) + 4 * i, len(b))
        out.extend(b)
        out.extend(bytes(-len(out) % 64))
    bs = len(out) - base
    put(out, base + 4, bs)
    put(a, lp + 68, bs)
    put(a, lhp + u(s['LHeader'], 36), bs)
    out.extend(bytes(-len(out) % 128))
    put(out, 4, len(out))
    put(a, lhp + 68, len(out))
    return (bytes(a), bytes(out), changes)
