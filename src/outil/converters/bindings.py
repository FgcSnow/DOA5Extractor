from convert_structures import *
def bindings(b):
    old_strings = u(b, 8)
    assert old_strings % 32 == 0
    count = old_strings // 32
    new_strings = align(count * 20)
    out = bytearray(new_strings)
    for i in range(count):
        record = struct.unpack_from('<8I', b, i * 32)
        assert not any((record[j] for j in (1, 3, 7)))
        name = record[2]
        name = name - old_strings + new_strings if name else 0
        struct.pack_into('<5I', out, i * 20, record[0], name, record[4], record[5], record[6])
    out.extend(b[old_strings:])
    return out
