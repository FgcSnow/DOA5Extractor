from pathlib import Path
import io
import struct
import tempfile

MAX_RESOURCE = 1024 * 1024 * 1024


def decode_pc(raw):
    import numpy as np
    import zlib

    if len(raw) > MAX_RESOURCE:
        raise ValueError("PC resource exceeds the maximum size")
    if raw[:4] in (b"TMC\0", b"texc", b"dlic", b"dlci"):
        return bytes(raw)
    if len(raw) < 4:
        raise ValueError("Compressed PC resource is truncated")
    size = _u32(raw, 0)
    if not 0 < size <= MAX_RESOURCE:
        raise ValueError("Invalid decompressed PC resource size")
    from player_key import require_key
    XOR_PHRASE = require_key()
    value = ((size + 999) * 7 // 11) + (size % 17) + 428
    key = bytes((value >> shift) & 255 for shift in (24, 16, 8, 0)
                if (value >> shift) & 255)
    if not key:
        raise ValueError("Invalid PC resource key")
    period = bytes(value ^ key[index % len(key)]
                   for index, value in enumerate(XOR_PHRASE))
    buf = bytearray(raw[4:])
    arr = np.frombuffer(buf, dtype=np.uint8)
    mask = np.resize(np.frombuffer(period, dtype=np.uint8), len(arr))
    np.bitwise_xor(arr, mask, out=arr, where=(arr != 0) & (arr != mask))
    out = bytearray()
    position = 0
    while len(buf) - position > 6:
        value = _u32(buf, position)
        length = value & ~0x8000
        end = position + 4 + length
        if end > len(buf):
            raise ValueError("Compressed PC resource block is truncated")
        block = bytes(buf[position + 4:end])
        if value & 0x8000:
            if block[:2] != b"\x78\x9c":
                raise ValueError("Unknown PC compression signature")
            decoder = zlib.decompressobj(-zlib.MAX_WBITS)
            block = decoder.decompress(block[2:], size - len(out) + 1)
            if not decoder.eof or decoder.unconsumed_tail:
                raise ValueError("Invalid or oversized compressed PC resource block")
        out.extend(block)
        if len(out) > size:
            raise ValueError("PC resource exceeds its declared decompressed size")
        position = (end + 15) & ~15
    if len(out) != size:
        raise ValueError("PC resource decompressed size does not match")
    return bytes(out)


def _u32(data, offset):
    if offset < 0 or offset + 4 > len(data):
        raise ValueError("Alternate resource header is truncated")
    return struct.unpack_from("<I", data, offset)[0]


def _table_length(data):
    if len(data) < 768 or data[:8] != b"texch\0\0\0":
        raise ValueError("Alternate texture table is not a texch resource")
    table = _u32(data, 0x20)
    if _u32(data, 0x14) != 3 or table + 12 > len(data):
        raise ValueError("Unsupported alternate texture table layout")
    if struct.unpack_from("<3I", data, table) != (256, 512, 768):
        raise ValueError("Unsupported alternate texture table offsets")
    length = struct.unpack_from("<i", data, 264)[0]
    if not 0 < length <= 64:
        raise ValueError("Alternate texture count must be between 1 and 64")
    return length


def patch_pc_table(pc_h, ntex, mapping, alt_texture_count=None):
    _table_length(pc_h)
    if isinstance(ntex, bool) or not isinstance(ntex, int) or not 0 < ntex <= 64:
        raise ValueError("Target texture count must be between 1 and 64")
    if alt_texture_count is not None and (
        isinstance(alt_texture_count, bool)
        or not isinstance(alt_texture_count, int)
        or alt_texture_count <= 0
    ):
        raise ValueError("Invalid alternate texture count")
    values = [-1] * 64
    seen = set()
    for pair in mapping:
        if len(pair) != 2:
            raise ValueError("Texture mapping must contain pairs")
        index, alternate = pair
        if any(isinstance(value, bool) or not isinstance(value, int)
               for value in pair):
            raise ValueError("Texture mapping indices must be integers")
        if not 0 <= index < ntex or not 0 <= alternate < 64 or index in seen:
            raise ValueError("Invalid or duplicate alternate texture mapping")
        if alt_texture_count is not None and alternate >= alt_texture_count:
            raise ValueError("Alternate texture mapping exceeds the texture count")
        seen.add(index)
        values[index] = alternate
    out = bytearray(pc_h)
    struct.pack_into("<i", out, 264, ntex)
    struct.pack_into("<64i", out, 512, *values)
    return bytes(out)


def _dds_size(data):
    if len(data) < 128 or data[:4] != b"DDS " or _u32(data, 4) != 124:
        raise ValueError("Truncated or unsupported alternate DDS")
    width, height = _u32(data, 16), _u32(data, 12)
    mips = max(1, _u32(data, 28))
    if not width or not height or mips > 32:
        raise ValueError("Invalid alternate DDS dimensions")
    if _u32(data, 112) & (0xFE00 | 0x200000) or _u32(data, 24) > 1:
        raise ValueError("Cube and volume alternate textures are unsupported")
    fmt = data[84:88]
    if fmt == bytes(4):
        if _u32(data, 88) != 32:
            raise ValueError("Alternate raw DDS must contain 32-bit pixels")
        size = 128 + sum(max(1, width >> level) * max(1, height >> level) * 4
                         for level in range(mips))
    else:
        if fmt not in (b"DXT1", b"DXT3", b"DXT5", b"ATI1", b"ATI2"):
            raise ValueError("Unsupported alternate DDS format")
        unit = 8 if fmt in (b"DXT1", b"ATI1") else 16
        size = 128 + sum(max(1, ((width >> level) + 3) // 4)
                         * max(1, ((height >> level) + 3) // 4) * unit
                         for level in range(mips))
    if size > len(data):
        raise ValueError("Alternate DDS body is truncated")
    return size


def fix_alternate_normals(blob, *, temp, texconv=None):
    import numpy as np
    from PIL import Image
    from v2_normals import fix_dds, fix_raw

    out = bytearray(blob)
    cursor = 0
    corrected = 0
    with tempfile.TemporaryDirectory(prefix="v2-alternates-", dir=Path(temp)) as work:
        while True:
            start = out.find(b"DDS ", cursor)
            if start < 0:
                break
            size = _dds_size(out[start:])
            dds = bytes(out[start:start + size])
            pixels = np.array(Image.open(io.BytesIO(dds)).convert("RGBA")).astype(float)
            red, green, blue = (float(pixels[..., channel].mean()) for channel in range(3))
            if blue < 8 and 100 <= red <= 155 and 100 <= green <= 155:
                if dds[84:88] == bytes(4):
                    replacement, _ = fix_raw(dds)
                else:
                    if texconv is None:
                        raise ValueError("texconv is required for compressed alternate normal maps")
                    replacement, _ = fix_dds(dds, Path(work), Path(texconv))
                if len(replacement) != size:
                    raise ValueError("Alternate normal conversion changed the texture size")
                out[start:start + size] = replacement
                corrected += 1
            cursor = start + size
    return bytes(out), corrected


def convert_ps4_variant(decoded_h, decoded_hl, *, drop_indices=(), temp, texconv=None):
    from convert_model import textures

    _table_length(decoded_h)
    position = decoded_h.find(b"TTGM")
    if position < 768 or position + 48 > len(decoded_h):
        raise ValueError("PS4 alternate texture section is missing")
    section_size = _u32(decoded_h, position + 16)
    if section_size < 48 or position + section_size != len(decoded_h):
        raise ValueError("Invalid PS4 alternate texture section size")
    if len(decoded_hl) < 16:
        raise ValueError("PS4 alternate texture data is truncated")
    section, blob = textures(decoded_h[position:position + section_size], decoded_hl)
    out = bytearray(decoded_h[:position])
    out.extend(section)
    struct.pack_into("<I", out, 16, len(out))
    length = _table_length(out)
    seen = set()
    for index in drop_indices:
        if (isinstance(index, bool) or not isinstance(index, int)
                or not 0 <= index < length or index in seen):
            raise ValueError("Invalid or duplicate excluded alternate texture index")
        seen.add(index)
        if struct.unpack_from("<i", out, 512 + 4 * index)[0] < 0:
            raise ValueError("Excluded alternate texture index is already unused")
        struct.pack_into("<i", out, 512 + 4 * index, -1)
    fixed, _ = fix_alternate_normals(blob, temp=temp, texconv=texconv)
    active = [value for value in struct.unpack_from("<%di" % length, out, 512) if value != -1]
    if active and (min(active) < 0 or max(active) >= fixed.count(b"DDS ")):
        raise ValueError("Converted alternate texture mapping is out of range")
    return bytes(out), fixed
