import io
from pathlib import Path
import struct
import subprocess
import tempfile

import numpy as np
from PIL import Image


def _u(data, offset):
    if offset < 0 or offset + 4 > len(data):
        raise ValueError("Binary field out of bounds")
    return struct.unpack_from("<I", data, offset)[0]


def _slice(data, offset, size):
    if offset < 0 or size < 0 or offset + size > len(data):
        raise ValueError("Binary block out of bounds")
    return data[offset:offset + size]


def _children(data):
    count, table = _u(data, 24), _u(data, 32)
    if table > len(data) or count > (len(data) - table) // 4:
        raise ValueError("Invalid block table")
    return [_u(data, table + 4 * i) for i in range(count)]


def _sections(tmc):
    result = {}
    for offset in _children(tmc):
        if not offset:
            continue
        size = _u(tmc, offset + 16)
        tag = _slice(tmc, offset, 8).rstrip(b"\0").decode("ascii")
        if not tag and not size:
            continue
        block = _slice(tmc, offset, size)
        if len(block) < 20:
            raise ValueError("TMC section truncated")
        if tag in result:
            raise ValueError("Duplicate TMC section")
        result[tag] = block
    return result


def model_textures(tmc, tmcl):
    sections = _sections(tmc)
    try:
        ttdm = sections["TTDM"]
        lheader = sections["LHeader"]
    except KeyError as exc:
        raise ValueError("TMC texture sections missing") from exc
    texture_list = _slice(ttdm, _u(ttdm, 40), len(ttdm) - _u(ttdm, 40))
    bases = _children(lheader)
    if not bases:
        raise ValueError("Texture base missing")
    base, lengths = bases[0], _u(texture_list, 36)
    result = []
    for index, offset in enumerate(_children(texture_list)):
        size = _u(texture_list, lengths + 4 * index)
        at = base + offset
        _slice(tmcl, at, size)
        result.append((at, size))
    return result


