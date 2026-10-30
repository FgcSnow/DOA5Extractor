from convert_structures import *

def motion(b, bones):
    count = bones * 9
    start = u(b, 0)
    assert 16 <= start <= 256 and start % 16 == 0
    starts = []
    for pos in range(0, start, 8):
        assert not u(b, pos + 4)
        value = u(b, pos)
        if value:
            starts.append(value)
    assert starts and starts == sorted(starts)
    end = max(starts) + count * 8
    tables = [struct.unpack_from('<' + 'Q' * count, b, s) for s in starts]
    assert all((end <= o < len(b) for offsets in tables for o in offsets))
    header = (len(starts) * 4 // 16 + 1) * 16
    out = bytearray(header)
    out.extend(b[end:])
    shift = end - header
    for index, offsets in enumerate(tables):
        put(out, index * 4, len(out))
        out.extend(struct.pack('<' + 'I' * count, *[o - shift for o in offsets]))
        out.extend(bytes(16 - len(out) % 16))
    return out

def convert_animation(b):
    slots = u(b, 20)
    tab = u(b, 32)
    offsets = [u(b, tab + 4 * i) for i in range(slots)]
    present = [(i, o) for i, o in enumerate(offsets) if o]
    is_root = b[present[0][1]:present[0][1] + 8] == b'char_dat'
    out = bytearray(b[:tab + 4 * slots])
    for k, (index, o) in enumerate(present):
        z = present[k + 1][1] if k + 1 < len(present) else len(b)
        chunk = b[o:z]
        if is_root:
            chunk = convert_animation(chunk[:u(chunk, 16)])
        elif index == 0:
            chunk = motion(chunk, u(b, offsets[3] + 8))
        put(out, tab + 4 * index, len(out))
        out.extend(chunk)
        out.extend(bytes(align(len(out)) - len(out)))
    put(out, 16, len(out))
    return out
