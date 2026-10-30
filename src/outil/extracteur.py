"""DOA5LR local PS4 resource extractor. No downloads and no game assets embedded."""
import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath

HOME = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
sys.path.insert(0, str(HOME / "converters"))
RECIPE = HOME / "recette.json"
MAX_RESOURCE = 1024 * 1024 * 1024
PACK_FORMAT = "doa5pack/1"
PACK_NAME = "DOA5LR PS4 stages"
OLD_PACK_NAMES = ("DOA5LR PS4 stages - prototype 0.2 (partiel)",)
GROUP_NAMES = {"costumes-classiques": "classic-costumes", "costumes-particules": "costume-particles"}

def shown(text):
    for internal, name in GROUP_NAMES.items():
        text = text.replace(internal, name)
    return text

def internal_groups(groups):
    names = {name: internal for internal, name in GROUP_NAMES.items()}
    return [names.get(g, g) for g in groups]

def digest(data):
    return hashlib.sha256(data).hexdigest()

def file_hash(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()

def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))

def safe_name(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("Invalid ZIP path")
    parts = value.split("/")
    if any(p in ("", ".", "..") or p.endswith((" ", ".")) or
           re.search(r'[<>:"|?*\x00-\x1f]', p) or
           re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", p, re.I)
           for p in parts):
        raise ValueError("Path not allowed: " + value)
    return value

def no_link(path):
    p = Path(path).absolute()
    for part in [p, *p.parents]:
        if part.exists() and (part.is_symlink() or getattr(part, "is_junction", lambda: False)()):
            raise ValueError("Link or junction not allowed: " + str(part))

def under(root, relative):
    safe_name(relative)
    root = Path(root).absolute()
    result = root.joinpath(*relative.split("/"))
    no_link(result)
    if not result.resolve().is_relative_to(root.resolve()):
        raise ValueError("Path outside the destination")
    return result

def read_recipe(path=None):
    recipe = load(path or RECIPE)
    if recipe["format"] != 1:
        raise ValueError("Unknown recipe format")
    seen = set()
    for job in recipe["jobs"]:
        for out in job["outputs"]:
            key = safe_name(out["path"]).casefold()
            if key in seen:
                raise ValueError("Duplicate output in the recipe")
            seen.add(key)
    return recipe

class Sources:
    def __init__(self, roots, wanted, log=print):
        self.rows = {}
        self.files = {}
        self.wanted = wanted
        self.log = log
        names = {r["catalog"].casefold() for r in wanted.values() if "file" not in r}
        files = {r["file"].casefold() for r in wanted.values() if "file" in r}
        visited = set()
        for root in roots:
            root = Path(root)
            if not root.is_dir():
                raise ValueError("Source folder not found: " + str(root))
            for p in root.rglob("*"):
                name = p.name.casefold()
                if (name not in names and name not in files) or not p.is_file():
                    continue
                no_link(p)
                if p.resolve() in visited:
                    continue
                visited.add(p.resolve())
                if name in files:
                    self.files.setdefault(name, []).append(p)
                if name in names:
                    self.index(p)
        log(f"{len(visited)} source file{'' if len(visited) == 1 else 's'} scanned")

    def index(self, p):
        if p.stat().st_size > 32 * 1024 * 1024:
            return
        b = p.read_bytes()
        if len(b) < 28 or b[:4] != b"LFMO":
            return
        _, n, _, table, _, _ = struct.unpack_from("<6I", b, 4)
        if n > 500000 or table + n * 12 > len(b):
            raise ValueError("Truncated catalog: " + str(p))
        archive = p.with_suffix(".lnk")
        if not archive.is_file():
            return
        no_link(archive)
        total = archive.stat().st_size
        with archive.open("rb") as f:
            header = f.read(32)
            if len(header) != 32:
                raise ValueError("Truncated archive")
            count = struct.unpack_from("<Q", header, 8)[0]
            if count > 500000 or 32 + count * 32 > total:
                raise ValueError("Invalid archive table")
            locations = f.read(count * 32)
        for i in range(n):
            _, index, at = struct.unpack_from("<3I", b, table + i * 12)
            if index >= count or at >= len(b):
                raise ValueError("Invalid catalog index")
            end = b.find(b"\0", at)
            if end < 0:
                raise ValueError("Truncated catalog name")
            name = b[at:end].decode("ascii").lstrip("/")
            offset, size, _, _ = struct.unpack_from("<4Q", locations, index * 32)
            if offset + size > total or size > MAX_RESOURCE:
                raise ValueError("Resource outside the archive")
            self.rows.setdefault((p.name.casefold(), name), []).append((archive, offset, size))

    def read(self, key):
        return self.find(key)[1]

    def find(self, key):
        spec = self.wanted[key]
        if "file" in spec:
            for p in self.files.get(spec["file"].casefold(), []):
                if p.stat().st_size == spec["size"]:
                    data = p.read_bytes()
                    if digest(data) == spec["sha256"]:
                        return p, data
            raise ValueError("Source file missing or different: " + spec["file"])
        rows = self.rows.get((spec["catalog"].casefold(), spec["name"]), [])
        for archive, offset, size in rows:
            if size != spec["size"]:
                continue
            with archive.open("rb") as f:
                f.seek(offset)
                data = f.read(size)
            if digest(data) == spec["sha256"]:
                return archive, data
        raise ValueError("Source missing or different: " +
                         spec["catalog"] + " / " + spec["name"])

def decode(raw):
    import numpy as np
    from player_key import require_key
    XOR_PHRASE = require_key()
    if len(raw) < 4:
        raise ValueError("Truncated compressed resource")
    size = struct.unpack_from("<I", raw)[0]
    if size <= 0 or size > MAX_RESOURCE:
        raise ValueError("Invalid unpacked size")
    key = bytes(v for v in struct.pack(">I", (size * 119 // 11) & 0xffffffff) if v)
    if not key:
        raise ValueError("Invalid resource key")
    period = bytes(XOR_PHRASE[i % len(XOR_PHRASE)] ^ key[i % len(key)]
                   for i in range(math.lcm(len(XOR_PHRASE), len(key))))
    buf = bytearray(raw[4:])
    arr = np.frombuffer(buf, dtype=np.uint8)
    mask = np.resize(np.frombuffer(period, dtype=np.uint8), len(arr))
    np.bitwise_xor(arr, mask, out=arr, where=(arr != 0) & (arr != mask))
    import zlib
    out = bytearray()
    at = 0
    while len(buf) - at > 6:
        value = struct.unpack_from("<I", buf, at)[0]
        n = value & ~0x8000
        if at + 4 + n > len(buf):
            raise ValueError("Truncated compressed block")
        data = bytes(buf[at + 4:at + 4 + n])
        if value & 0x8000:
            if data[:2] != b"\x78\x9c":
                raise ValueError("Unknown compression signature")
            dec = zlib.decompressobj(-zlib.MAX_WBITS)
            data = dec.decompress(data[2:], size - len(out) + 1)
            if not dec.eof or dec.unconsumed_tail:
                raise ValueError("Invalid or oversized unpacked block")
        out.extend(data)
        if len(out) > size:
            raise ValueError("Size overflow")
        at = (at + 4 + n + 15) & ~15
    if len(out) != size:
        raise ValueError("Unpacked size differs")
    return bytes(out)

def model_data(raw):
    return raw if raw[:4] in (b"TMC\0", b"texc", b"dlic", b"dlci") else decode(raw)

def normalize_variant(a, external, temp, texconv):
    from convert_structures import u, put, children
    if not texconv or not Path(texconv).is_file():
        raise ValueError("texconv.exe is required for v2 variants")
    a = bytearray(a)
    pos = a.find(b"TTDM")
    t = a[pos:]
    l = t[u(t, 40):]
    lp = pos + u(t, 40)
    out = bytearray(external[:64])
    cache = Path(temp) / "dds-cache"
    compressed = cache / "compressed"
    compressed.mkdir(parents=True, exist_ok=True)
    for i, o in enumerate(children(l)):
        n = u(l, u(l, 36) + 4*i)
        b = bytearray(external[o:o+n])
        if b[84:88] == bytes(4) and len(b) >= 262144:
            name = digest(b) + ".dds"
            src, dest = cache / name, compressed / name
            if not dest.exists():
                src.write_bytes(b)
                result = subprocess.run([str(texconv), "-nologo", "-y", "-f", "BC3_UNORM",
                    "-bc", "u", "-m", str(max(1, u(b, 28))), "-o", str(compressed), str(src)],
                    capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if result.returncode:
                    raise ValueError("texconv: " + result.stdout + result.stderr)
            b = bytearray(dest.read_bytes())
        if u(b, 112) == 0x200000:
            if max(1, u(b, 28)) == 1:
                put(b, 8, u(b, 8) & ~0x20000)
            if b[84:88] in (b"DXT1", b"DXT3", b"DXT5"):
                unit = 8 if b[84:88] == b"DXT1" else 16
                put(b, 20, max(1, (u(b,16)+3)//4)*max(1, (u(b,12)+3)//4)*unit*max(1,u(b,24)))
        elif u(b, 24) == 1:
            put(b, 24, 0)
        put(a, lp + u(l,32) + 4*i, len(out))
        put(a, lp + u(l,36) + 4*i, len(b))
        out.extend(b)
        out.extend(bytes((-len(out)) % 64))
    put(out, 4, len(out))
    put(a, lp + 68, len(out))
    return [bytes(a), bytes(out)]

def make_archive(job, generated, output_dir=None):
    number = job["archive_id"]
    resources = job["resources"]
    body = bytearray()
    total = 0
    for r in job["costumes"]:
        pairs = r["hair_face_pairs"]
        body.extend(bytes([r["character_id"], r["dlcu"], r["details"], len(pairs)] +
                          [n for pair in pairs for n in pair]))
        total += r["character_id"] * r["dlcu"]
    checksum = (((total & 4095) * (number+1)) & 0xffffffff) % (9*number+17)
    bcm = struct.pack("<BBHI", 9, 0, len(job["costumes"]), checksum) + body
    header = bytearray(32 + 32*len(resources))
    header[:4] = b"PCDM"
    struct.pack_into("<Q", header, 8, len(resources))
    struct.pack_into("<Q", header, 24, 2048)
    cat = bytearray(40 + 12*len(resources))
    cat[:4] = b"LFMO"
    struct.pack_into("<6I", cat, 4, 1, len(resources), 32, 40, len(cat), 0)
    struct.pack_into("<2I", cat, 32, 0, len(cat))
    cat.extend(f"_output/costume_pack_{number}.order".encode() + b"\0")
    struct.pack_into("<I", cat, 24, len(cat))
    blp = bytearray(struct.pack("<4s3I", b"1PIF", len(resources), 16, 16+20*len(resources)))
    lnk_path = None
    if output_dir is not None:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        lnk_path = under(output_dir, str(number) + ".lnk")
    with (lnk_path.open("x+b") if lnk_path else io.BytesIO()) as lnk:
        lnk.write(header)
        lnk.write(bytes((-lnk.tell()) % 2048))
        for i, r in enumerate(resources):
            source = under(generated, r["path"])
            size = source.stat().st_size
            struct.pack_into("<4Q", header, 32+i*32, lnk.tell(), size, size, 0)
            with source.open("rb") as inp:
                shutil.copyfileobj(inp, lnk, 1024 * 1024)
            lnk.write(bytes((-lnk.tell()) % 2048))
            struct.pack_into("<3I", cat, 40+i*12, 0, i, len(cat))
            cat.extend(b"/" + r["asset_name"].encode("ascii") + b"\0")
            blp.extend(struct.pack("<5I", r["asset_id"], size, 0, 0, 0))
        struct.pack_into("<Q", header, 16, lnk.tell())
        lnk.seek(0)
        lnk.write(header)
        result = lnk_path if lnk_path else lnk.getvalue()
    return [bcm, cat, blp, result]

def particules_commun(job, sources, roots, dossier):
    import common_lnk
    try:
        archive_pc, tmc_pc = sources.find(job["inputs"][0])
        bruts = [tmc_pc] + [sources.read(k) for k in job["inputs"][1:]]
    except ValueError:
        if common_lnk.deja_modifie(roots):
            raise ValueError("PC common.lnk is already the costume particles one: nothing to do") from None
        raise
    return common_lnk.construire(archive_pc, bruts[:2], [decode(b) for b in bruts[2:]], dossier)

def convert(op, inputs, temp, texconv=None, options=None):
    import scene2
    from convert_model import convert_model
    from convert_animation import convert_animation
    from convert_structures import u
    from bindings import bindings
    options = options or {}
    if op == "copy":
        return inputs
    if op in ("stage_thumbs", "stage_layout", "stage_raw8"):
        import stage_menu
        if op == "stage_thumbs":
            return [stage_menu.vignettes(*inputs)]
        if op == "stage_layout":
            return [stage_menu.mise_en_page(inputs[0])]
        return [stage_menu.raw8(inputs[0], inputs[1], options["texture"])]
    if op == "stage_audio":
        import stage_audio
        return [stage_audio.banque_stage(inputs, HOME / "tools" / "libatrac9.dll", options.get("append_program"))]
    if op == "dnz_preview":
        import dnz_preview
        return [dnz_preview.apercu(inputs[0])]
    if op == "extrastages_d5sd":
        import extrastages_d5sd
        eboot, son_ps4, son_pc, titan, azuchi1, fireworks, azuchi2, undd, xws1, xws2 = inputs
        return [extrastages_d5sd.construire(eboot, son_ps4, son_pc, {"TITAN": titan, "AZUCHI1": azuchi1,
                                            "FIREWORKS": fireworks, "AZUCHI2": azuchi2}, undd, xws1, xws2)]
    if op == "crimson_vfx":
        import crimson_vfx
        import softengine_lr
        return [crimson_vfx.d5sd(softengine_lr.image(inputs[0]))]
    if op == "softengine_lr":
        import softengine_lr
        return [softengine_lr.donnees(inputs[0])]
    if op in ("pc_copy_decoded", "pc_table_patch"):
        from v2_alternates import decode_pc, patch_pc_table
        decoded = [decode_pc(data) for data in inputs]
        if op == "pc_copy_decoded":
            return decoded
        if len(decoded) != 1:
            raise ValueError("One source required for the PC texture table")
        return [patch_pc_table(decoded[0], options["texture_count"], options["texture_map"])]
    if op == "variant_normalfix":
        from v2_alternates import convert_ps4_variant
        if len(inputs) != 2:
            raise ValueError("Two sources required for the PS4 variant")
        return list(convert_ps4_variant(model_data(inputs[0]), model_data(inputs[1]),
                    drop_indices=options.get("drop_texture_indices", []),
                    temp=Path(temp), texconv=texconv))
    if op in ("model", "model_normalized", "model_compressed", "model_normalized_normalfix"):
        a, b = convert_model(model_data(inputs[0]), model_data(inputs[1]))
        if op != "model":
            import normalize
            if not texconv or not Path(texconv).is_file():
                raise ValueError("texconv.exe is required for v2 costumes")
            normalize.TEXCONV = Path(texconv)
            normalize.CACHE = Path(temp) / "dds-cache"
            normalize.COMP = normalize.CACHE / "compressed"
            normalize.COMP.mkdir(parents=True, exist_ok=True)
            a, b, _ = normalize.normalize(a, b, headers=op != "model_compressed")
        if op == "model_normalized_normalfix" or options.get("normal_repair") or "normal_indices" in options:
            if not texconv or not Path(texconv).is_file():
                raise ValueError("texconv.exe is required for v2 normal maps")
            from v2_normals import fix_normals
            a, b = fix_normals(bytes(a), bytes(b), Path(texconv),
                               Path(temp) / "normal-repair", options.get("normal_indices"))
        return [bytes(a), bytes(b)]
    if op == "scene":
        return [bytes(x) for x in scene2.convert_scene2(*inputs)]
    if op == "animation":
        b = inputs[0]
        return [bytes(convert_animation(b)) if any(u(b, u(b, 32) + 4*i)
                   for i in range(u(b, 20))) else b]
    if op == "bindings":
        return [bytes(bindings(inputs[0]))]
    if op == "camera":
        from camera import convert_camera
        return [convert_camera(inputs[0])]
    if op == "scene_script":
        from scene47 import convert as convert_script
        return [convert_script(inputs[0])]
    if op == "config":
        from convert_structures import put
        data = bytearray(model_data(inputs[0]))
        if len(data) < 80 or u(data,16) != len(data) or data[:8] != b"texch\0\0\0" or u(data, 20) != 2:
            raise ValueError("Unknown costume configuration")
        put(data, 20, 1)
        return [bytes(data)]
    if op in ("icon", "variant", "variant_normalized"):
        from convert_structures import children, put
        from convert_model import textures
        h = model_data(inputs[0])
        if op == "icon":
            start, top = children(h)
            tex, blob = textures(h[top:top+u(h, top+16)], h[start:top])
            out = bytearray(h[:start])
            out.extend(blob)
            out.extend(bytes((-len(out)) % 4096))
            put(out, 52, len(out))
            out.extend(tex)
            put(out, 16, len(out))
            return [bytes(out)]
        hl = model_data(inputs[1])
        pos = h.find(b"TTGM")
        if pos < 0:
            raise ValueError("TTGM missing")
        tex, blob = textures(h[pos:pos+u(h,pos+16)], hl)
        out = bytearray(h[:pos])
        out.extend(tex)
        put(out, 16, len(out))
        return [bytes(out), bytes(blob)] if op == "variant" else normalize_variant(out, blob, temp, texconv)
    raise ValueError("Unknown conversion: " + op)

def choose(recipe, groups):
    all_groups = {j["group"] for j in recipe["jobs"]}
    if groups == ["all"]:
        groups = sorted(all_groups)
    groups = internal_groups(groups)
    unknown = set(groups) - all_groups
    if unknown:
        raise ValueError("Unknown group: " + shown(", ".join(sorted(unknown))))
    return [j for j in recipe["jobs"] if j["group"] in groups]

def manifest(recipe, jobs, recipe_path=None):
    return {"format": 1, "recipe_id": recipe["id"], "recipe_sha256": file_hash(recipe_path or RECIPE),
            "jobs": sorted(j["id"] for j in jobs),
            "installable": all(j["installable"] for j in jobs),
            "files": sorted([o for j in jobs if not j.get("intermediate") for o in j["outputs"]], key=lambda o: o["path"])}

def installer_manifest(info, pack_name=PACK_NAME):
    if not info["installable"]:
        raise ValueError("The DOA5Installer stage format takes stages only: make costumes in the extractor format.")
    files = info["files"]
    if not files or len(files) + 1 > 4096:
        raise ValueError("Too many files for DOA5Installer")
    forbidden = (".asi", ".dll", ".exe", ".com", ".scr", ".bat", ".cmd", ".ps1", ".vbs",
                 ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta", ".msi", ".lnk", ".url",
                 ".reg", ".ini", ".sys", ".cpl")
    for spec in files:
        name = safe_name(spec["path"])
        if (not name.startswith(("maps/", "DOA5LR-StageSlots/")) or len(name) > 200
                or any(ord(c) < 32 or ord(c) > 126 for c in name)
                or name.lower().endswith(forbidden) or spec["size"] > 512 * 1024 * 1024
                or any(re.match(r"^(COM|LPT)(?:[0-9])?(?:\.|$)", part, re.I)
                       for part in name.split("/"))):
            raise ValueError("File not accepted by DOA5Installer: " + name)
    result = {"format": PACK_FORMAT, "name": pack_name,
              "files": {o["path"]: {"size": o["size"], "sha256": o["sha256"]} for o in files},
              "extractor": info}
    payload = json.dumps(result, sort_keys=True, indent=2).encode("ascii")
    if len(payload) > 1024 * 1024:
        raise ValueError("Manifest too large for DOA5Installer")
    upper_size = sum(o["size"] + 256 + 2 * len(o["path"]) for o in files) + len(payload) + 1024
    if upper_size >= 0xffffffff:
        raise ValueError("ZIP too large for DOA5Installer")
    return result

def zip_add(z, name, data):
    info = zipfile.ZipInfo(safe_name(name), (1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = 0o100644 << 16
    if isinstance(data, Path):
        info.file_size = data.stat().st_size
        with data.open("rb") as inp, z.open(info, "w") as out:
            shutil.copyfileobj(inp, out, 1024 * 1024)
    else:
        z.writestr(info, data)

def build(roots, groups, destination, texconv=None, log=print, pack_format="extractor", key_file=None):
    from player_key import key_context
    with key_context(key_file):
        return _build(roots, groups, destination, texconv, log, pack_format)

ZIP_SET = (("stages", "DOA5MissingContent.zip", "installer"),
           ("costumes", "DOA5LR-PS4-Costumes.zip", "extractor"),
           ("particles", "DOA5LR-PS4-Costume-Particles.zip", "extractor"))

def split_groups(groups):
    parts = {"stages": [], "costumes": [], "particles": []}
    for g in groups:
        kind = "particles" if g == "costumes-particules" else "costumes" if g.startswith("costumes-") else "stages"
        parts[kind].append(g)
    return {k: v for k, v in parts.items() if v}

def build_set(roots, groups, folder, texconv=None, log=print, key_file=None, replace=False):
    from player_key import KeyRequired
    recipe = read_recipe()
    if groups == ["all"]:
        groups = sorted({j["group"] for j in recipe["jobs"]})
    groups = internal_groups(groups)
    choose(recipe, groups)
    parts = split_groups(groups)
    if not parts:
        raise ValueError("No content selected")
    folder = Path(folder).absolute()
    no_link(folder)
    folder.mkdir(parents=True, exist_ok=True)
    made, failed = [], []
    for kind, name, pack_format in ZIP_SET:
        if kind not in parts:
            continue
        target = folder / name
        log(f"--- {name}")
        if target.exists() and not replace:
            failed.append(name + ": already in this folder")
            log(failed[-1])
            continue
        part = folder / (name + ".part")
        try:
            if part.exists():
                no_link(part)
                part.unlink()
            build(roots, parts[kind], part, texconv, log, pack_format, key_file)
            os.replace(part, target)
            made.append(name)
        except KeyRequired:
            raise
        except (ValueError, OSError) as exc:
            failed.append(f"{name}: {exc}")
            log(name + " not made: " + str(exc))
    for name in made:
        log("Ready: " + str(folder / name))
    if failed:
        raise ValueError(("Made: " + ", ".join(made) + ". " if made else "") + "Not made: " + " | ".join(failed))
    return [folder / name for name in made]


def _build(roots, groups, destination, texconv=None, log=print, pack_format="extractor"):
    recipe = read_recipe()
    jobs = choose(recipe, groups)
    if pack_format not in ("extractor", "installer"):
        raise ValueError("Unknown output format")
    info = manifest(recipe, jobs)
    output_manifest = installer_manifest(info) if pack_format == "installer" else info
    if texconv is None and (HOME / "tools/texconv.exe").is_file():
        texconv = HOME / "tools/texconv.exe"
    if not jobs:
        raise ValueError("No group selected")
    if len(jobs) > 1 and any(j["operation"] == "common_lnk" for j in jobs):
        raise ValueError("Costume particles: make a separate ZIP (PS4 particles card of DOA5Installer, or Import)")
    target = Path(destination).absolute()
    no_link(target)
    if target.exists():
        raise ValueError("Output already exists: " + str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    wanted = {k: recipe["sources"][k] for j in jobs for k in j["inputs"]}
    sources = Sources(roots, wanted, log)
    with tempfile.TemporaryDirectory(prefix="doa5lr-extract-", dir=target.parent) as temp:
        archive = Path(temp) / "content.zip"
        generated = Path(temp) / "generated"
        with zipfile.ZipFile(archive, "x", allowZip64=pack_format != "installer") as z:
            for n, job in enumerate(jobs, 1):
                log(f"[{n}/{len(jobs)}] {shown(job['id'])}")
                if job["operation"] == "archive":
                    results = make_archive(job, generated, Path(temp) / "archives")
                elif job["operation"] == "common_lnk":
                    results = [particules_commun(job, sources, roots, Path(temp) / "common")]
                else:
                    results = convert(job["operation"], [sources.read(k) for k in job["inputs"]], temp, texconv, job)
                if len(results) != len(job["outputs"]):
                    raise ValueError("Wrong number of outputs")
                for data, spec in zip(results, job["outputs"]):
                    size = data.stat().st_size if isinstance(data, Path) else len(data)
                    checksum = file_hash(data) if isinstance(data, Path) else digest(data)
                    if size != spec["size"] or checksum != spec["sha256"]:
                        raise ValueError("Conversion differs from the reference: " + spec["path"])
                    if job.get("intermediate"):
                        staged = under(generated, spec["path"])
                        staged.parent.mkdir(parents=True, exist_ok=True)
                        staged.write_bytes(data)
                    else:
                        zip_add(z, spec["path"], data)
            zip_add(z, "manifest.json", json.dumps(output_manifest, sort_keys=True,
                                                   indent=2).encode("utf-8"))
        verify(archive, log=lambda _: None)
        if target.exists():
            raise ValueError("Output was just created by another process")
        archive.rename(target)
    log("ZIP verified: " + str(target))
    return info

def export_installer(source_zip, destination, log=print):
    target = Path(destination).absolute()
    no_link(target)
    if target.exists():
        raise ValueError("Output already exists: " + str(target))
    with zipfile.ZipFile(source_zip) as source:
        info = verified_zip(source)
        output_manifest = installer_manifest(info)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="doa5lr-installer-", dir=target.parent) as temp:
            archive = Path(temp) / "content.zip"
            with zipfile.ZipFile(archive, "x", allowZip64=False) as out:
                for spec in info["files"]:
                    entry = zipfile.ZipInfo(spec["path"], (1980, 1, 1, 0, 0, 0))
                    entry.compress_type = zipfile.ZIP_STORED
                    entry.external_attr = 0o100644 << 16
                    entry.file_size = spec["size"]
                    with source.open(spec["path"]) as inp, out.open(entry, "w") as dst:
                        shutil.copyfileobj(inp, dst)
                zip_add(out, "manifest.json", json.dumps(output_manifest, sort_keys=True, indent=2).encode("ascii"))
            verify(archive, log=lambda _: None)
            if target.exists():
                raise ValueError("Output was just created by another process")
            archive.rename(target)
    log("DOA5Installer ZIP verified: " + str(target))
    return info

def check_installer_transport(z):
    if z.getinfo("manifest.json").file_size > 1024 * 1024:
        raise ValueError("Manifest too large for DOA5Installer")
    for info in z.infolist():
        if (info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                or info.extract_version >= 45
                or info.file_size > 512 * 1024 * 1024
                or info.compress_size > 512 * 1024 * 1024
                or info.header_offset >= 0xffffffff):
            raise ValueError("Unsupported compression or ZIP64 for DOA5Installer: " + info.filename)
    stream = z.fp
    position = stream.tell()
    try:
        stream.seek(0, 2)
        size = stream.tell()
        if size >= 0xffffffff:
            raise ValueError("ZIP too large for DOA5Installer")
        stream.seek(max(0, size - 65557))
        tail = stream.read()
        at = tail.rfind(b"PK\x05\x06")
        if at < 0 or at + 22 > len(tail):
            raise ValueError("Invalid end of ZIP")
        disk, directory_disk, disk_count, count, directory_size, offset, comment = struct.unpack_from(
            "<4H2IH", tail, at + 4)
        if (disk or directory_disk or disk_count != count or count != len(z.infolist())
                or count == 0xffff or offset == 0xffffffff or directory_size == 0xffffffff
                or at + 22 + comment != len(tail)
                or offset + directory_size != size - 22 - comment):
            raise ValueError("ZIP64, multi-disk or other ZIP structure not supported by DOA5Installer")
    finally:
        stream.seek(position)

def verified_zip(z):
    infos = z.infolist()
    names = [safe_name(i.filename) for i in infos]
    if len({n.casefold() for n in names}) != len(names):
        raise ValueError("Duplicate ZIP entries")
    if "manifest.json" not in names:
        raise ValueError("Manifest missing")
    if z.getinfo("manifest.json").file_size > 2 * 1024 * 1024:
        raise ValueError("Manifest too large")
    given = json.loads(z.read("manifest.json"))
    if not isinstance(given, dict):
        raise ValueError("Invalid manifest")
    is_pack = given.get("format") == PACK_FORMAT
    if is_pack:
        check_installer_transport(z)
    reference = given.get("extractor") if is_pack else given
    if not isinstance(reference, dict):
        raise ValueError("Extractor metadata missing")
    candidates = [RECIPE, *sorted((HOME / "recipes").glob("*.json"))]
    recipe_path = next((p for p in candidates if file_hash(p) == reference.get("recipe_sha256")), None)
    if recipe_path is None:
        raise ValueError("Unknown recipe: this ZIP does not match the bundled recipes")
    recipe = read_recipe(recipe_path)
    ids = reference.get("jobs", [])
    if (not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids)
            or len(ids) != len(set(ids))):
        raise ValueError("Invalid job list")
    jobs = [j for j in recipe["jobs"] if j["id"] in ids]
    expected = manifest(recipe, jobs, recipe_path)
    name = given.get("name") if given.get("name") in OLD_PACK_NAMES else PACK_NAME
    if given != (installer_manifest(expected, name) if is_pack else expected):
        raise ValueError("Manifest differs from the trusted recipe")
    files = {o["path"]: o for o in expected["files"]}
    if set(names) != set(files) | {"manifest.json"}:
        raise ValueError("Missing or unknown files in the ZIP")
    for info in infos:
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode) or info.is_dir() or info.flag_bits & 1:
            raise ValueError("ZIP entry not allowed")
        if info.filename == "manifest.json":
            continue
        spec = files[info.filename]
        if info.file_size != spec["size"]:
            raise ValueError("Wrong size: " + info.filename)
        with z.open(info) as f:
            h = hashlib.file_digest(f, "sha256").hexdigest()
        if h != spec["sha256"]:
            raise ValueError("Wrong SHA-256: " + info.filename)
    return expected

def verify(path, log=print):
    with zipfile.ZipFile(path) as z:
        result = verified_zip(z)
    log(f"Verification OK: {len(result['files'])} file{'' if len(result['files']) == 1 else 's'}; "
        + ("importable" if result["installable"] else "for DOA5Installer only (not for Import)"))
    return result

def game_closed():
    if os.name == "nt":
        result = subprocess.run(["tasklist", "/FI", "IMAGENAME eq game.exe", "/FO", "CSV", "/NH"],
                                capture_output=True, text=True, check=True,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if any(row and row[0].lower() == "game.exe" for row in csv.reader(io.StringIO(result.stdout))):
            raise ValueError("Close DOA5LR before importing or restoring")

def save_state(path, state):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(temp, path)

def install(zip_path, game, layout="auto", log=print):
    game = Path(game).absolute()
    no_link(game)
    if not (game / "game.exe").is_file():
        raise ValueError("game.exe not found in the target folder")
    game_closed()
    if layout == "auto":
        modern = any((game / "maps" / name).is_dir() for name in ("CodexCrimson","CodexDangerZone","PS4Stages"))
        legacy = any((game / name).is_dir() for name in ("CodexCrimson","CodexDangerZone","PS4Stages"))
        if modern and legacy:
            raise ValueError("Stage folders found both in maps and in the game folder: remove one set, "
                             "or use the command line with --layout modern or legacy.")
        layout = "legacy" if legacy else "modern"
    if layout not in ("legacy", "modern"):
        raise ValueError("Unknown layout")
    lock = game / ".doa5lr-ps4-import.lock"
    with lock.open("x") as f:
        f.write(str(os.getpid()))
    try:
        with zipfile.ZipFile(zip_path) as z:
            info = verified_zip(z)
            if not info["installable"]:
                raise ValueError("This ZIP is for DOA5Installer only (PS4 costumes card): Import refused")
            backup = under(game, "DOA5LR-PS4-Backups/" + uuid.uuid4().hex)
            backup.mkdir(parents=True)
            state_path = backup / "state.json"
            state = {"format": 1, "game": str(game), "status": "preparing", "files": []}
            for spec in info["files"]:
                relative = spec["path"][5:] if layout == "legacy" and spec["path"].startswith("maps/") else spec["path"]
                target = under(game, relative)
                before = file_hash(target) if target.is_file() else None
                if target.exists() and not target.is_file():
                    raise ValueError("The target is not a file")
                old = under(backup, "originals/" + relative)
                if before:
                    old.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(target, old)
                    if file_hash(old) != before:
                        raise ValueError("Backup differs")
                new = under(backup, "prepared/" + relative)
                new.parent.mkdir(parents=True, exist_ok=True)
                with z.open(spec["path"]) as inp, new.open("xb") as out:
                    shutil.copyfileobj(inp, out)
                if file_hash(new) != spec["sha256"]:
                    raise ValueError("Temporary copy damaged")
                state["files"].append({"path": relative, "source_path": spec["path"], "before": before,
                                       "after": spec["sha256"], "written": False})
            state["status"] = "installing"
            save_state(state_path, state)
            try:
                for row in state["files"]:
                    game_closed()
                    target = under(game, row["path"])
                    actual = file_hash(target) if target.is_file() else None
                    if actual != row["before"]:
                        raise ValueError("File changed during import: " + row["path"])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    row["written"] = True
                    save_state(state_path, state)
                    os.replace(under(backup, "prepared/" + row["path"]), target)
                state["status"] = "installed"
                save_state(state_path, state)
            except Exception:
                restore_state(state_path, allow_incomplete=True)
                raise
        log("Import done (" + layout + "). Backup: " + str(state_path))
    finally:
        lock.unlink()

def restore_state(state_path, allow_incomplete=False):
    state_path = Path(state_path).absolute()
    no_link(state_path)
    state = load(state_path)
    game = Path(state["game"]).absolute()
    backup = state_path.parent
    if backup.parent != game / "DOA5LR-PS4-Backups" or state_path.name != "state.json":
        raise ValueError("Journal outside the backup folder")
    if state["status"] not in (("installed", "installing", "restoring") if allow_incomplete else ("installed", "installing", "restoring")):
        raise ValueError("Backup cannot be restored")
    trusted = {o["path"]: o["sha256"] for j in read_recipe()["jobs"] if j["installable"] for o in j["outputs"]}
    paths = [r["path"] for r in state["files"]]
    if len(paths) != len(set(paths)):
        raise ValueError("Duplicate journal entries")
    for r in state["files"]:
        original = r.get("source_path", r["path"])
        allowed_paths = {original}
        if original.startswith("maps/"):
            allowed_paths.add(original[5:])
        if r["path"] not in allowed_paths or trusted.get(original) != r["after"]:
            raise ValueError("Unknown journal")
        target = under(game, r["path"])
        actual = file_hash(target) if target.is_file() else None
        if actual not in (r["before"], r["after"]):
            raise ValueError("File changed since the import: " + r["path"])
        if r["before"] and file_hash(under(backup, "originals/" + r["path"])) != r["before"]:
            raise ValueError("Backup damaged")
    game_closed()
    state["status"] = "restoring"
    save_state(state_path, state)
    for r in reversed(state["files"]):
        if not r["written"]:
            continue
        target = under(game, r["path"])
        game_closed()
        actual = file_hash(target) if target.is_file() else None
        if actual not in (r["before"], r["after"]):
            raise ValueError("File changed during restore: " + r["path"])
        if r["before"]:
            tmp = under(backup, "restore.tmp")
            shutil.copyfile(under(backup, "originals/" + r["path"]), tmp)
            os.replace(tmp, target)
        elif target.exists():
            target.unlink()
    state["status"] = "restored"
    save_state(state_path, state)

def restore(state_path):
    state = load(state_path)
    game = Path(state["game"]).absolute()
    no_link(game)
    lock = game / ".doa5lr-ps4-import.lock"
    with lock.open("x") as f:
        f.write(str(os.getpid()))
    try:
        restore_state(state_path)
    finally:
        lock.unlink()

def error_text(exc):
    if isinstance(exc, FileNotFoundError) and exc.filename:
        return "File not found: " + str(exc.filename)
    if isinstance(exc, zipfile.BadZipFile):
        return "Not a valid ZIP file (damaged or incomplete)"
    return str(exc)


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    source_help = "A source folder: PS4 base game, PS4 update 1.19, PS4 DLC or PC game folder (repeat for each one)"
    texconv_help = "Path to texconv.exe (default: the bundled one)"
    key_help = "Your own key file"
    sub.add_parser("list", help="List the content groups")
    b = sub.add_parser("build", help="Make one ZIP from the chosen groups")
    b.add_argument("--source", action="append", required=True, help=source_help)
    b.add_argument("--groups", nargs="+", required=True, help="Groups to include (see the list command), or all")
    b.add_argument("--output", required=True, help="ZIP file to create")
    b.add_argument("--texconv", help=texconv_help)
    b.add_argument("--key-file", help=key_help)
    b.add_argument("--format", choices=("extractor", "installer"), default="extractor",
                   help="installer: stage ZIP for the PS4 content card of DOA5Installer")
    s = sub.add_parser("build-set", help="Make the stage, costume and particle ZIPs for DOA5Installer in one folder")
    s.add_argument("--source", action="append", required=True, help=source_help)
    s.add_argument("--groups", nargs="+", default=["all"], help="Groups to include (see the list command); default: all")
    s.add_argument("--folder", required=True, help="Folder for the ZIPs")
    s.add_argument("--texconv", help=texconv_help)
    s.add_argument("--key-file", help=key_help)
    s.add_argument("--replace", action="store_true", help="Replace the ZIPs already in the folder")
    x = sub.add_parser("export-installer", help="Repack a verified stage ZIP for DOA5Installer")
    x.add_argument("zip", help="Stage ZIP made in the extractor format")
    x.add_argument("--output", required=True, help="ZIP file to create")
    v = sub.add_parser("verify", help="Check a ZIP made by this extractor")
    v.add_argument("zip", help="ZIP file to check")
    i = sub.add_parser("import", help="Install a stage or particle ZIP without DOA5Installer, with a backup")
    i.add_argument("zip", help="ZIP file to import")
    i.add_argument("--game", required=True, help="Folder that contains game.exe")
    i.add_argument("--layout", choices=("auto","modern","legacy"), default="auto",
                   help="modern: stage folders in the maps folder; legacy: in the game folder; auto (default): detect")
    r = sub.add_parser("restore", help="Undo an import from its backup")
    r.add_argument("state", help="state.json of the import backup")
    args = parser.parse_args()
    if args.command == "list":
        recipe = read_recipe()
        for group in sorted({j["group"] for j in recipe["jobs"]}):
            jobs = choose(recipe, [group])
            count = sum(len(j["outputs"]) for j in jobs if not j.get("intermediate"))
            print(shown(group), count, "file" if count == 1 else "files",
                  "(DOA5Installer only)" if not all(j["installable"] for j in jobs) else "(importable)")
    elif args.command == "build":
        build(args.source, args.groups, args.output, args.texconv, pack_format=args.format, key_file=args.key_file)
    elif args.command == "build-set":
        build_set(args.source, args.groups, args.folder, args.texconv, key_file=args.key_file, replace=args.replace)
    elif args.command == "export-installer":
        export_installer(args.zip, args.output)
    elif args.command == "verify":
        verify(args.zip)
    elif args.command == "import":
        install(args.zip, args.game, args.layout)
    else:
        restore(args.state)
        print("Restore done")

if __name__ == "__main__":
    try:
        cli()
    except Exception as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        sys.exit(1)