def normal_indices(tmc):
    try:
        geometry = _sections(tmc)["MdlGeo"]
    except KeyError as exc:
        raise ValueError("MdlGeo section missing") from exc
    result = set()
    for offset in _children(geometry):
        obj = _slice(geometry, offset, _u(geometry, offset + 16))
        for child in _children(obj):
            entry = _slice(obj, child, len(obj) - child)
            count = _u(entry, 12)
            if count > max(0, (len(entry) - 16) // 4):
                raise ValueError("Invalid material texture table")
            for index in range(count):
                binding = _slice(entry, _u(entry, 16 + 4 * index), 16)
                _, kind, texture, _ = struct.unpack("<4I", binding)
                if kind == 1:
                    result.add(texture)
    return result


def _dds_header(dds):
    if len(dds) < 128 or dds[:4] != b"DDS " or _u(dds, 4) != 124:
        raise ValueError("Invalid DDS header")
    width, height = _u(dds, 16), _u(dds, 12)
    mips, depth = max(1, _u(dds, 28)), max(1, _u(dds, 24))
    if not width or not height or mips > 32:
        raise ValueError("Invalid DDS dimensions")
    return width, height, depth, mips


def _rgba(dds):
    with Image.open(io.BytesIO(dds)) as image:
        return np.array(image.convert("RGBA")).astype(np.float32)


def _blue_from_rg(red, green):
    x, y = red / 127.5 - 1.0, green / 127.5 - 1.0
    z = np.sqrt(np.clip(1.0 - x * x - y * y, 0.0, 1.0))
    return np.clip(np.round((z + 1.0) * 127.5), 0, 255)


def fix_dds(dds: bytes, work: Path, texconv: Path) -> tuple[bytes, float]:
    width, height, depth, mips = _dds_header(dds)
    fmt = dds[84:88]
    if fmt not in (b"DXT1", b"DXT5"):
        raise ValueError("Unsupported compressed DDS format")
    if _u(dds, 112) & 0xFE00 or depth != 1:
        raise ValueError("2D conversion does not take cube or volume textures")
    rgba = _rgba(dds)
    rgba[..., 2] = _blue_from_rg(rgba[..., 0], rgba[..., 1])
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="normal-", dir=work) as temporary:
        folder = Path(temporary)
        source, output = folder / "n.png", folder / "o"
        output.mkdir()
        Image.fromarray(rgba.astype(np.uint8), "RGBA").save(source)
        proc = subprocess.run(
            [str(texconv), "-nologo", "-y", "-f",
             "BC1_UNORM" if fmt == b"DXT1" else "BC3_UNORM",
             "-m", str(mips), "-o", str(output), str(source)],
            capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode:
            raise RuntimeError("Normal map conversion: " + proc.stdout + proc.stderr)
        generated = output / "n.DDS"
        if not generated.is_file():
            generated = output / "n.dds"
        new = bytearray(generated.read_bytes())
    if (new[84:88] != fmt or _u(new, 16) != width
            or _u(new, 12) != height or max(1, _u(new, 28)) != mips
            or len(new) != len(dds)):
        raise ValueError("Normal map format or size changed")
    new[:128] = dds[:128]
    check = _rgba(bytes(new))
    drift = float(np.abs(check[..., :2] - rgba[..., :2]).mean())
    if check[..., 2].mean() <= 150 or drift >= 6:
        raise ValueError("Converted normal map fails the channel check")
    return bytes(new), drift


def fix_raw(dds: bytes) -> tuple[bytes, float]:
    width, height, depth, mips = _dds_header(dds)
    if dds[84:88] != bytes(4) or _u(dds, 88) != 32:
        raise ValueError("Raw normal map must be a 32-bit RGBA DDS")
    red_mask, green_mask, blue_mask = (_u(dds, at) for at in (92, 96, 100))
    masks = (red_mask, green_mask, blue_mask)
    if any(not mask or mask >> ((mask & -mask).bit_length() - 1) != 255
           for mask in masks):
        raise ValueError("8-bit DDS masks expected")
    if red_mask & green_mask or red_mask & blue_mask or green_mask & blue_mask:
        raise ValueError("Overlapping DDS masks")
    shift = lambda mask: (mask & -mask).bit_length() - 1
    out, position = bytearray(dds), 128
    for level in range(mips):
        size = (max(1, width >> level) * max(1, height >> level)
                * max(1, depth >> level) * 4)
        pixels = np.frombuffer(_slice(out, position, size), dtype="<u4").copy()
        red = ((pixels & red_mask) >> shift(red_mask)).astype(np.float32)
        green = ((pixels & green_mask) >> shift(green_mask)).astype(np.float32)
        blue = _blue_from_rg(red, green).astype(np.uint32)
        pixels = (pixels & ~np.uint32(blue_mask)) | (blue << np.uint32(shift(blue_mask)))
        out[position:position + size] = pixels.astype("<u4").tobytes()
        position += size
    if position != len(dds):
        raise ValueError("Raw normal map size mismatch")
    return bytes(out), 0.0


def _slice_header(dds, width, height, size):
    header = bytearray(dds[:128])
    for offset, value in ((12, height), (16, width), (20, size), (24, 0),
                          (28, 1), (8, _u(header, 8) & ~(0x20000 | 0x800000)),
                          (108, 0x1000), (112, 0)):
        struct.pack_into("<I", header, offset, value)
    return header


def fix_volume(dds: bytes, work: Path, texconv: Path) -> tuple[bytes, float]:
    width, height, depth, mips = _dds_header(dds)
    fmt = dds[84:88]
    if fmt not in (b"DXT1", b"DXT5") or not _u(dds, 112) & 0x200000:
        raise ValueError("BC1 or BC3 DDS volume expected")
    unit = 8 if fmt == b"DXT1" else 16
    out, position, drift = bytearray(dds), 128, 0.0
    for level in range(mips):
        w, h, d = (max(1, value >> level) for value in (width, height, depth))
        size = max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * unit
        for _ in range(d):
            block = _slice(dds, position, size)
            if w >= 4 and h >= 4:
                header = _slice_header(dds, w, h, size)
                repaired, error = fix_dds(bytes(header) + block, work, texconv)
                out[position:position + size] = repaired[128:]
                drift = max(drift, error)
            position += size
    if position != len(dds):
        raise ValueError("DDS volume size mismatch")
    return bytes(out), drift


def _historical_candidate(dds):
    width, height, depth, _ = _dds_header(dds)
    if _u(dds, 112) & 0x200000 and depth > 1:
        unit = 8 if dds[84:88] == b"DXT1" else 16
        size = max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * unit
        header = bytearray(dds[:128])
        for offset, value in ((24, 0), (28, 1), (112, 0),
                              (8, _u(header, 8) & ~(0x20000 | 0x800000)),
                              (108, 0x1000)):
            struct.pack_into("<I", header, offset, value)
        preview = bytes(header) + dds[128:128 + size]
    else:
        preview = dds
    try:
        rgba = _rgba(preview)
    except (OSError, ValueError, NotImplementedError):
        return False
    red, green, blue = (float(rgba[..., channel].mean()) for channel in range(3))
    return blue < 8 and 100 <= red <= 155 and 100 <= green <= 155


def fix_normals(
    tmc: bytes, tmcl: bytes, texconv: Path, work: Path,
    indices: list[int] | None = None,
) -> tuple[bytes, bytes]:
    textures, normals = model_textures(tmc, tmcl), normal_indices(tmc)
    if any(index >= len(textures) for index in normals):
        raise ValueError("Normal map index outside the texture table")
    automatic = indices is None
    if automatic:
        selected = sorted(normals)
    else:
        if (any(type(index) is not int for index in indices)
                or len(set(indices)) != len(indices)):
            raise ValueError("Invalid normal map index list")
        if any(index not in normals for index in indices):
            raise ValueError("Requested index is not a normal map")
        selected = sorted(indices)
    out = bytearray(tmcl)
    for index in selected:
        offset, size = textures[index]
        dds = bytes(tmcl[offset:offset + size])
        if automatic and not _historical_candidate(dds):
            continue
        try:
            if dds[84:88] == bytes(4):
                repaired, _ = fix_raw(dds)
            elif _u(dds, 112) & 0x200000 and _u(dds, 24) > 1:
                repaired, _ = fix_volume(dds, Path(work), Path(texconv))
            else:
                repaired, _ = fix_dds(dds, Path(work), Path(texconv))
        except ValueError:
            if automatic:
                continue
            raise
        if len(repaired) != size or repaired[:128] != dds[:128]:
            raise ValueError("The fix changed the texture structure")
        out[offset:offset + size] = repaired
    return bytes(tmc), bytes(out)
